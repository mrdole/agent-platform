#!/usr/bin/env python3
"""MCP server (stdio) for lab bench instruments, with guard rails in bench_core.py.

Env:
  KNOWLEDGE_ROOT       lab-knowledge checkout (inventory/devices.yaml) - required
  BENCH_NAME           restrict to one bench (recommended)
  BENCH_ALLOW_WRITE    "1" to allow allowlisted writes this session; anything else = read-only
  BENCH_VISA_BACKEND   pyvisa backend, default "@py" (pyvisa-py); "path/sim.yaml@sim" for tests
  BENCH_AUDIT_LOG      JSON-lines audit log path (default ~/.local/state/agent-platform/bench-audit.jsonl)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_core import Bench  # noqa: E402

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

if not os.environ.get("KNOWLEDGE_ROOT"):
    print("bench server: KNOWLEDGE_ROOT is required", file=sys.stderr)
    sys.exit(2)

bench = Bench.from_env()
mode = "allowlisted writes enabled" if bench.allow_write else "read-only"
mcp = Server(
    "bench",
    instructions=(
        f"Lab bench instruments ({mode}). Identify instruments before use. query() takes a single SCPI "
        "query ending in '?'. write() only accepts commands on the device's allowlist and only when the "
        "operator enabled writes. Never change RF output state or levels without explicit approval. "
        "Every call is audit-logged."
    ),
)


@mcp.tool()
def list_devices() -> list[dict]:
    """List instruments on this bench: name, kind, model, VISA address, read_only."""
    return [{k: d.get(k) for k in ("name", "kind", "model", "bench", "visa", "read_only")} for d in bench.devices()]


@mcp.tool()
def identify(device: str) -> dict:
    """Send *IDN? to a device and return its identification string."""
    return bench.identify(device)


@mcp.tool()
def query(device: str, command: str) -> dict:
    """Send one SCPI query (header ends in '?', e.g. 'FREQ:CENT?' or 'MEAS:VOLT:DC? 10,0.001')."""
    return bench.query(device, command)


@mcp.tool()
def write(device: str, command: str) -> dict:
    """Send one SCPI configuration command. Rejected unless writes are enabled for this session,
    the device is not read_only, and the command matches its write_allowlist."""
    return bench.write(device, command)


if __name__ == "__main__":
    mcp.run()
