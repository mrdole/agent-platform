"""gitlab_bots group tokens, protected paths, branch lockdown and export, against a fake API."""
import datetime as dt
import json
import stat
import subprocess
import urllib.parse

import pytest

import botenv
import gitlab_bots as gb
from test_bots import FakeGitLab

TODAY = dt.date(2026, 10, 30)
HUMANS = 77
LOCK = {"human_group": "grp/humans", "human_branches": "*/*", "agent_branches": "agent/*",
        "projects": ["grp/fw"]}


class Fake(FakeGitLab):
    """Adds the routes the lockdown features use. Anything else goes to FakeGitLab."""

    def __init__(self, rules=None, shared=(), members=None):
        super().__init__()
        self.group_tokens = {}
        self.group_vars = {}
        self.rules = {p: dict(r) for p, r in (rules or {}).items()}
        self.shared = {"grp/fw": list(shared)}
        self.member_list = members or {}
        self.calls = []

    def _new(self, project, name, expires, data=None):
        t = super()._new(project, name, expires, data)
        t["user_id"] = 1000 + t["id"]
        return t

    def __call__(self, method, url, headers, body):
        split = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(split.path.split("/api/v4", 1)[1])
        data = json.loads(body) if body else {}
        self.calls.append((method, path))
        if path.startswith("/groups/"):
            return self._group(method, path[len("/groups/"):], data)
        rest = path[len("/projects/"):]
        if rest.endswith("/members/all"):
            members = self.member_list.get(rest[:-len("/members/all")])
            return (404, b"{}") if members is None else (200, json.dumps(members).encode())
        if rest.endswith("/share"):
            self.shared.setdefault(rest[:-len("/share")], []).append(data["group_id"])
            return 201, b"{}"
        if rest.endswith("/protected_branches") and "allowed_to_push" in data:
            project = rest[:-len("/protected_branches")]
            self.rules.setdefault(project, {})[data["name"]] = {
                "name": data["name"], "push_access_levels": data["allowed_to_push"],
                "merge": data["allowed_to_merge"], "body": data}
            return 201, b"{}"
        if rest.endswith("/protected_branches") and method == "GET":
            project = rest[:-len("/protected_branches")]
            return 200, json.dumps(list(self.rules.get(project, {}).values())).encode()
        if "/" in rest and rest in self.shared and method == "GET":
            return 200, json.dumps({"shared_with_groups": [{"group_id": g} for g in self.shared[rest]]}).encode()
        return super().__call__(method, url, headers, body)

    def _group(self, method, rest, data):
        if rest == "grp/humans":
            return 200, json.dumps({"id": HUMANS}).encode()
        if "/variables" in rest:
            group, _, key = rest.partition("/variables")
            key = key.strip("/") or data["key"]
            if method == "PUT" and (group, key) not in self.group_vars:
                return 404, b"{}"
            self.group_vars[(group, key)] = data
            return 200, b"{}"
        group, _, tail = rest.partition("/access_tokens")
        tokens = self.group_tokens.setdefault(group, [])
        if method == "GET":
            return 200, json.dumps(tokens).encode()
        if tail.endswith("/rotate"):
            old = next(t for t in tokens if t["id"] == int(tail.split("/")[1]))
            old["active"] = False
            data = {"name": old["name"], "expires_at": data["expires_at"]}
        token = {"id": self.next_id, "name": data["name"], "active": True, "user_id": 2000 + self.next_id,
                 "expires_at": data["expires_at"], "token": f"glpat-group-{self.next_id}"}
        self.next_id += 1
        tokens.append(token)
        return 201, json.dumps(token).encode()


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("AP_BOT_TOKENS", raising=False)
    return tmp_path / "cfg" / "agent-platform"


def go(fake, cfg, mode="apply", **kw):
    lines = []
    rc = gb.run(gb.API("https://gitlab.test", "pat", fake), {"gitlab_url": "https://gitlab.test", **cfg},
                mode, today=TODAY, out=lines.append, **kw)
    return rc, lines


# ---------------- protected paths ----------------
@pytest.mark.parametrize("cfg", [
    {"group_profiles": {"it-debug": ["lab"]}},
    {"group_profiles": {"it-debug": ["lab/lab-limits"]}},
    {"profiles": {"firmware": ["lab/lab-limits"]}},
    {"profiles": {"firmware": ["lab/lab-limits/sub"]}},
])
def test_token_reaching_protected_path_is_refused_before_any_call(cfgdir, cfg):
    fake = Fake()
    rc, lines = go(fake, {**cfg, "protected_paths": ["lab/lab-limits"]})
    assert rc == 2 and fake.calls == [] and lines[0].startswith("refused:")
    assert not cfgdir.exists()


def test_similar_name_is_not_a_match():
    assert gb.forbidden_targets({"protected_paths": ["lab/lab-limits"],
                                 "profiles": {"firmware": ["lab/lab-limits-docs"]},
                                 "group_profiles": {"it-debug": ["la"]}}) == []


