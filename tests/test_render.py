import json
from pathlib import Path

import render


def test_deep_merge_keeps_rule_order_and_concats_instructions():
    base = {"permission": {"bash": {"*": "ask", "git *": "allow"}}, "instructions": ["a"]}
    over = {"permission": {"bash": {"make*": "allow", "*": "deny"}}, "instructions": ["b", "a"]}
    out = render.deep_merge(base, over)
    assert list(out["permission"]["bash"]) == ["*", "git *", "make*"]  # "*" stays first
    assert out["permission"]["bash"]["*"] == "deny"
    assert out["instructions"] == ["a", "b"]
    assert base["permission"]["bash"]["*"] == "ask"  # input untouched


def test_resolve_refs_makes_paths_absolute(tmp_path: Path):
    obj = {"prompt": "{file:./p.md}", "instructions": ["./R.md", "/abs.md"], "x": "{file:{env:AP_HOME}/y}"}
    out = render.resolve_refs(obj, tmp_path)
    assert out["prompt"] == "{file:" + str(tmp_path / "p.md") + "}"
    assert out["instructions"] == [str(tmp_path / "R.md"), "/abs.md"]
    assert out["x"] == "{file:{env:AP_HOME}/y}"


def test_every_profile_renders_both_modes(root):
    for profile in render.profiles(root):
        normal = render.render(profile, ap_home=root)
        headless = render.render(profile, headless=True, ap_home=root)
        assert normal["permission"]["skill"]["*"] == "deny"
        assert headless["permission"]["bash"]["*"] == "deny"
        assert "provider" in normal and normal["provider"]["corp"]["options"]["apiKey"] == "{env:CORP_LLM_KEY}"
        for path in normal["instructions"]:
            assert Path(path).is_file(), path


def test_firmware_cannot_flash_headless(root):
    assert render.render("firmware", ap_home=root)["permission"]["bash"]["bench-lock *"] == "ask"
    assert render.render("firmware", headless=True, ap_home=root)["permission"]["bash"]["bench-lock *"] == "deny"


def test_cli_writes_file(root, tmp_path, capsys):
    out = tmp_path / "x.json"
    assert render.main(["pipeline", "--headless", "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip() == str(out)
    assert json.loads(out.read_text())["permission"]["bash"]["glab mr create*"] == "deny"
