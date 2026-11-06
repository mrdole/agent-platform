#!/usr/bin/env python3
"""Print shell exports that make one agent session act as the profile's GitLab bot.

    eval "$(botenv.py <profile> [--dir PATH])"      # the `agent` launcher does this

Token lookup, first match wins:
  1. $<token_env> from profile.yaml (e.g. GITLAB_TOKEN_FIRMWARE) - CI sets this per project
  2. ~/.config/agent-platform/bot-tokens.json [profile][project path, or a parent group's path]
     - written by gitlab_bots.py. $AP_BOT_TOKENS overrides the file path, e.g. in a container.

With a token, for this session only:
  * glab uses it (GITLAB_TOKEN), so issues/MRs/comments are by the bot
  * git pushes to the GitLab host go over HTTPS with the token, even if origin is an SSH
    remote (pushInsteadOf + a session credential helper). Fetches still use your normal access.
  * commits are authored "agent-<profile> (via <your git name>)"
Without a token it warns on stderr (pushes would be yours) and exits 3 if AP_REQUIRE_BOT=1.
Nothing is written to any git config file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

AP_HOME = Path(os.environ.get("AP_HOME") or Path(__file__).resolve().parent.parent)
CRED_HELPER = '!f() { test "$1" = get || exit 0; echo username=oauth2; echo "password=$AP_BOT_TOKEN"; }; f'
SSH_SCP = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*?)(?:\.git)?/?$")
URL = re.compile(r"^(?:ssh|https?|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+?)(?:\.git)?/?$")


def tokens_file() -> Path:
    if os.environ.get("AP_BOT_TOKENS"):
        return Path(os.environ["AP_BOT_TOKENS"])
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "agent-platform" / "bot-tokens.json"


def parse_remote(url: str) -> tuple[str, str] | None:
    """git@host:group/proj.git | ssh://git@host:22/group/proj.git | https://host/group/proj.git"""
    url = url.strip()
    m = URL.match(url) or SSH_SCP.match(url)
    return (m.group("host"), m.group("path")) if m else None


def _git(directory: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def lookup_token(profile: str, token_env: str, project: str | None) -> tuple[str, str]:
    if os.environ.get(token_env):
        return os.environ[token_env], f"${token_env}"
    path = tokens_file()
    if project and path.is_file():
        data = json.loads(path.read_text())
        entries = data.get(profile) or {}
        tok = entries.get(project, {})
        if tok.get("token"):
            return tok["token"], f"{path} [{profile}][{project}]"
        # A group token covers the group's subgroups too. The most specific group wins.
        groups = [g for g, e in entries.items() if e.get("kind") == "group" and project.startswith(g + "/")]
        if groups:
            group = max(groups, key=len)
            return entries[group]["token"], f"{path} [{profile}][{group}] (group token)"
    return "", ""


def build_env(profile: str, directory: Path, environ: dict | None = None) -> tuple[dict[str, str], list[str]]:
    environ = os.environ if environ is None else environ
    prof = yaml.safe_load((AP_HOME / "profiles" / profile / "profile.yaml").read_text())
    if prof["gitlab"].get("bot_identity") is False:
        return {}, [f"botenv: profile '{profile}' works as you (bot_identity: false)"]
    token_env = prof["gitlab"]["token_env"]
    remote = parse_remote(_git(directory, "remote", "get-url", "origin"))
    host, project = remote if remote else (environ.get("GITLAB_HOST", ""), None)
    token, source = lookup_token(profile, token_env, project)
    notes = []
    if not token:
        where = f"project {project}" if project else "this directory (no GitLab origin found)"
        notes.append(f"botenv: no bot token for profile '{profile}' in {where}; "
                     f"pushes and glab actions would use YOUR identity. Run tools/gitlab_bots.py apply.")
        return {}, notes

    owner = _git(directory, "config", "user.name") or environ.get("USER", "user")
    name = f"agent-{profile} (via {owner})"
    email = f"agent-{profile}@noreply.{host or 'invalid'}"
    env = {
        "AP_BOT_TOKEN": token, "GITLAB_TOKEN": token, "AP_BOT_PROFILE": profile,
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
    }
    if project:
        env["AP_BOT_PROJECT"] = project
    if host:
        env["GITLAB_HOST"] = host
        start = int(environ.get("GIT_CONFIG_COUNT", "0") or 0)
        pairs = [
            (f"credential.https://{host}.helper", ""),           # drop other helpers for this host
            (f"credential.https://{host}.helper", CRED_HELPER),  # ...and use the bot token
            (f"url.https://{host}/.pushInsteadOf", f"git@{host}:"),
            (f"url.https://{host}/.pushInsteadOf", f"ssh://git@{host}/"),
        ]
        for i, (k, v) in enumerate(pairs, start=start):
            env[f"GIT_CONFIG_KEY_{i}"] = k
            env[f"GIT_CONFIG_VALUE_{i}"] = v
        env["GIT_CONFIG_COUNT"] = str(start + len(pairs))
    notes.append(f"botenv: acting as bot for '{profile}'" + (f" in {project}" if project else "") + f" (token from {source})")
    return env, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--dir", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)
    env, notes = build_env(args.profile, args.dir)
    for n in notes:
        print(n, file=sys.stderr)
    works_as_user = any("bot_identity: false" in n for n in notes)
    if not env and not works_as_user and os.environ.get("AP_REQUIRE_BOT") == "1":
        return 3
    for k, v in env.items():
        print(f"export {k}={shlex.quote(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
