import json

import check


def run(root):
    return check.main(["--root", str(root)])


def test_repo_is_clean(root, capsys):
    assert run(root) == 0, capsys.readouterr().out


def test_skill_name_mismatch_fails(platform_copy, capsys):
    md = platform_copy / "skills" / "core-handoff" / "SKILL.md"
    md.write_text(md.read_text().replace("name: core-handoff", "name: core-other"))
    assert run(platform_copy) == 1
    assert "must equal directory name" in capsys.readouterr().out


def test_unacknowledged_headless_ask_fails(platform_copy, capsys):
    (platform_copy / "profiles" / "firmware" / "opencode.headless.json").unlink()
    assert run(platform_copy) == 1
    assert "bench-lock *" in capsys.readouterr().out


def test_hardcoded_model_fails(platform_copy, capsys):
    p = platform_copy / "profiles" / "meta" / "opencode.json"
    cfg = json.loads(p.read_text())
    cfg["agent"]["tester"]["model"] = "corp/gpt-oss-20b"
    p.write_text(json.dumps(cfg))
    assert run(platform_copy) == 1
    assert "must be {env:AP_MODEL_*}" in capsys.readouterr().out


def test_reporter_profile_must_not_edit(platform_copy, capsys):
    p = platform_copy / "profiles" / "it-debug" / "opencode.json"
    cfg = json.loads(p.read_text())
    cfg["permission"]["edit"] = "allow"
    p.write_text(json.dumps(cfg))
    assert run(platform_copy) == 1
    assert "reporter-role profile must set permission.edit to deny" in capsys.readouterr().out


def test_secret_is_caught(platform_copy, capsys):
    fake = "glpat" + "-" + "x" * 22  # built at runtime so this file doesn't trip the scanner
    (platform_copy / "core" / "oops.md").write_text(f"token: {fake}\n")
    assert run(platform_copy) == 1
    assert "secret" in capsys.readouterr().out


def _headless(platform_copy, edit):
    p = platform_copy / "core" / "opencode.headless.json"
    cfg = json.loads(p.read_text())
    edit(cfg["permission"])
    p.write_text(json.dumps(cfg))


def test_headless_redirect_deny_missing_fails(platform_copy, capsys):
    _headless(platform_copy, lambda perm: perm["bash"].pop("*>*"))
    assert run(platform_copy) == 1
    out = capsys.readouterr().out
    assert "still lets a redirect through" in out and "'cat *'" in out


def test_headless_narrow_redirect_deny_still_fails(platform_copy, capsys):
    # `* > *` misses `cat f>out`
    def edit(perm):
        perm["bash"].pop("*>*")
        perm["bash"]["* > *"] = "deny"
    _headless(platform_copy, edit)
    assert run(platform_copy) == 1
    assert "still lets a redirect through" in capsys.readouterr().out


def test_profile_allow_after_redirect_deny_fails(platform_copy, capsys):
    # a profile allow rule renders before the headless overlay, so it must still be caught
    p = platform_copy / "profiles" / "meta" / "opencode.headless.json"
    p.write_text(json.dumps({"permission": {"bash": {"*>*": "allow"}}}))
    assert run(platform_copy) == 1
    assert "still lets a redirect through" in capsys.readouterr().out


def test_headless_execute_deny_missing_fails(platform_copy, capsys):
    _headless(platform_copy, lambda perm: perm.pop("execute"))
    assert run(platform_copy) == 1
    assert "must deny the 'execute' tool" in capsys.readouterr().out