def test_bot_found_on_protected_path_is_flagged(cfgdir):
    fake = Fake(members={"lab/lab-limits": [{"username": "alice"}, {"username": "group_12_bot_abc"}]})
    rc, lines = go(fake, {"protected_paths": ["lab/lab-limits"]}, "status")
    assert rc == 1 and any("group_12_bot_abc" in line for line in lines)


def test_clean_protected_path_passes_and_unreadable_one_fails(cfgdir):
    fake = Fake(members={"lab/lab-limits": [{"username": "alice"}]})
    assert go(fake, {"protected_paths": ["lab/lab-limits"]}, "status")[0] == 0
    rc, lines = go(Fake(), {"protected_paths": ["lab/lab-limits"]}, "status")
    assert rc == 1 and any("can't list members" in line for line in lines)


# ---------------- group tokens ----------------
def test_group_token_created_stored_and_kept(cfgdir):
    fake = Fake()
    cfg = {"group_profiles": {"it-debug": ["grp"]}}
    rc, lines = go(fake, cfg, ci_vars=True)
    assert rc == 0
    token = fake.group_tokens["grp"][0]
    assert token["name"] == "agent-it-debug"
    assert fake.group_vars[("grp", "GITLAB_TOKEN_ANALYST")]["value"] == token["token"]
    assert fake.group_vars[("grp", "GITLAB_TOKEN_ANALYST")]["protected"]
    saved = json.loads((cfgdir / "bot-tokens.json").read_text())["it-debug"]["grp"]
    assert saved["kind"] == "group" and saved["token"] == token["token"]
    assert not any("glpat-" in line for line in lines)
    _, lines = go(fake, cfg)
    assert any("token:keep" in line for line in lines) and len(fake.group_tokens["grp"]) == 1


def test_group_plan_changes_nothing(cfgdir):
    fake = Fake()
    go(fake, {"group_profiles": {"it-debug": ["grp"]}}, "plan")
    assert fake.group_tokens["grp"] == [] and not cfgdir.exists()


def test_lost_group_value_rotates(cfgdir):
    fake = Fake()
    cfg = {"group_profiles": {"it-debug": ["grp"]}}
    go(fake, cfg)
    (cfgdir / "bot-tokens.json").unlink()
    _, lines = go(fake, cfg)
    assert any("token:rotate" in line for line in lines)
    assert [t["active"] for t in fake.group_tokens["grp"]] == [False, True]


@pytest.fixture
def clone(tmp_path):
    def make(remote):
        repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", remote], check=True)
        return repo
    return make


def test_botenv_uses_most_specific_group_and_project_wins(cfgdir, clone):
    cfgdir.mkdir(parents=True)
    (cfgdir / "bot-tokens.json").write_text(json.dumps({"firmware": {
        "grp": {"token": "outer", "kind": "group"},
        "grp/sub": {"token": "inner", "kind": "group"},
        "grp/sub/exact": {"token": "project"}}}))
    assert botenv.lookup_token("firmware", "NO_SUCH_VAR", "grp/other")[0] == "outer"
    assert botenv.lookup_token("firmware", "NO_SUCH_VAR", "grp/sub/fw")[0] == "inner"
    assert botenv.lookup_token("firmware", "NO_SUCH_VAR", "grp/sub/exact")[0] == "project"
    assert botenv.lookup_token("firmware", "NO_SUCH_VAR", "grpx/fw")[0] == ""
    env, _ = botenv.build_env("firmware", clone("git@gitlab.test:grp/sub/fw.git"), {})
    assert env["GITLAB_TOKEN"] == "inner"


# ---------------- branch lockdown ----------------
def test_lockdown_plan_changes_nothing(cfgdir):
    fake = Fake()
    rc, lines = go(fake, {"profiles": {"firmware": ["grp/fw"]}, "branch_lockdown": LOCK}, "plan")
    assert rc == 0 and fake.rules == {} and fake.shared["grp/fw"] == []
    assert any("would add */*" in line for line in lines)
    assert any("would add agent/*" in line for line in lines)


def test_flag_is_required_to_change_branches(cfgdir):
    fake = Fake()
    go(fake, {"profiles": {"firmware": ["grp/fw"]}, "branch_lockdown": LOCK})
    assert fake.rules == {} and fake.shared["grp/fw"] == []


def test_lockdown_adds_rules_with_bot_only_on_agent_branches(cfgdir):
    fake = Fake()
    rc, _ = go(fake, {"profiles": {"firmware": ["grp/fw"]}, "branch_lockdown": LOCK}, branches=True)
    assert rc == 0
    bot = {"user_id": fake.tokens["grp/fw"][0]["user_id"]}
    humans = {"group_id": HUMANS}
    rules = fake.rules["grp/fw"]
    assert rules["*/*"]["push_access_levels"] == [humans]
    assert rules["agent/*"]["push_access_levels"] == [humans, bot]
    assert rules["agent/*"]["merge"] == [humans]               # bots never merge
    assert rules["agent/*"]["body"]["allow_force_push"] is False
    assert fake.shared["grp/fw"] == [HUMANS]


