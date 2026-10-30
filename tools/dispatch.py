#!/usr/bin/env python3
"""GitLab issue -> headless agent run -> draft MR. Runs in a scheduled CI job.

    agent-dispatch --profile firmware [--agent lead] [--dry-run] [--no-strict]

Picks the oldest open issue labelled `agent::ready` and `profile::<profile>`, runs the
profile's agent headless on it, commits the working-tree changes to
`agent/<profile>/<iid>-<slug>`, pushes with the profile's own token, and opens a draft MR.

Issue labels track state:
    agent::ready -> agent::running -> agent::review | agent::needs-human | agent::failed

The agent never commits or pushes. This script does, so git actions stay deterministic.

Env (set by GitLab CI): CI_SERVER_URL, CI_PROJECT_ID, CI_PROJECT_PATH, CI_PROJECT_DIR,
CI_PIPELINE_URL; plus the profile's token variable (profile.yaml gitlab.token_env).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

AP_HOME = Path(os.environ.get("AP_HOME") or Path(__file__).resolve().parent.parent)
READY, RUNNING, REVIEW, NEEDS_HUMAN, FAILED = (
    "agent::ready", "agent::running", "agent::review", "agent::needs-human", "agent::failed")
OVERRIDE_FILES = ("opencode.json", "opencode.jsonc", ".opencode")
RUN_DIR = ".agent-run"
OUTPUT_TAIL = 6000


class GitLabAPI:
    def __init__(self, server_url: str, project_id: str, token: str):
        self.base = f"{server_url.rstrip('/')}/api/v4/projects/{urllib.parse.quote(str(project_id), safe='')}"
        self.token = token

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitLab {method} {path}: {exc.code} {exc.read()[:300]!r}") from None

    def ready_issues(self, profile: str) -> list[dict]:
        return self._call("GET", "/issues", {
            "state": "opened", "labels": f"{READY},profile::{profile}",
            "order_by": "created_at", "sort": "asc", "per_page": 5})

    def default_branch(self) -> str:
        return self._call("GET", "")["default_branch"]

    def update_labels(self, iid: int, add: list[str], remove: list[str]) -> None:
        self._call("PUT", f"/issues/{iid}", body={"add_labels": ",".join(add), "remove_labels": ",".join(remove)})

    def note(self, iid: int, text: str) -> None:
        self._call("POST", f"/issues/{iid}/notes", body={"body": text})

    def create_mr(self, source: str, target: str, title: str, description: str, labels: list[str]) -> dict:
        return self._call("POST", "/merge_requests", body={
            "source_branch": source, "target_branch": target, "title": title,
            "description": description, "labels": ",".join(labels), "remove_source_branch": True})


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "task"


def build_prompt(issue: dict) -> str:
    return (
        f"You are working on GitLab issue #{issue['iid']}: {issue['title']}\n\n"
        f"{(issue.get('description') or '').strip()}\n\n"
        "Rules for this run:\n"
        "- Work only toward the acceptance criteria in the issue. If there are none, make no changes "
        "and explain exactly what is missing.\n"
        "- Run the project's checks (see AGENTS.md) and report the exact commands and results.\n"
        "- Do not commit or push. The dispatcher commits your working-tree changes and opens the MR.\n"
        "- End with sections '## Evidence' (commands run and results) and '## Not verified'.\n"
    )


def tail(text: str, n: int = OUTPUT_TAIL) -> str:
    return text if len(text) <= n else "...(truncated)...\n" + text[-n:]


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def git(repo: Path, *args: str, check: bool = True, secret: str = "") -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        cmd = redact(" ".join(args), secret)
        raise RuntimeError(f"git {cmd} failed: {redact(proc.stderr.strip(), secret)}")
    return proc.stdout


def has_project_override(repo: Path) -> bool:
    return any((repo / name).exists() for name in OVERRIDE_FILES)


def run_agent(agent_cmd: str, profile: str, agent: str, prompt: str, repo: Path, iid: int, timeout: int):
    cmd = [agent_cmd, "--headless", profile, "run", "--agent", agent, "--auto", "--title", f"#{iid}", prompt]
    try:
        proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, out + f"\n[agent timed out after {timeout}s]"
    output = proc.stdout + (f"\n[stderr]\n{proc.stderr}" if proc.returncode != 0 else "")
    return proc.returncode, output


def dispatch(api, repo: Path, profile: str, agent: str, push_url: str, *, agent_cmd: str = "agent",
             strict: bool = True, timeout: int = 3600, pipeline_url: str = "", secret: str = "",
             dry_run: bool = False) -> str:
    issues = api.ready_issues(profile)
    if not issues:
        print(f"dispatch: no issues labelled {READY} + profile::{profile}")
        return "idle"
    issue = issues[0]
    iid = int(issue["iid"])
    print(f"dispatch: issue #{iid}: {issue['title']}")
    if dry_run:
        return "dry-run"

    if strict and has_project_override(repo):
        api.update_labels(iid, add=[NEEDS_HUMAN], remove=[READY])
        api.note(iid, "Agent run refused: this repo has its own OpenCode config "
                      f"({', '.join(n for n in OVERRIDE_FILES if (repo / n).exists())}), which would override "
                      f"the `{profile}` profile's permissions. Remove it or move it into the platform profile.")
        return "needs-human"

    api.update_labels(iid, add=[RUNNING], remove=[READY])
    run_dir = repo / RUN_DIR
    run_dir.mkdir(exist_ok=True)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(errors="ignore") if exclude.exists() else ""
    if f"{RUN_DIR}/" not in existing:
        with exclude.open("a") as fh:
            fh.write(f"\n{RUN_DIR}/\n")

    try:
        target = api.default_branch()
        branch = f"agent/{profile}/{iid}-{slugify(issue['title'])}"
        if git(repo, "ls-remote", "--heads", push_url, branch, secret=secret).strip():
            branch += f"-r{os.environ.get('CI_PIPELINE_ID', os.getpid())}"
        git(repo, "checkout", "-B", branch)

        rc, output = run_agent(agent_cmd, profile, agent, build_prompt(issue), repo, iid, timeout)
        (run_dir / "agent-output.md").write_text(output, encoding="utf-8")

        if rc != 0:
            api.update_labels(iid, add=[FAILED], remove=[RUNNING])
            api.note(iid, f"Agent run failed (exit {rc}). {pipeline_url}\n\n```\n{tail(output, 3000)}\n```")
            return "failed"

        if not git(repo, "status", "--porcelain").strip():
            api.update_labels(iid, add=[NEEDS_HUMAN], remove=[RUNNING])
            api.note(iid, f"Agent made no changes. Its report:\n\n{tail(output)}")
            return "needs-human"

        git(repo, "add", "-A")
        msg = f"{issue['title'][:72]}\n\nAgent-Profile: {profile}\nRefs: #{iid}\n"
        git(repo, "-c", f"user.name=agent-{profile}", "-c", f"user.email=agent-{profile}@noreply.invalid",
            "commit", "-q", "-m", msg)
        git(repo, "push", "-q", push_url, f"HEAD:refs/heads/{branch}", secret=secret)

        description = (
            f"Implements #{iid}.\n\n"
            f"- Profile: `{profile}`, agent: `{agent}`\n"
            + (f"- Pipeline: {pipeline_url}\n" if pipeline_url else "")
            + "\nReview the diff and the evidence below before marking ready. "
              "The agent's claims are not verified by this bot.\n\n"
              f"## Agent report\n\n{tail(output)}\n"
        )
        mr = api.create_mr(branch, target, f"Draft: {issue['title']}", description,
                           [REVIEW, f"profile::{profile}"])
        api.update_labels(iid, add=[REVIEW], remove=[RUNNING])
        api.note(iid, f"Opened draft MR !{mr.get('iid')}: {mr.get('web_url', '')}")
        return "review"
    except Exception as exc:  # report every failure on the issue, then re-raise
        api.update_labels(iid, add=[FAILED], remove=[RUNNING])
        api.note(iid, f"Dispatcher error: {redact(str(exc), secret)}\n{pipeline_url}")
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--agent", help="agent to run (default: profile.yaml default_agent)")
    ap.add_argument("--repo", type=Path, default=Path(os.environ.get("CI_PROJECT_DIR", ".")))
    ap.add_argument("--agent-cmd", default="agent")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--no-strict", action="store_true", help="allow repos that carry their own OpenCode config")
    ap.add_argument("--dry-run", action="store_true", help="only show which issue would run")
    args = ap.parse_args(argv)

    prof_file = AP_HOME / "profiles" / args.profile / "profile.yaml"
    if not prof_file.is_file():
        print(f"dispatch: unknown profile {args.profile}", file=sys.stderr)
        return 2
    prof = yaml.safe_load(prof_file.read_text())
    token_env = prof["gitlab"]["token_env"]
    token = os.environ.get(token_env, "")
    server = os.environ.get("CI_SERVER_URL", "")
    project_id = os.environ.get("CI_PROJECT_ID", "")
    project_path = os.environ.get("CI_PROJECT_PATH", "")
    if not (token and server and project_id and project_path):
        print(f"dispatch: need {token_env}, CI_SERVER_URL, CI_PROJECT_ID, CI_PROJECT_PATH", file=sys.stderr)
        return 2

    parts = urllib.parse.urlsplit(server)
    push_url = f"{parts.scheme}://oauth2:{token}@{parts.netloc}{parts.path.rstrip('/')}/{project_path}.git"
    api = GitLabAPI(server, project_id, token)
    result = dispatch(api, args.repo.resolve(), args.profile, args.agent or prof["default_agent"], push_url,
                      agent_cmd=args.agent_cmd, strict=not args.no_strict, timeout=args.timeout,
                      pipeline_url=os.environ.get("CI_PIPELINE_URL", ""), secret=token, dry_run=args.dry_run)
    print(f"dispatch: {result}")
    return 1 if result == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
