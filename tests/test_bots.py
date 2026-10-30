import datetime as dt
import json
import os
import stat
import subprocess
import urllib.parse

import pytest

import botenv
import gitlab_bots as gb


# ---------------- botenv ----------------
@pytest.mark.parametrize("url,expected", [
    ("git@gitlab.test:grp/sub/fw.git", ("gitlab.test", "grp/sub/fw")),
    ("ssh://git@gitlab.test:2222/grp/fw.git", ("gitlab.test", "grp/fw")),
    ("https://gitlab.test/grp/fw.git", ("gitlab.test", "grp/fw")),
    ("https://oauth2:x@gitlab.test/grp/fw", ("gitlab.test", "grp/fw")),
])
def test_parse_remote(url, expected):
    assert botenv.parse_remote(url) == expected


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("GITLAB_TOKEN_FIRMWARE", raising=False)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    r = tmp_path / "fw"
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "remote", "add", "origin", "git@gitlab.test:grp/fw.git"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "Evan"], check=True)
    return r


def store_token(tmp_path, value="glpat-bot-firmware-value"):
    f = tmp_path / "cfg" / "agent-platform" / "bot-tokens.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"firmware": {"grp/fw": {"token": value}}}))


def test_no_token_warns_and_exports_nothing(repo, monkeypatch):
    env, notes = botenv.build_env("firmware", repo)
    assert env == {} and "YOUR identity" in notes[0]
    monkeypatch.setenv("AP_REQUIRE_BOT", "1")
    assert botenv.main(["firmware", "--dir", str(repo)]) == 3


def test_session_acts_as_bot(repo, tmp_path):
    store_token(tmp_path)
    env, _ = botenv.build_env("firmware", repo)
    assert env["GITLAB_TOKEN"] == "glpat-bot-firmware-value" and env["GITLAB_HOST"] == "gitlab.test"
    assert env["GIT_AUTHOR_NAME"] == "agent-firmware (via Evan)"
    run_env = {**os.environ, **env}
    # SSH origin is pushed over HTTPS...
    push_url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "--push", "origin"],
                              env=run_env, capture_output=True, text=True).stdout.strip()
    assert push_url == "https://gitlab.test/grp/fw.git"
    # ...and git's credential lookup for that host returns the bot token.
    cred = subprocess.run(["git", "-C", str(repo), "credential", "fill"], env=run_env, capture_output=True,
                          text=True, input="protocol=https\nhost=gitlab.test\n\n").stdout
    assert "username=oauth2" in cred and "password=glpat-bot-firmware-value" in cred
    # nothing was written to the repo's git config
    assert "pushinsteadof" not in (repo / ".git" / "config").read_text().lower()


def test_ci_env_token_wins_and_existing_git_config_env_kept(repo, tmp_path, monkeypatch):
    store_token(tmp_path)
    monkeypatch.setenv("GITLAB_TOKEN_FIRMWARE", "from-ci")
    env, _ = botenv.build_env("firmware", repo, {**os.environ, "GIT_CONFIG_COUNT": "2"})
    assert env["GITLAB_TOKEN"] == "from-ci"
    assert "GIT_CONFIG_KEY_2" in env and env["GIT_CONFIG_COUNT"] == "6"


# ---------------- gitlab_bots ----------------
class FakeGitLab:
    def __init__(self, protected=None):
        self.tokens = {}       # project -> list
        self.vars = {}
        self.labels = set()
        self.protected = dict(protected or {})
        self.next_id = 1

    def __call__(self, method, url, headers, body):
        assert headers["PRIVATE-TOKEN"] == "pat"
        path = urllib.parse.unquote(urllib.parse.urlsplit(url).path.split("/api/v4", 1)[1])
        data = json.loads(body) if body else {}
        parts = path.split("/projects/", 1)[1]
        project, _, rest = parts.partition("/access_tokens") if "/access_tokens" in parts else (None, None, None)
        if "/access_tokens" in path:
            project = parts.split("/access_tokens")[0]
            if method == "GET":
                return 200, json.dumps(self.tokens.get(project, [])).encode()
            if rest.endswith("/rotate"):
                tid = int(rest.split("/")[1])
                for t in self.tokens[project]:
                    if t["id"] == tid:
                        t["active"] = False
                return 200, json.dumps(self._new(project, "agent-x-rotated", data["expires_at"])).encode()
            return 201, json.dumps(self._new(project, data["name"], data["expires_at"], data)).encode()
        if "/variables" in path:
            project, _, key = parts.partition("/variables")
            key = key.strip("/") or data["key"]
            if method == "PUT" and (project, key) not in self.vars:
                return 404, b"{}"
            self.vars[(project, key)] = data
            return 200, b"{}"
        if "/protected_branches" in path:
            project, _, br = parts.partition("/protected_branches")
            if method == "GET":
                p = self.protected.get(project)
                return (200, json.dumps(p).encode()) if p else (404, b"{}")
            self.protected[project] = {"push_access_levels": [{"access_level": data["push_access_level"]}]}
            return 201, b"{}"
        if "/labels" in path:
            project = parts.split("/labels")[0]
            key = (project, data["name"])
            if key in self.labels:
                return 409, b"{}"
            self.labels.add(key)
            return 201, b"{}"
        raise AssertionError(f"unexpected {method} {path}")

    def _new(self, project, name, expires, data=None):
        t = {"id": self.next_id, "name": name if data else self._name_of_rotated(project), "active": True,
             "expires_at": expires, "token": f"glpat-generated-{self.next_id}"}
        if data:
            t.update(scopes=data["scopes"], access_level=data["access_level"])
        self.next_id += 1
        self.tokens.setdefault(project, []).append(t)
        return t

    def _name_of_rotated(self, project):
        return next(t["name"] for t in self.tokens[project] if not t["active"])


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg" / "agent-platform"