def test_reporter_bot_gets_no_branch_rights(cfgdir):
    fake = Fake()
    go(fake, {"profiles": {"it-debug": ["grp/fw"]}, "branch_lockdown": LOCK}, branches=True)
    assert fake.rules["grp/fw"]["agent/*"]["push_access_levels"] == [{"group_id": HUMANS}]


def test_group_bot_is_added_for_projects_in_its_group(cfgdir):
    fake = Fake()
    go(fake, {"group_profiles": {"firmware": ["grp"]}, "branch_lockdown": LOCK}, branches=True)
    uid = fake.group_tokens["grp"][0]["user_id"]
    assert {"user_id": uid} in fake.rules["grp/fw"]["agent/*"]["push_access_levels"]


def test_second_run_is_stable_and_never_rewrites(cfgdir):
    fake = Fake()
    cfg = {"profiles": {"firmware": ["grp/fw"]}, "branch_lockdown": LOCK}
    go(fake, cfg, branches=True)
    before = json.dumps(fake.rules, sort_keys=True)
    fake.calls.clear()
    rc, lines = go(fake, cfg, branches=True)
    assert rc == 0 and json.dumps(fake.rules, sort_keys=True) == before
    assert sum(" ok" in line for line in lines) == 2
    assert ("POST", "/projects/grp/fw/share") not in fake.calls


def test_existing_rule_missing_the_bot_is_reported_not_changed(cfgdir):
    existing = {"*/*": {"name": "*/*", "push_access_levels": [{"group_id": HUMANS, "access_level": 40}]},
                "agent/*": {"name": "agent/*", "push_access_levels": [{"group_id": HUMANS, "access_level": 40}]}}
    fake = Fake(rules={"grp/fw": existing}, shared=[HUMANS])
    rc, lines = go(fake, {"profiles": {"firmware": ["grp/fw"]}, "branch_lockdown": LOCK}, branches=True)
    assert rc == 1 and any("rule agent/* in grp/fw lacks" in line for line in lines)
    assert fake.rules["grp/fw"] == existing


def test_star_pattern_warns_about_main(cfgdir):
    _, lines = go(Fake(), {"branch_lockdown": {**LOCK, "human_branches": "*"}}, "plan")
    assert any("push straight to main" in line for line in lines)


def test_human_branches_has_no_default(cfgdir):
    fake = Fake()
    conf = {k: v for k, v in LOCK.items() if k != "human_branches"}
    rc, lines = go(fake, {"branch_lockdown": conf}, branches=True)
    assert rc == 1 and fake.rules == {} and any("needs human_branches" in line for line in lines)


# ---------------- export ----------------
def seeded(cfgdir):
    cfgdir.mkdir(parents=True)
    (cfgdir / "bot-tokens.json").write_text(json.dumps(
        {"firmware": {"grp/fw": {"token": "fw-secret"}}, "meta": {"grp/ap": {"token": "meta-secret"}}}))


def test_export_writes_only_named_profiles(cfgdir, tmp_path):
    seeded(cfgdir)
    dest = tmp_path / "handoff" / "bot-tokens.json"
    lines = []
    assert gb.export_tokens(["firmware"], dest, out=lines.append) == 0
    assert json.loads(dest.read_text()) == {"firmware": {"grp/fw": {"token": "fw-secret"}}}
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert not any("secret" in line for line in lines)


def test_export_refuses_unknown_profile_and_own_store(cfgdir, tmp_path):
    seeded(cfgdir)
    dest = tmp_path / "out.json"
    assert gb.export_tokens(["firmware", "bench"], dest, out=lambda s: None) == 2 and not dest.exists()
    assert gb.export_tokens(["firmware"], cfgdir / "bot-tokens.json", out=lambda s: None) == 2


def test_export_needs_no_pat(cfgdir, tmp_path, monkeypatch):
    seeded(cfgdir)
    monkeypatch.delenv("GITLAB_PAT", raising=False)
    dest = tmp_path / "out.json"
    assert gb.main(["export", "--profile", "firmware", "--out", str(dest)]) == 0 and dest.is_file()
    assert gb.main(["export", "--profile", "firmware"]) == 2


def test_exported_file_works_through_ap_bot_tokens(cfgdir, tmp_path, monkeypatch):
    seeded(cfgdir)
    dest = tmp_path / "out.json"
    gb.export_tokens(["firmware"], dest, out=lambda s: None)
    monkeypatch.setenv("AP_BOT_TOKENS", str(dest))
    assert botenv.lookup_token("firmware", "NO_SUCH_VAR", "grp/fw")[0] == "fw-secret"
    assert botenv.lookup_token("meta", "NO_SUCH_VAR", "grp/ap")[0] == ""
