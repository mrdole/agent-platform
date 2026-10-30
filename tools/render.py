#!/usr/bin/env python3
"""Render the OpenCode config for a profile: core + profile (+ headless overlays).

    render.py PROFILE [--headless] [--out PATH] [--print]

Writes the merged config (default ~/.cache/agent-platform/<profile>[.headless].opencode.json)
and prints its path, which the `agent` launcher passes to OpenCode as OPENCODE_CONFIG.

Merge rules (see BLUEPRINT.md section 4):
  * dicts merge recursively; new keys are appended, existing keys keep their position, so
    permission rule order ("*" first, specific rules after) survives the merge
  * `instructions` lists are concatenated (core first); other lists are replaced
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
CONCAT_KEYS = {"instructions"}


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
    for path, base in layers:
        cfg = deep_merge(cfg, resolve_refs(load_json(path), base))
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