CFG = {"gitlab_url": "https://gitlab.test", "expires_days": 30,
       "profiles": {"firmware": ["grp/fw"], "it-debug": ["grp/fw"]}}
TODAY = dt.date(2026, 10, 30)


def test_plan_makes_no_changes(cfgdir):
    fake = FakeGitLab()
    lines = []
    gb.run(gb.API("https://gitlab.test", "pat", fake), CFG, "plan", today=TODAY, out=lines.append)
    assert fake.tokens == {} and not cfgdir.exists()
    assert any("token:create" in line and "main:unprotected" in line for line in lines)


def test_apply_creates_tokens_vars_labels_protection(cfgdir):
    fake = FakeGitLab()
    lines = []
    rc = gb.run(gb.API("https://gitlab.test", "pat", fake), CFG, "apply", ci_vars=True, protect=True,
                labels=True, today=TODAY, out=lines.append)
    assert rc == 0
    toks = fake.tokens["grp/fw"]
    fw = next(t for t in toks if t["name"] == "agent-firmware")
    an = next(t for t in toks if t["name"] == "agent-it-debug")
    assert fw["access_level"] == 30 and fw["scopes"] == ["api", "write_repository"]
    assert an["access_level"] == 20 and an["scopes"] == ["read_api", "read_repository"]
    assert fw["expires_at"] == "2026-11-29"
    v = fake.vars[("grp/fw", "GITLAB_TOKEN_FIRMWARE")]
    assert v["masked"] and v["protected"] and v["value"] == fw["token"]
    assert ("grp/fw", "agent::ready") in fake.labels and ("grp/fw", "profile::it-debug") in fake.labels
    assert fake.protected["grp/fw"]["push_access_levels"] == [{"access_level": 0}]
    store = cfgdir / "bot-tokens.json"
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert json.loads(store.read_text())["firmware"]["grp/fw"]["token"] == fw["token"]
    assert not any("glpat-" in line for line in lines)   # values never printed


def test_second_apply_keeps_and_missing_value_rotates(cfgdir):
    fake = FakeGitLab()
    api = gb.API("https://gitlab.test", "pat", fake)
    one = {**CFG, "profiles": {"firmware": ["grp/fw"]}}
    gb.run(api, one, "apply", today=TODAY, out=lambda s: None)
    lines = []
    gb.run(api, one, "apply", today=TODAY, out=lines.append)
    assert "token:kept" in lines[0] and len(fake.tokens["grp/fw"]) == 1
    (cfgdir / "bot-tokens.json").unlink()          # lost the value -> must rotate to get a new one
    lines = []
    gb.run(api, one, "apply", today=TODAY, out=lines.append)
    assert "token:rotated" in lines[0]
    assert json.loads((cfgdir / "bot-tokens.json").read_text())["firmware"]["grp/fw"]["token"] == "glpat-generated-2"


def test_flags_main_that_allows_pushes(cfgdir):
    fake = FakeGitLab(protected={"grp/fw": {"push_access_levels": [{"access_level": 40}]}})
    lines = []
    rc = gb.run(gb.API("https://gitlab.test", "pat", fake), {**CFG, "profiles": {"firmware": ["grp/fw"]}},
                "status", today=TODAY, out=lines.append)
    assert rc == 1 and any("allows direct pushes" in line for line in lines)
