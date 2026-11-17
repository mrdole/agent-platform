import subprocess
from pathlib import Path

import pytest

import dispatch


class FakeAPI:
    def __init__(self, issues):
        self.issues = issues
        self.labels = []
        self.notes = []
        self.mrs = []

    def ready_issues(self, profile):
        return self.issues

    def default_branch(self):
        return "main"

    def update_labels(self, iid, add, remove):
        self.labels.append((iid, tuple(add), tuple(remove)))

    def note(self, iid, text):
        self.notes.append((iid, text))

    def create_mr(self, source, target, title, description, labels):
        self.mrs.append(dict(source=source, target=target, title=title, description=description, labels=labels))
        return {"iid": 5, "web_url": "https://gl/mr/5"}


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    sh(tmp_path, "git", "clone", "-q", str(remote), str(work))
    (work / "README.md").write_text("hi\n")
    sh(work, "git", "add", "-A")
    sh(work, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    sh(work, "git", "push", "-q", "origin", "main")
    return work, remote


def stub_agent(tmp_path: Path, body: str) -> str:
    p = tmp_path / "fake-agent"
    p.write_text("#!/usr/bin/env bash\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


ISSUE = {"iid": 42, "title": "Fix UART baud divisor", "description": "Acceptance:\n1. tests pass"}


def test_happy_path_opens_draft_mr(repo, tmp_path):
    work, remote = repo
    agent = stub_agent(tmp_path, 'echo "$@" > args.txt; echo fixed > uart.c; echo "## Evidence\nmake test: 0"')
    api = FakeAPI([ISSUE])
    result = dispatch.dispatch(api, work, "firmware", "lead", str(remote), agent_cmd=agent)
    assert result == "review"
    branch = "agent/firmware/42-fix-uart-baud-divisor"
    assert subprocess.run(["git", "rev-parse", "--verify", branch], cwd=remote, capture_output=True).returncode == 0
    files = subprocess.run(["git", "show", "--name-only", "--format=", branch], cwd=remote,
                           capture_output=True, text=True).stdout.split()
    assert "uart.c" in files and not any(f.startswith(".agent-run") for f in files)
    args = (work / "args.txt").read_text()
    assert args.startswith("--headless firmware run --standalone --agent lead --auto")
    mr = api.mrs[0]
    assert mr["source"] == branch and mr["target"] == "main" and mr["title"].startswith("Draft: ")
    assert "## Evidence" in mr["description"]
    assert api.labels[0] == (42, ("agent::running",), ("agent::ready",))
    assert api.labels[-1] == (42, ("agent::review",), ("agent::running",))
    msg = subprocess.run(["git", "log", "-1", "--format=%an %B", branch], cwd=remote,
                         capture_output=True, text=True).stdout
    assert msg.startswith("agent-firmware") and "Refs: #42" in msg


def test_no_changes_needs_human(repo, tmp_path):
    work, remote = repo
    api = FakeAPI([ISSUE])
    assert dispatch.dispatch(api, work, "firmware", "lead", str(remote),
                             agent_cmd=stub_agent(tmp_path, "echo 'no acceptance criteria'")) == "needs-human"
    assert api.labels[-1][1] == ("agent::needs-human",) and not api.mrs


def test_agent_failure(repo, tmp_path):
    work, remote = repo
    api = FakeAPI([ISSUE])
    assert dispatch.dispatch(api, work, "firmware", "lead", str(remote),
                             agent_cmd=stub_agent(tmp_path, "echo boom; exit 3")) == "failed"
    assert api.labels[-1][1] == ("agent::failed",) and "exit 3" in api.notes[-1][1]


def test_strict_mode_refuses_project_config(repo, tmp_path):
    work, remote = repo
    (work / "opencode.json").write_text("{}")
    api = FakeAPI([ISSUE])
    assert dispatch.dispatch(api, work, "firmware", "lead", str(remote),
                             agent_cmd=stub_agent(tmp_path, "exit 0")) == "needs-human"
    assert "override" in api.notes[-1][1]


@pytest.mark.parametrize("name", ["opencode.json", "opencode.jsonc", ".opencode", ".claude", ".agents"])
def test_strict_mode_refuses_each_override_path(repo, tmp_path, name):
    work, remote = repo
    (work / name).mkdir() if name.startswith(".") else (work / name).write_text("{}")
    api = FakeAPI([ISSUE])
    assert dispatch.dispatch(api, work, "firmware", "lead", str(remote),
                             agent_cmd=stub_agent(tmp_path, "exit 0")) == "needs-human"
    assert name in api.notes[-1][1]


def test_run_agent_uses_private_server(tmp_path):
    log = tmp_path / "argv"
    agent = stub_agent(tmp_path, f'printf "%s\\n" "$@" > {log}')
    dispatch.run_agent(agent, "pipeline", "lead", "do it", tmp_path, 7, 30)
    argv = log.read_text().split("\n")
    assert argv[:3] == ["--headless", "pipeline", "run"]
    assert argv[3] == "--standalone"
    assert argv[-2] == "do it"


def test_idle_and_redaction():
    assert dispatch.dispatch(FakeAPI([]), Path("."), "firmware", "lead", "x") == "idle"
    assert dispatch.redact("https://oauth2:SECRET@h/p.git failed", "SECRET") == "https://oauth2:[REDACTED]@h/p.git failed"
    assert dispatch.slugify("Fix: UART baud!! divisor (v2)") == "fix-uart-baud-divisor-v2"
