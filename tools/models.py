#!/usr/bin/env python3
"""Model lookup per location (model server), backed by lab-knowledge models/registry.yaml.

    models.py list      [--location L] [--good-for TAG]
    models.py recommend "task in plain words" [--location L] [--modality image]
    models.py probe     --location L       # ask the server's /models and diff with the registry
    models.py provider-json --location L   # OpenCode provider "models" block for core/opencode.core.json

The registry path comes from --knowledge or $AP_KNOWLEDGE_ROOT. Location base URLs and keys are
read from the env vars the registry names (base_url_env / api_key_env), e.g. AP_LLM_BASE_URL and
CORP_LLM_KEY from site.env / secrets.env (run it through `agent`'s environment or export them).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp" / "knowledge"))
import knowledge_core as kc  # noqa: E402


def location(root: Path, name: str) -> dict:
    for loc in kc._load_registry(root)["locations"]:
        if loc.get("name") == name:
            return loc
    raise SystemExit(f"models: unknown location '{name}'")


def _urlopen(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def served_ids(loc: dict, opener=_urlopen) -> list[str]:
    if loc.get("access", "api") != "api":
        raise SystemExit(f"models: {loc['name']} is interactive-only; there is no /models endpoint to probe")
    base =loc.get("base_url") or os.environ.get(loc.get("base_url_env", ""), "")
    if not base:
        raise SystemExit(f"models: no base URL for {loc['name']} (set {loc.get('base_url_env')})")
    headers = {}
    key = os.environ.get(loc.get("api_key_env", ""), "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        data = json.loads(opener(base.rstrip("/") + "/models", headers))
    except urllib.error.URLError as exc:
        raise SystemExit(f"models: cannot reach {base}: {exc}") from None
    return sorted(m["id"] for m in data.get("data", []) if "id" in m)


def probe(root: Path, name: str, opener=_urlopen) -> dict:
    served = set(served_ids(location(root, name), opener))
    registered = {m["id"] for m in kc.list_models(root, name)}
    return {"location": name, "ok": sorted(served & registered),
            "registered_not_served": sorted(registered - served),
            "served_not_registered": sorted(served - registered)}


def provider_models(root: Path, name: str) -> dict:
    return {m["id"]: {"name": m.get("name", m["id"])} for m in kc.list_models(root, name)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--knowledge", type=Path, default=os.environ.get("AP_KNOWLEDGE_ROOT"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--location")
    ls.add_argument("--good-for")
    rc = sub.add_parser("recommend")
    rc.add_argument("task")
    rc.add_argument("--location")
    rc.add_argument("--modality")
    rc.add_argument("-k", type=int, default=3)
    pr = sub.add_parser("probe")
    pr.add_argument("--location", required=True)
    pj = sub.add_parser("provider-json")
    pj.add_argument("--location", required=True)
    args = ap.parse_args(argv)
    if not args.knowledge:
        print("models: pass --knowledge or set AP_KNOWLEDGE_ROOT", file=sys.stderr)
        return 2
    root = Path(args.knowledge)

    if args.cmd == "list":
        for m in kc.list_models(root, args.location, args.good_for):
            mods = "+".join(m.get("modalities", ["text"]))
            print(f"{m['id']:28} {mods:22} tools:{m.get('tool_calling', '?'):7} {', '.join(m.get('good_for', []))}")
        return 0
    if args.cmd == "recommend":
        res = kc.recommend_models(root, args.task, args.location, args.modality, args.k)
        print(f"task tags: {', '.join(res['matched_tags']) or '(none)'}")
        for r in res["results"]:
            print(f"  {r['id']:28} score {r['score']:5}  {r['why']}")
        if res["interactive_only"]:
            print("  interactive only (people, not agents): "
                  + ", ".join(f"{a['id']} ({'/'.join(a['locations'])})" for a in res["interactive_only"]))
        if res["hint"]:
            print(res["hint"])
        return 0
    if args.cmd == "probe":
        res = probe(root, args.location)
        print(json.dumps(res, indent=2))
        return 1 if res["registered_not_served"] else 0
    print(json.dumps({"models": provider_models(root, args.location)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
