#!/usr/bin/env python3
"""Create and maintain one GitLab bot identity per (profile, project) using YOUR personal token.

    export GITLAB_PAT=...                 # your PAT, scope `api`; you need Maintainer on each project
    gitlab_bots.py plan                   # what would change (default; makes no changes)
    gitlab_bots.py apply [--ci-vars] [--protect] [--labels] [--rotate] [--branches]
    gitlab_bots.py status                 # tokens, expiry, protection, audit of protected_paths
    gitlab_bots.py export --profile P --out FILE    # no PAT or network; see "Handoff" below

Config (default ~/.config/agent-platform/bots.yaml, example in ansible/bots.example.yaml):
    gitlab_url: https://gitlab.example.internal
    expires_days: 90
    profiles:
      firmware: [group/firmware]
      pipeline: [group/pipeline, group/ci-templates]
    group_profiles:                # one group access token per profile; needs Owner on the group
      it-debug: [group/software]
    protected_paths: [lab/lab-limits]   # no agent token may reach these
    branch_lockdown:               # see "Branch lockdown" below
      human_group: group/lab-humans
      human_branches: "*/*"
      agent_branches: "agent/*"
      projects: [group/firmware]

For each (profile, project) `apply` makes a project access token named `agent-<profile>`
with the role and scopes from profiles/<p>/profile.yaml. GitLab creates a bot user for it,
so everything done with it shows as the bot, not you. Token values go to
~/.config/agent-platform/bot-tokens.json (mode 0600) and are never printed.
  --ci-vars   also store it as a masked, protected CI/CD variable (profile.yaml token_env)
  --protect   protect `main` (push: no one, merge: Maintainers) if it isn't protected yet
  --labels    create the agent::* and profile::<p> labels (projects only, not groups)
  --rotate    rotate existing tokens even if you already have their values
  --branches  apply branch_lockdown

Group tokens reach every project in the group and its subgroups, so a config that would
give any token a path under protected_paths is refused before anything is called. Every
mode also lists the members of each protected path and flags any bot, which catches access
the path check can't see, such as a project shared into a group.

Branch lockdown: GitLab can't limit a token to chosen branches, and a Developer can push to
any unprotected branch. So humans get a wildcard rule that excludes the bot, and agent
branches get a rule that includes it. GitLab applies the MOST PERMISSIVE matching rule, so
human_branches must be chosen with care, and there is no default:
  "*/*"  `main` keeps "push: no one", but a bot can still push to names with no slash
  "*"    a bot is limited to agent branches, but humans can push straight to `main`
Rules that already exist are reported, never changed. Only direct members of human_group
count, and the project must be shared with the group directly, which `--branches` does.
Whether a group token's bot can be listed on a rule is undocumented, so test on a scratch
project.

Handoff: tokens land in YOUR home folder. `export` writes only the named profiles to a new
0600 file. Move it to the agent user's ~/.config/agent-platform/bot-tokens.json, or mount it
in a container and point AP_BOT_TOKENS at it. Never run this script as the agent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
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
NO_ONE, DEVELOPER, MAINTAINER = 0, 30, 40
BOT_USER = re.compile(r"^(project|group)_\d+_bot")


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

    def tokens(self, path, kind="projects"):
        return self.call("GET", f"/{kind}/{self.pid(path)}/access_tokens")[1] or []

    def create_token(self, path, name, scopes, level, expires, kind="projects"):
        return self.call("POST", f"/{kind}/{self.pid(path)}/access_tokens",
                         {"name": name, "scopes": scopes, "access_level": level, "expires_at": expires})[1]

    def rotate_token(self, path, token_id, expires, kind="projects"):
        return self.call("POST", f"/{kind}/{self.pid(path)}/access_tokens/{token_id}/rotate",
                         {"expires_at": expires})[1]

    def upsert_variable(self, path, key, value, kind="projects"):
        body = {"key": key, "value": value, "masked": True, "protected": True}
        if kind == "projects":
            body["environment_scope"] = "*"
        status, _ = self.call("PUT", f"/{kind}/{self.pid(path)}/variables/{key}", body, ok=(200, 404))
        if status == 404:
            self.call("POST", f"/{kind}/{self.pid(path)}/variables", body)

    def group_id(self, group):
        return self.call("GET", f"/groups/{self.pid(group)}")[1]["id"]

    def shared_group_ids(self, project):
        data = self.call("GET", f"/projects/{self.pid(project)}")[1] or {}
        return {g.get("group_id") for g in data.get("shared_with_groups") or []}

    def share(self, project, group_id):
        self.call("POST", f"/projects/{self.pid(project)}/share",
                  {"group_id": group_id, "group_access": DEVELOPER})

    def branch_rules(self, project):
        rules = self.call("GET", f"/projects/{self.pid(project)}/protected_branches?per_page=100")[1] or []
        return {r["name"]: r for r in rules}

    def add_branch_rule(self, project, name, pushers, mergers):
        self.call("POST", f"/projects/{self.pid(project)}/protected_branches",
                  {"name": name, "push_access_level": MAINTAINER, "merge_access_level": MAINTAINER,
                   "allowed_to_push": pushers, "allowed_to_merge": mergers, "allow_force_push": False})

    def members(self, project):
        """Everyone with access, including inherited and invited members. None if not visible."""
        found, page = [], 1
        while True:
            status, data = self.call("GET", f"/projects/{self.pid(project)}/members/all"
                                            f"?per_page=100&page={page}", ok=(200, 403, 404))
            if status != 200:
                return None
            found += data or []
            if len(data or []) < 100:
                return found
            page += 1

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


def write_private(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".bot-tokens.")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(store, fh, indent=2)
    os.replace(tmp, path)


def save_store(store: dict) -> None:
    write_private(tokens_file(), store)


def under(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def forbidden_targets(cfg: dict) -> list[str]:
    """Token targets that would reach a protected path. Checked before any API call."""
    errors = []
    for guarded in cfg.get("protected_paths") or []:
        for profile, projects in (cfg.get("profiles") or {}).items():
            errors += [f"profile {profile}: project {p} is inside protected path {guarded}"
                       for p in projects if under(p, guarded)]
        for profile, groups in (cfg.get("group_profiles") or {}).items():
            errors += [f"profile {profile}: group token on {g} would reach protected path {guarded}"
                       for g in groups if under(guarded, g) or under(g, guarded)]
    return errors


def audit_protected(api: API, cfg: dict, out) -> int:
    problems = 0
    for guarded in cfg.get("protected_paths") or []:
        members = api.members(guarded)
        if members is None:
            out(f"          ! can't list members of protected path {guarded}; check it by hand")
            problems += 1
            continue
        bots = sorted(m.get("username", "") for m in members
                      if m.get("bot") or BOT_USER.match(m.get("username", "")))
        for bot in bots:
            out(f"          ! bot {bot} has access to protected path {guarded}")
        problems += len(bots)
        if not bots:
            out(f"protected {guarded}: no bots among {len(members)} members")
    return problems


def lockdown(api: API, cfg: dict, bots: dict, apply: bool, out) -> int:
    """Report or apply branch_lockdown. Existing rules are never modified."""
    conf = cfg.get("branch_lockdown")
    if not conf:
        return 0
    if not conf.get("human_branches"):
        out("          ! branch_lockdown needs human_branches; see the notes in gitlab_bots.py")
        return 1
    pattern, agent = conf["human_branches"], conf.get("agent_branches", "agent/*")
    if pattern == "*":
        out("          ! human_branches '*' lets humans push straight to main (most permissive rule wins)")
    humans = {"group_id": api.group_id(conf["human_group"])}
    problems = 0
    for project in conf.get("projects") or []:
        worker_bots = [{"user_id": uid} for uid in sorted(bots.get(project, ()))]
        if not worker_bots:
            out(f"branches  {project:35} no worker bot id yet, so {agent} has no bot on it")
        shared = humans["group_id"] in api.shared_group_ids(project)
        if not shared and apply:
            api.share(project, humans["group_id"])
        elif not shared:
            out(f"branches  {project:35} would share with {conf['human_group']}")
        rules = api.branch_rules(project)
        for name, pushers in ((pattern, [humans]), (agent, [humans, *worker_bots])):
            rule = rules.get(name)
            if rule is None and apply:
                api.add_branch_rule(project, name, pushers, [humans])
                out(f"branches  {project:35} added {name}")
            elif rule is None:
                out(f"branches  {project:35} would add {name}")
            else:
                have = [{k: a[k] for k in ("group_id", "user_id") if a.get(k)}
                        for a in rule.get("push_access_levels", [])]
                missing = [p for p in pushers if p not in have]
                if missing:
                    out(f"          ! rule {name} in {project} lacks {missing}; fix it in Settings > Repository")
                    problems += 1
                else:
                    out(f"branches  {project:35} {name} ok")
    return problems


def export_tokens(profiles: list[str], dest: Path, out=print) -> int:
    if dest.resolve() == tokens_file().resolve():
        out("gitlab_bots: --out must not be your own token store")
        return 2
    store = load_store()
    missing = [p for p in profiles if p not in store]
    if missing:
        out(f"gitlab_bots: no tokens stored for {', '.join(missing)}; run apply first")
        return 2
    write_private(dest, {p: store[p] for p in profiles})
    out(f"wrote {sum(len(store[p]) for p in profiles)} token(s) for {', '.join(profiles)} to {dest} (0600)")
    return 0


def profile_meta(profile: str) -> dict:
    meta = yaml.safe_load((AP_HOME / "profiles" / profile / "profile.yaml").read_text())
    gl = meta["gitlab"]
    return {"name": f"agent-{profile}", "level": ACCESS[gl["role"]], "scopes": gl["scopes"],
            "token_env": gl["token_env"]}


def active(tokens: list[dict], name: str) -> dict | None:
    live = [t for t in tokens if t.get("name") == name and t.get("active", True) and not t.get("revoked")]
    return max(live, key=lambda t: t.get("id", 0)) if live else None


def run(api: API, cfg: dict, mode: str, *, ci_vars=False, protect=False, labels=False, rotate=False,
        branches=False, today: dt.date | None = None, out=print) -> int:
    refused = forbidden_targets(cfg)
    if refused:
        for line in refused:
            out(f"refused: {line}")
        return 2
    today = today or dt.date.today()
    expires = (today + dt.timedelta(days=int(cfg.get("expires_days", 90)))).isoformat()
    store = load_store()
    problems = 0
    bots: dict[str, set] = {}
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
            if existing and existing.get("user_id") and meta["level"] >= DEVELOPER:
                bots.setdefault(project, set()).add(existing["user_id"])
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
                if tok.get("user_id") and meta["level"] >= DEVELOPER:
                    bots.setdefault(project, set()).add(tok["user_id"])
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

    locked = (cfg.get("branch_lockdown") or {}).get("projects") or []
    for profile, groups in (cfg.get("group_profiles") or {}).items():
        meta = profile_meta(profile)
        for group in groups:
            existing = active(api.tokens(group, "groups"), meta["name"])
            have_value = bool((store.get(profile) or {}).get(group, {}).get("token"))
            action = "create" if existing is None else "rotate" if rotate or not have_value else "keep"
            uid = existing.get("user_id") if existing else None
            if mode == "apply" and action != "keep":
                tok = (api.create_token(group, meta["name"], meta["scopes"], meta["level"], expires, "groups")
                       if action == "create" else api.rotate_token(group, existing["id"], expires, "groups"))
                uid = tok.get("user_id") or uid
                store.setdefault(profile, {})[group] = {"token": tok["token"], "id": tok.get("id"), "kind": "group",
                                                        "expires_at": tok.get("expires_at", expires)}
                save_store(store)
            if mode == "apply" and ci_vars:
                api.upsert_variable(group, meta["token_env"], store[profile][group]["token"], "groups")
            if uid and meta["level"] >= DEVELOPER:
                for project in locked:
                    if under(project, group):
                        bots.setdefault(project, set()).add(uid)
            exp = existing.get("expires_at") if existing else None
            out(f"{profile:9} {group + ' (group)':35} token:{action:6} expires:{exp or '-':10}")

    problems += lockdown(api, cfg, bots, mode == "apply" and branches, out)
    problems += audit_protected(api, cfg, out)
    if mode == "plan":
        out("plan only; run `apply` to make changes")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", choices=["plan", "apply", "status", "export"], default="plan")
    ap.add_argument("-c", "--config", type=Path,
                    default=Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                    / "agent-platform" / "bots.yaml")
    ap.add_argument("--ci-vars", action="store_true")
    ap.add_argument("--protect", action="store_true")
    ap.add_argument("--labels", action="store_true")
    ap.add_argument("--rotate", action="store_true")
    ap.add_argument("--branches", action="store_true", help="apply branch_lockdown from the config")
    ap.add_argument("--profile", action="append", default=[], help="export: profile to include (repeatable)")
    ap.add_argument("--out", type=Path, help="export: file to write")
    args = ap.parse_args(argv)
    if args.mode == "export":
        if not args.profile or not args.out:
            print("gitlab_bots: export needs --profile and --out", file=sys.stderr)
            return 2
        return export_tokens(args.profile, args.out)
    pat = os.environ.get("GITLAB_PAT", "")
    if not pat:
        print("gitlab_bots: export GITLAB_PAT (your personal token with `api` scope)", file=sys.stderr)
        return 2
    if not args.config.is_file():
        print(f"gitlab_bots: no config at {args.config} (see ansible/bots.example.yaml)", file=sys.stderr)
        return 2
    cfg = yaml.safe_load(args.config.read_text()) or {}
    return run(API(cfg["gitlab_url"], pat), cfg, args.mode, ci_vars=args.ci_vars, protect=args.protect,
               labels=args.labels, rotate=args.rotate, branches=args.branches)


if __name__ == "__main__":
    sys.exit(main())
