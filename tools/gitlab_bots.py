#!/usr/bin/env python3
"""Create and maintain one GitLab bot identity per (profile, project) using YOUR personal token.

    export GITLAB_PAT=...                 # your PAT, scope `api`; you need Maintainer on each project
    gitlab_bots.py plan                   # what would change (default; makes no changes)
    gitlab_bots.py apply [--ci-vars] [--protect] [--labels] [--rotate]
    gitlab_bots.py status                 # tokens, expiry, main-branch protection

Config (default ~/.config/agent-platform/bots.yaml, example in ansible/bots.example.yaml):
    gitlab_url: https://gitlab.example.internal
    expires_days: 90
    profiles:
      firmware: [group/firmware]
      pipeline: [group/pipeline, group/ci-templates]

For each (profile, project) `apply` makes a project access token named `agent-<profile>`
with the role and scopes from profiles/<p>/profile.yaml. GitLab creates a bot user for it,
so everything done with it shows as the bot, not you. Token values go to
~/.config/agent-platform/bot-tokens.json (mode 0600) and are never printed.
  --ci-vars   also store it as a masked, protected CI/CD variable (profile.yaml token_env)
  --protect   protect `main` (push: no one, merge: Maintainers) if it isn't protected yet
  --labels    create the agent::* and profile::<p> labels
  --rotate    rotate existing tokens even if you already have their values
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from botenv import AP_HOME, tokens_file  # noqa: E402

ACCESS = {"reporter": 20, "developer": 30}
LABELS = {"agent::ready": "#1f75cb", "agent::running": "#e9be74", "agent::review": "#108548",
          "agent::needs-human": "#c91c00", "agent::failed": "#ed9121"}
NO_ONE, MAINTAINER = 0, 40


class API:
    def __init__(self, url: str, pat: str, opener=None):
        self.base = url.rstrip("/") + "/api/v4"
        self.pat = pat
        self.opener = opener or self._urlopen

    @staticmethod
    def _urlopen(method, url, headers, body):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def call(self, method: str, path: str, body: dict | None = None, ok=(200, 201, 204)):
        data = json.dumps(body).encode() if body is not None else None
        status, raw = self.opener(method, self.base + path,
                                  {"PRIVATE-TOKEN": self.pat, "Content-Type": "application/json"}, data)
        if status not in ok:
            raise RuntimeError(f"{method} {path}: HTTP {status} {raw[:200]!r}")
        return status, (json.loads(raw) if raw else None)

    @staticmethod
    def pid(project: str) -> str:
        return urllib.parse.quote(project, safe="")

    def tokens(self, project):
        return self.call("GET", f"/projects/{self.pid(project)}/access_tokens")[1] or []

    def create_token(self, project, name, scopes, level, expires):
        return self.call("POST", f"/projects/{self.pid(project)}/access_tokens",
                         {"name": name, "scopes": scopes, "access_level": level, "expires_at": expires})[1]

    def rotate_token(self, project, token_id, expires):
        return self.call("POST", f"/projects/{self.pid(project)}/access_tokens/{token_id}/rotate",
                         {"expires_at": expires})[1]

    def upsert_variable(self, project, key, value):
        body = {"key": key, "value": value, "masked": True, "protected": True, "environment_scope": "*"}
        status, _ = self.call("PUT", f"/projects/{self.pid(project)}/variables/{key}", body, ok=(200, 404))
        if status == 404:
            self.call("POST", f"/projects/{self.pid(project)}/variables", body)

    def protection(self, project, branch="main"):
        status, data = self.call("GET", f"/projects/{self.pid(project)}/protected_branches/{branch}", ok=(200, 404))
        return None if status == 404 else data

    def protect(self, project, branch="main"):
        self.call("POST", f"/projects/{self.pid(project)}/protected_branches",
                  {"name": branch, "push_access_level": NO_ONE, "merge_access_level": MAINTAINER,
                   "allow_force_push": False})

    def ensure_label(self, project, name, color):
        self.call("POST", f"/projects/{self.pid(project)}/labels", {"name": name, "color": color}, ok=(201, 409))


def load_store() -> dict:
    path = tokens_file()
    return json.loads(path.read_text()) if path.is_file() else {}


def save_store(store: dict) -> None:
    path = tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".bot-tokens.")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(store, fh, indent=2)
    os.replace(tmp, path)


def profile_meta(profile: str) -> dict:
    meta = yaml.safe_load((AP_HOME / "profiles" / profile / "profile.yaml").read_text())
    gl = meta["gitlab"]
    return {"name": f"agent-{profile}", "level": ACCESS[gl["role"]], "scopes": gl["scopes"],
            "token_env": gl["token_env"]}


def active(tokens: list[dict], name: str) -> dict | None:
    live = [t for t in tokens if t.get("name") == name and t.get("active", True) and not t.get("revoked")]
    return max(live, key=lambda t: t.get("id", 0)) if live else None


def run(api: API, cfg: dict, mode: str, *, ci_vars=False, protect=False, labels=False, rotate=False,
        today: dt.date | None = None, out=print) -> int:
    today = today or dt.date.today()
    expires = (today + dt.timedelta(days=int(cfg.get("expires_days", 90)))).isoformat()
    store = load_store()
    problems = 0
    for profile, projects in (cfg.get("profiles") or {}).items():
        meta = profile_meta(profile)
        for project in projects:
            existing = active(api.tokens(project), meta["name"])
            have_value = bool((store.get(profile) or {}).get(project, {}).get("token"))
            if existing is None:
                action = "create"
            elif rotate or not have_value:
                action = "rotate"
            else:
                action = "keep"
            prot = api.protection(project)
            pushers = [a.get("access_level") for a in (prot or {}).get("push_access_levels", [])]
            prot_state = ("unprotected" if prot is None else
                          "push: no one" if pushers == [NO_ONE] else f"push levels {pushers}")
            exp = existing.get("expires_at") if existing else None
            line = f"{profile:9} {project:35} token:{action:6} expires:{exp or '-':10} main:{prot_state}"
            if mode in ("plan", "status"):
                out(line)
                if prot is not None and pushers != [NO_ONE]:
                    out(f"          ! main allows direct pushes; set 'Allowed to push' to No one in {project}")
                    problems += 1
                continue

            if action == "create":
                tok = api.create_token(project, meta["name"], meta["scopes"], meta["level"], expires)
            elif action == "rotate":
                tok = api.rotate_token(project, existing["id"], expires)
            else:
                tok = None
            if tok:
                store.setdefault(profile, {})[project] = {"token": tok["token"], "id": tok.get("id"),
                                                          "expires_at": tok.get("expires_at", expires)}
                save_store(store)
            value = store[profile][project]["token"]
            if ci_vars:
                api.upsert_variable(project, meta["token_env"], value)
            if protect and prot is None:
                api.protect(project)
                prot_state = "push: no one (set)"
            elif prot is not None and pushers != [NO_ONE]:
                out(f"          ! main allows direct pushes in {project}; fix it in Settings > Repository")
                problems += 1
            if labels:
                for name, color in {**LABELS, f"profile::{profile}": "#6699cc"}.items():
                    api.ensure_label(project, name, color)
            out(f"{profile:9} {project:35} token:{action}d{' +ci-var' if ci_vars else ''}"
                f"{' +labels' if labels else ''} main:{prot_state}".replace("keepd", "kept"))
    if mode == "plan":
        out("plan only; run `apply` to make changes")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", choices=["plan", "apply", "status"], default="plan")
    ap.add_argument("-c", "--config", type=Path,
                    default=Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                    / "agent-platform" / "bots.yaml")
    ap.add_argument("--ci-vars", action="store_true")
    ap.add_argument("--protect", action="store_true")
    ap.add_argument("--labels", action="store_true")
    ap.add_argument("--rotate", action="store_true")
    args = ap.parse_args(argv)
    pat = os.environ.get("GITLAB_PAT", "")
    if not pat:
        print("gitlab_bots: export GITLAB_PAT (your personal token with `api` scope)", file=sys.stderr)
        return 2
    if not args.config.is_file():
        print(f"gitlab_bots: no config at {args.config} (see ansible/bots.example.yaml)", file=sys.stderr)
        return 2
    cfg = yaml.safe_load(args.config.read_text()) or {}
    return run(API(cfg["gitlab_url"], pat), cfg, args.mode, ci_vars=args.ci_vars, protect=args.protect,
               labels=args.labels, rotate=args.rotate)


if __name__ == "__main__":
    sys.exit(main())
