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
