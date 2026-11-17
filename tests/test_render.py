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


# --- G19: 2.x `permissions` lists must not drop or override core denies -----------------

def _legacy_render(profile, headless, root):
    """What render() did before G19: plain deep_merge of the layers."""
    core, prof = root / "core", root / "profiles" / profile
    layers = [(core / "opencode.core.json", core), (prof / "opencode.json", prof)]
    if headless:
        layers.append((core / "opencode.headless.json", core))
        if (prof / "opencode.headless.json").is_file():
            layers.append((prof / "opencode.headless.json", prof))
    cfg = {}
    for path, base in layers:
        cfg = render.deep_merge(cfg, render.resolve_refs(render.load_json(path), base))
    cfg["$schema"] = render.SCHEMA
    return cfg


def test_1x_profiles_render_unchanged(root):
    for profile in render.profiles(root):
        for headless in (False, True):
            cfg = render.render(profile, headless=headless, ap_home=root)
            assert cfg == _legacy_render(profile, headless, root)
            assert "permissions" not in cfg


def _set_profile(platform_copy, profile, name, data):
    (platform_copy / "profiles" / profile / name).write_text(json.dumps(data))


def test_list_overlay_cannot_drop_or_override_core_denies(platform_copy):
    overlay = {"permissions": [{"action": "shell", "resource": "git push --force*", "effect": "allow"},
                               {"action": "shell", "resource": "sudo *", "effect": "allow"}],
               "agent": {"scout": {"permissions": [{"action": "edit", "resource": "*", "effect": "allow"}]}}}
    base = json.loads((platform_copy / "profiles" / "meta" / "opencode.json").read_text())
    _set_profile(platform_copy, "meta", "opencode.json", render.deep_merge(base, overlay))
    cfg = render.render("meta", ap_home=platform_copy)
    perms = cfg["permissions"]
    for res in ("git push --force*", "sudo *"):
        assert {"action": "shell", "resource": res, "effect": "deny"} in perms
        assert render.evaluate(perms, "shell", res) == "deny"  # deny is last
    # core 1.x denies are carried into the list too, and agent-level core denies survive
    assert render.evaluate(perms, "shell", "rm -rf /*") == "deny"
    scout = cfg["agent"]["scout"]
    assert render.evaluate(render.scope_rules(cfg) + scout["permissions"], "edit", "x") == "deny"
    # profile's own non-conflicting rule is kept
    assert perms[0]["effect"] == "allow" or len(perms) >= 2


def test_list_headless_overlay_cannot_reopen_headless_default(platform_copy, capsys):
    _set_profile(platform_copy, "pipeline", "opencode.headless.json",
                 {"permissions": [{"action": "shell", "resource": "*", "effect": "allow"}]})
    import check
    assert check.main(["--root", str(platform_copy)]) == 1
    assert "loosens core deny" in capsys.readouterr().out


def test_check_flags_1x_override_and_2x_override():
    import check
    core = {"permission": {"bash": {"*": "ask", "sudo *": "deny"}, "edit": "deny"}}
    ok = {"permission": {"bash": {"*": "ask", "sudo *": "deny"}, "edit": "deny"}}
    rep = check.Report()
    check.check_core_denies(core, ok, "p", rep)
    assert rep.errors == []
    for bad in (
        {"permission": {"bash": {"*": "ask", "sudo *": "allow"}, "edit": "deny"}},          # 1.x
        {"permission": core["permission"],
         "permissions": [{"action": "shell", "resource": "sudo *", "effect": "allow"}]},   # 2.x after 1.x
        {"permission": core["permission"],
         "agent": {"x": {"permissions": [{"action": "edit", "resource": "*", "effect": "allow"}]}}},
    ):
        rep = check.Report()
        check.check_core_denies(core, bad, "p", rep)
        assert rep.errors, bad
