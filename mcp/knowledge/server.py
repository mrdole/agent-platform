#!/usr/bin/env python3
"""MCP server (stdio) exposing a lab-knowledge checkout to agents.

Env:
  KNOWLEDGE_ROOT        path to the lab-knowledge checkout (required)
  KNOWLEDGE_PROFILE     profile name; docs/devices are filtered by applies_to
  KNOWLEDGE_STALE_DAYS  docs verified longer ago than this are flagged stale (default 180)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import knowledge_core  # noqa: E402
from knowledge_core import DEFAULT_STALE_DAYS, KnowledgeStore  # noqa: E402

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

ROOT = os.environ.get("KNOWLEDGE_ROOT", "")
if not ROOT or not Path(ROOT).is_dir():
    print(f"knowledge server: KNOWLEDGE_ROOT '{ROOT}' is not a directory", file=sys.stderr)
    sys.exit(2)

store = KnowledgeStore(
    ROOT,
    profile=os.environ.get("KNOWLEDGE_PROFILE") or None,
    stale_days=int(os.environ.get("KNOWLEDGE_STALE_DAYS") or DEFAULT_STALE_DAYS),
    personal=os.environ.get("KNOWLEDGE_PERSONAL") or None,
)

mcp = Server(
    "knowledge",
    instructions=(
        "Lab knowledge: environment facts, gotchas, how-tos, lab inventory and the skills catalog. "
        "Search before guessing. Docs with status 'draft' or stale=true are hints to confirm, not facts. "
        "Cite the doc path you relied on."
    ),
)


@mcp.tool()
def search_docs(query: str, k: int = 5, source: str | None = None) -> list[dict]:
    """Search knowledge docs (gotchas, how-tos, references, policies, personal notes) by symptom,
    error text or system name. Returns path, source (team or personal), title, kind, status,
    stale flag and a snippet. source='team' or 'personal' restricts the search. Personal notes
    are the user's own unreviewed notes: hints, never facts. Read a result with get_doc before
    acting on it."""
    return store.search_docs(query, k, source)


@mcp.tool()
def get_doc(path: str) -> dict:
    """Read one doc by the path search_docs returned ('network/llm-server-access.md' for team docs,
    'personal:inbox/some-note.md' for personal notes)."""
    return store.get_doc(path)


@mcp.tool()
def list_docs(kind: str | None = None, tag: str | None = None, source: str | None = None) -> list[dict]:
    """List docs, optionally filtered by kind (gotcha, howto, reference, policy, note, task),
    tag, or source (team, personal)."""
    return store.list_docs(kind, tag, source)


@mcp.tool()
def list_devices(bench: str | None = None) -> list[dict]:
    """List lab instruments and boards from inventory, optionally for one bench."""
    return store.list_devices(bench)


@mcp.tool()
def get_device(name: str) -> dict:
    """Get one device from inventory (VISA address, model, bench details). Always use this for
    addresses; never use addresses from memory or logs."""
    return store.get_device(name)


@mcp.tool()
def find_skill(query: str, k: int = 5) -> list[dict]:
    """Search the skills catalog (every SKILL.md indexed across the GitLab group) to see whether
    a skill for a task already exists, and where."""
    return store.find_skill(query, k)


@mcp.tool()
def list_models(location: str | None = None, good_for: str | None = None) -> list[dict]:
    """List models from the model registry: id, modalities, tool-calling ability, what each is good
    and bad for, platform role. Filter by location (model server, e.g. 'corp-llm') or a task tag."""
    return knowledge_core.list_models(ROOT, location, good_for)


@mcp.tool()
def recommend_model(task: str, location: str | None = None, modality: str | None = None, k: int = 3) -> dict:
    """Rank available models for a task described in plain words (e.g. 'review a firmware diff',
    'read a spectrum analyzer screenshot', 'write SQL'). Optional location (model server) and
    input modality (text, image, video, audio). Returns reasons and whether the ranking is from
    vendor priors or our own evals."""
    return knowledge_core.recommend_models(ROOT, task, location, modality, k)


if __name__ == "__main__":
    mcp.run()
