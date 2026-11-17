#!/usr/bin/env python3
"""Render the OpenCode config for a profile: core + profile (+ headless overlays).

    render.py PROFILE [--headless] [--out PATH] [--print]

Writes the merged config (default ~/.cache/agent-platform/<profile>[.headless].opencode.json)
and prints its path, which the `agent` launcher passes to OpenCode as OPENCODE_CONFIG.

Merge rules (see BLUEPRINT.md section 4):
  * dicts merge recursively; new keys are appended, existing keys keep their position, so
    permission rule order ("*" first, specific rules after) survives the merge
  * `instructions` lists are concatenated (core first); other lists are replaced
  * OpenCode 2.x `permissions` lists (top level and per agent) are concatenated too, and
    then every hard core `deny` is re-appended at the end, so with last-match-wins no later
    profile or headless rule can override it. Catch-all defaults (`*` deny that the core
    itself narrows later) stay where they are; tools/check.py verifies them instead.
    1.x `permission` objects merge as before.
  * relative paths in `{file:...}` references and in `instructions` are made absolute,
    relative to the file they were written in
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path

AP_HOME = Path(os.environ.get("AP_HOME") or Path(__file__).resolve().parent.parent)
SCHEMA = "https://opencode.ai/config.json"
FILE_REF = re.compile(r"\{file:([^}]+)\}")
CONCAT_KEYS = {"instructions", "permissions"}
ACTION_ALIAS = {"bash": "shell", "task": "subagent"}  # 1.x name -> 2.x name


def _glob(pattern: str, text: str) -> bool:
    return re.fullmatch(".*".join(re.escape(p) for p in pattern.split("*")), text, re.S) is not None


def object_rules(perm) -> list[dict]:
    """1.x `permission` object -> ordered 2.x style rules."""
    rules = []
    for action, val in (perm.items() if isinstance(perm, dict) else []):
        action = ACTION_ALIAS.get(action, action)
        if isinstance(val, str):
            rules.append({"action": action, "resource": "*", "effect": val})
        elif isinstance(val, dict):
            rules += [{"action": action, "resource": res, "effect": eff}
                      for res, eff in val.items() if isinstance(eff, str)]
    return rules


def list_rules(perms) -> list[dict]:
    return [r for r in perms if isinstance(r, dict)] if isinstance(perms, list) else []


def scope_rules(node) -> list[dict]:
    """All rules of one scope (global or one agent), 1.x object first, then 2.x list."""
    node = node if isinstance(node, dict) else {}
    return object_rules(node.get("permission")) + list_rules(node.get("permissions"))


def scope_variants(node) -> list[list[dict]]:
    """Both orders in which a runtime might stack the object and list forms."""
    node = node if isinstance(node, dict) else {}
    o, li = object_rules(node.get("permission")), list_rules(node.get("permissions"))
    return [o + li, li + o] if o and li else [o + li]


def _key(rule: dict) -> tuple:
    return (rule.get("action"), rule.get("resource"), rule.get("effect"))


def evaluate(rules: list[dict], action: str, resource: str):
    """Effect of the last rule matching (action, resource); None if no rule matches."""
    effect = None
    for r in rules:
        if _glob(str(r.get("action", "")), action) and _glob(str(r.get("resource", "")), resource):
            effect = r.get("effect")
    return effect


def hard_denies(rules: list[dict]) -> list[dict]:
    """Denies that are not a catch-all default (a `*` deny the same chain narrows later)."""
    out = []
    for i, r in enumerate(rules):
        if r.get("effect") != "deny":
            continue
        if r.get("resource") == "*" and any(
            q.get("action") == r.get("action") and q.get("effect") != "deny" for q in rules[i + 1:]
        ):
            continue
        out.append({"action": r.get("action"), "resource": r.get("resource"), "effect": "deny"})
    return out


def _reappend(perms: list, denies: list[dict]) -> list:
    keys = {_key(d) for d in denies}
    return [r for r in perms if not (isinstance(r, dict) and _key(r) in keys)] + copy.deepcopy(denies)


def enforce_core_denies(cfg: dict, core_cfg: dict) -> dict:
    """Wherever the merged config has a 2.x `permissions` list, end it with the hard core denies."""
    core_global = scope_rules(core_cfg)
    if isinstance(cfg.get("permissions"), list):
        cfg["permissions"] = _reappend(cfg["permissions"], hard_denies(core_global))
    for name, agent in (cfg.get("agent") or {}).items():
        if isinstance(agent, dict) and isinstance(agent.get("permissions"), list):
            core_agent = ((core_cfg.get("agent") or {}).get(name))
            agent["permissions"] = _reappend(agent["permissions"],
                                             hard_denies(core_global + scope_rules(core_agent)))
    return cfg


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        elif key in CONCAT_KEYS and isinstance(out.get(key), list) and isinstance(value, list):
            out[key] = out[key] + [v for v in value if v not in out[key]]
        else:
            out[key] = copy.deepcopy(value)
    return out


def _absolute(ref: str, base_dir: Path) -> str:
    if ref.startswith(("/", "~", "{env:")):
        return ref
    return str((base_dir / ref).resolve())


def resolve_refs(obj, base_dir: Path):
    if isinstance(obj, dict):
        return {
            k: [_absolute(p, base_dir) for p in v] if k == "instructions" and isinstance(v, list)
            else resolve_refs(v, base_dir)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [resolve_refs(v, base_dir) for v in obj]
    if isinstance(obj, str):
        return FILE_REF.sub(lambda m: "{file:" + _absolute(m.group(1).strip(), base_dir) + "}", obj)
    return obj


def load_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"render: {path}: invalid JSON: {exc}") from exc


def profiles(ap_home: Path = AP_HOME) -> list[str]:
    return sorted(p.name for p in (ap_home / "profiles").iterdir() if (p / "opencode.json").is_file())


def render(profile: str, headless: bool = False, ap_home: Path = AP_HOME) -> dict:
    core_dir = ap_home / "core"
    prof_dir = ap_home / "profiles" / profile
    if not (prof_dir / "opencode.json").is_file():
        raise SystemExit(f"render: unknown profile '{profile}'. Known: {', '.join(profiles(ap_home))}")

    layers = [(core_dir / "opencode.core.json", core_dir), (prof_dir / "opencode.json", prof_dir)]
    if headless:
        layers.append((core_dir / "opencode.headless.json", core_dir))
        if (prof_dir / "opencode.headless.json").is_file():
            layers.append((prof_dir / "opencode.headless.json", prof_dir))

    cfg: dict = {}
    core_cfg: dict = {}
    for path, base in layers:
        layer = resolve_refs(load_json(path), base)
        cfg = deep_merge(cfg, layer)
        if base == core_dir:
            core_cfg = deep_merge(core_cfg, layer)
    enforce_core_denies(cfg, core_cfg)
    cfg["$schema"] = SCHEMA
    return cfg


def default_out(profile: str, headless: bool) -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "agent-platform"
    return cache / f"{profile}{'.headless' if headless else ''}.opencode.json"


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--headless", action="store_true", help="apply the headless overlay (bash defaults to deny)")
    ap.add_argument("--out", type=Path, help="output path")
    ap.add_argument("--print", action="store_true", help="print the merged JSON instead of writing it")
    args = ap.parse_args(argv)

    cfg = render(args.profile, args.headless)
    if args.print:
        json.dump(cfg, sys.stdout, indent=2)
        print()
        return 0
    out = args.out or default_out(args.profile, args.headless)
    write_atomic(out, cfg)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
