import shutil
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for sub in ("tools", "mcp/knowledge", "mcp/bench", "evals"):
    sys.path.insert(0, str(ROOT / sub))


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def knowledge_root(tmp_path: Path) -> Path:
    """A small, self-contained lab-knowledge checkout."""
    k = tmp_path / "lab-knowledge"
    (k / "docs" / "gotchas").mkdir(parents=True)
    (k / "docs" / "network").mkdir(parents=True)
    (k / "docs" / "it").mkdir(parents=True)
    (k / "inventory").mkdir()
    (k / "catalog").mkdir()
    (k / "docs" / "gotchas" / "opencode-headless-ask.md").write_text(textwrap.dedent("""\
        ---
        title: OpenCode headless runs auto-approve ask permissions
        kind: gotcha
        applies_to: [all]
        status: verified
        owner: platform
        last_verified: 2026-10-01
        tags: [opencode, ci]
        ---
        ## Cause
        opencode run --auto approves anything not explicitly denied.
        """))
    (k / "docs" / "network" / "llm-server-access.md").write_text(textwrap.dedent("""\
        ---
        title: Reaching the LLM server through the proxy
        kind: reference
        applies_to: [all]
        status: draft
        owner: team
        tags: [network, proxy]
        ---
        Set NODE_EXTRA_CA_CERTS so OpenCode trusts the corporate CA.
        Add the LLM host to NO_PROXY.
        """))
    (k / "docs" / "it" / "core-switch-layout.md").write_text(textwrap.dedent("""\
        ---
        title: Core switch layout
        kind: reference
        applies_to: [it-debug]
        status: verified
        owner: it
        last_verified: 2026-09-01
        ---
        VLAN 40 carries the lab subnet.
        """))
    (k / "docs" / "_template.md").write_text("---\ntitle: x\n---\nignored\n")
    (k / "inventory" / "benches.yaml").write_text("benches:\n  - name: bench-a\n    runner_tag: bench-a\n")
    (k / "inventory" / "devices.yaml").write_text(textwrap.dedent("""\
        devices:
          - name: sa-01
            kind: spectrum_analyzer
            bench: bench-a
            visa: "TCPIP0::192.0.2.10::inst0::INSTR"
            read_only: true
          - name: sg-01
            kind: signal_generator
            bench: bench-a
            visa: "TCPIP0::192.0.2.11::inst0::INSTR"
            read_only: false
            write_allowlist: [FREQ, POW]
            termination: "\\n"
          - name: board-a
            kind: dut
            bench: bench-a
        """))
    (k / "catalog" / "skills.json").write_text(
        '{"skills": [{"name": "bench-flash", "description": "Flash a firmware image to a board on a lab bench", '
        '"project": "tools/agent-platform", "path": "skills/bench-flash/SKILL.md"}, '
        '{"name": "pipeline-debug-ci", "description": "Diagnose failing GitLab CI pipelines", '
        '"project": "tools/agent-platform", "path": "skills/pipeline-debug-ci/SKILL.md"}]}')
    return k


@pytest.fixture
def platform_copy(tmp_path: Path) -> Path:
    """A writable copy of the platform for tests that break things on purpose."""
    dest = tmp_path / "agent-platform"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "venv"))
    return dest
