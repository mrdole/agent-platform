#!/usr/bin/env python3
"""Check that this machine has everything agent-platform needs, and say how to fix what's missing.

    agent doctor              (or: python3 tools/doctor.py)
    agent doctor --network    also contact the model server and GitLab

Exit 1 if anything required is missing. Optional items only warn.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

AP_HOME = Path(os.environ.get("AP_HOME") or Path(__file__).resolve().parent.parent)
FIX_INSTALL = "re-run ./bootstrap.sh"


def load_env() -> dict[str, str]:
    return _env_files()


def _env_files() -> dict[str, str]:
    """Merge platform.env, site.env, secrets.env and the real environment (same order as `agent`)."""
    merged: dict[str, str] = {}
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-platform"
    for path in [AP_HOME / "core/platform.env", Path("/etc/agent-platform/site.env"), cfg / "site.env",
                 Path("/etc/agent-platform/secrets.env"), cfg / "secrets.env"]:
        if path.is_file() and os.access(path, os.R_OK):
            for line in path.read_text().splitlines():
                m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
                if m:
                    merged[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    merged.update(os.environ)
    return merged


def _version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return ""


def checks(env: dict[str, str], network: bool = False) -> list[tuple[str, bool, bool, str, str]]:
    """Return (name, ok, required, detail, fix)."""
    results = []
    add = lambda name, ok, req, detail, fix: results.append((name, ok, req, detail, fix))  # noqa: E731

    git = _version(["git", "--version"])
    gv = tuple(int(x) for x in re.findall(r"(\d+)\.(\d+)", git)[0]) if git else (0, 0)
    add("git >= 2.31", gv >= (2, 31), True, git or "not found", "sudo dnf install git")

    venv = Path(env.get("AP_VENV") or AP_HOME / "venv")
    vpy = venv / "bin" / "python"
    vver = _version([str(vpy), "--version"]) if vpy.exists() else ""
    vnum = tuple(int(x) for x in re.findall(r"(\d+)\.(\d+)", vver)[0]) if vver else (0, 0)
    add("platform venv (Python >= 3.10)", vnum >= (3, 10), True,
        f"{venv}: {vver or 'missing'}", FIX_INSTALL + " (needs python3.12 from AppStream)")
    mcp_ok = bool(vver) and subprocess.run([str(vpy), "-c", "import mcp, yaml"], capture_output=True).returncode == 0
    add("MCP SDK + PyYAML in venv", mcp_ok, True, "importable" if mcp_ok else "not importable", FIX_INSTALL)

    oc = _version(["opencode", "--version"])
    add("opencode on PATH", bool(oc), True, oc or "not found", FIX_INSTALL + " (opencode_install: npm)")
    add("flock (bench-lock)", bool(shutil.which("flock")), True, shutil.which("flock") or "not found",
        "sudo dnf install util-linux")

    site = [p for p in (Path("/etc/agent-platform/site.env"),
                        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-platform/site.env")
            if p.is_file()]
    add("site.env", bool(site), True, str(site[0]) if site else "missing", FIX_INSTALL)
    url = env.get("AP_LLM_BASE_URL", "")
    add("model server URL set", bool(url) and "example.internal" not in url, True, url or "unset",
        "set ap_llm_base_url in ~/.config/agent-platform/site.yml, " + FIX_INSTALL)
    add("CORP_LLM_KEY", bool(env.get("CORP_LLM_KEY")), True, "set" if env.get("CORP_LLM_KEY") else "missing",
        "add CORP_LLM_KEY=... to ~/.config/agent-platform/secrets.env")
    kroot = env.get("AP_KNOWLEDGE_ROOT", "")
    add("lab-knowledge checkout", bool(kroot) and Path(kroot, "docs").is_dir(), True, kroot or "unset",
        "clone lab-knowledge there, or set ap_knowledge_repo_url / ap_knowledge_root in site.yml")
    ca = env.get("NODE_EXTRA_CA_CERTS", "")
    add("CA bundle for Node (OpenCode)", bool(ca) and Path(ca).is_file(), True, ca or "unset", FIX_INSTALL)

    add("glab (pipeline/it-debug skills, MRs)", bool(shutil.which("glab")), False,
        _version(["glab", "--version"]) or "not found", "set glab_rpm_url in site.yml, " + FIX_INSTALL)
    add("podman (firmware builds)", bool(shutil.which("podman")), False,
        _version(["podman", "--version"]) or "not found", "sudo dnf install podman")
    personal = env.get("AP_KNOWLEDGE_PERSONAL", "")
    has_remote = bool(personal) and bool(_version(["git", "-C", personal, "remote", "get-url", "origin"]))
    add("personal notes repo (kn)", bool(personal) and Path(personal, "docs").is_dir(), False,
        f"{personal or 'unset'}{'' if has_remote or not personal else ' (no GitLab remote: not backed up)'}",
        "kn init ~/repos/knowledge, then set ap_personal_knowledge_root in site.yml")
    tokens = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-platform/bot-tokens.json"
    add("GitLab bot tokens", tokens.is_file(), False, str(tokens) if tokens.is_file() else "none yet",
        "python3 tools/gitlab_bots.py apply (see docs/security.md)")
    add("ansible-core (to re-run setup)", bool(shutil.which("ansible-playbook")), False,
        _version(["ansible-playbook", "--version"]) or "not found", "sudo dnf install ansible-core")

    if network:
        if url:
            code, detail = _http(url.rstrip("/") + "/models", env.get("CORP_LLM_KEY", ""))
            add("model server reachable", code == 200, True, detail,
                "see lab-knowledge docs/network/llm-server-access.md (proxy, NO_PROXY, CA)")
        host = env.get("GITLAB_HOST", "")
        if host:
            code, detail = _http(f"https://{host}/api/v4/version", "")
            add("GitLab reachable", code in (200, 401), True, detail, "check proxy / NO_PROXY / CA for the GitLab host")
    return results


def _http(url: str, key: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, f"{url} -> {resp.status}"
    except urllib.error.HTTPError as exc:
        return exc.code, f"{url} -> HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return 0, f"{url} -> {getattr(exc, 'reason', exc)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", action="store_true", help="also contact the model server and GitLab")
    args = ap.parse_args(argv)
    failed = 0
    for name, ok, required, detail, fix in checks(_env_files(), args.network):
        mark = "ok  " if ok else ("FAIL" if required else "warn")
        print(f"[{mark}] {name:38} {detail}")
        if not ok:
            print(f"       fix: {fix}")
            failed += required
    print(f"doctor: {'all required checks passed' if not failed else f'{failed} required check(s) failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
