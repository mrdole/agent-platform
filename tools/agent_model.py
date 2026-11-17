#!/usr/bin/env python3
"""Look up an agent's model in a rendered OpenCode config (G22).

    agent_model.py <agent> [--config FILE]     # default FILE: $OPENCODE_CONFIG

Prints the model with `{env:VAR}` resolved from the environment, e.g. `corp/laguna-5.2.1`.
Exits 1 (prints nothing) if the agent has no model or a variable is unset.

Why: OpenCode 2.0.27's `run --agent <primary>` ignores that agent's own `model` and uses the
top-level `model`, so the `agent` launcher passes `--model` explicitly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_env(value: str, env=None) -> str | None:
    env = os.environ if env is None else env
    missing = []

    def sub(m):
        if not env.get(m.group(1)):
            missing.append(m.group(1))
        return env.get(m.group(1), "")
    out = ENV_REF.sub(sub, value)
    return None if missing else out


def agent_model(cfg: dict, agent: str, env=None) -> str | None:
    """The agent's model as `provider/model[#variant]`, or None if unset/unresolvable."""
    block = (cfg.get("agent") or {}).get(agent)
    model = block.get("model") if isinstance(block, dict) else None
    if not isinstance(model, str) or not model:
        return None
    return resolve_env(model, env)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("agent")
    ap.add_argument("--config", default=os.environ.get("OPENCODE_CONFIG"))
    args = ap.parse_args(argv)
    if not args.config:
        return 1
    try:
        with open(args.config, encoding="utf-8") as fh:
            model = agent_model(json.load(fh), args.agent)
    except (OSError, json.JSONDecodeError):
        return 1
    if not model:
        return 1
    print(model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
