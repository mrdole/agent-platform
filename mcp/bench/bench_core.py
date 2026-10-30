"""Bench instrument access with guard rails. No MCP dependency (server.py wraps this).

Policy (enforced here, not just in prompts):
  * query(): only SCPI queries - the command header ends in '?' - and no ';' or newlines,
    so a write can't be smuggled after a query.
  * write(): only when BENCH_ALLOW_WRITE=1, the device is not read_only, and the command's
    SCPI header (first token, e.g. 'FREQ' in 'FREQ 1GHZ') exactly matches an entry in the
    device's write_allowlist (from lab-knowledge inventory). Short and long forms are
    different headers; list both if needed.
  * every call is appended to an audit log (JSON lines).
Device definitions come from lab-knowledge inventory/devices.yaml; see its BLUEPRINT.md.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

import yaml

FORBIDDEN = re.compile(r"[;\r\n]")


class BenchPolicyError(Exception):
    pass


def normalize(cmd: str) -> str:
    return " ".join(cmd.strip().split())


def is_query(cmd: str) -> bool:
    if FORBIDDEN.search(cmd):
        return False
    c = normalize(cmd)
    return bool(c) and c.split(" ", 1)[0].endswith("?")


def write_allowed(device: dict, cmd: str, allow_write: bool) -> tuple[bool, str]:
    if not allow_write:
        return False, "writes are disabled (set AP_BENCH_ALLOW_WRITE=1 for this session)"
    if device.get("read_only", True):
        return False, f"device {device.get('name')} is read_only in inventory"
    if FORBIDDEN.search(cmd):
        return False, "multiple commands (';' or newline) are not allowed"
    header = normalize(cmd).upper().split(" ", 1)[0]
    if header.endswith("?"):
        return False, "that is a query; use query()"
    allowed = {normalize(h).upper() for h in device.get("write_allowlist") or []}
    if header in allowed:
        return True, ""
    return False, f"header '{header}' not in write_allowlist for {device.get('name')}"


class Bench:
    def __init__(self, knowledge_root: str | Path, bench: str | None = None, allow_write: bool = False,
                 backend: str = "@py", audit_log: str | Path | None = None, timeout_ms: int = 5000,
                 rm=None):
        self.root = Path(knowledge_root)
        self.bench = bench or None
        self.allow_write = allow_write
        self.backend = backend or "@py"
        default_log = Path.home() / ".local/state/agent-platform/bench-audit.jsonl"
        self.audit_log = Path(audit_log) if audit_log else default_log
        self.timeout_ms = timeout_ms
        self._rm = rm

    @classmethod
    def from_env(cls) -> "Bench":
        return cls(
            knowledge_root=os.environ["KNOWLEDGE_ROOT"],
            bench=os.environ.get("BENCH_NAME") or None,
            allow_write=os.environ.get("BENCH_ALLOW_WRITE") == "1",
            backend=os.environ.get("BENCH_VISA_BACKEND") or "@py",
            audit_log=os.environ.get("BENCH_AUDIT_LOG") or None,
        )

    # ---------- inventory ----------
    def devices(self) -> list[dict]:
        path = self.root / "inventory" / "devices.yaml"
        data = yaml.safe_load(path.read_text()) if path.is_file() else {}
        devs = [d for d in (data or {}).get("devices") or [] if isinstance(d, dict)]
        return [d for d in devs if self.bench is None or d.get("bench") == self.bench]

    def device(self, name: str) -> dict:
        for d in self.devices():
            if d.get("name") == name:
                return d
        scope = f" on bench {self.bench}" if self.bench else ""
        raise BenchPolicyError(f"no device named {name}{scope}")

    # ---------- VISA ----------
    @property
    def rm(self):
        if self._rm is None:
            import pyvisa
            self._rm = pyvisa.ResourceManager(self.backend)
        return self._rm

    def _open(self, dev: dict):
        inst = self.rm.open_resource(dev["visa"])
        inst.timeout = int(dev.get("timeout_ms", self.timeout_ms))
        term = dev.get("termination")
        if term:
            inst.read_termination = inst.write_termination = term.encode().decode("unicode_escape")
        return inst

    def _audit(self, action: str, device: str, command: str, ok: bool, result: str) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "user": os.environ.get("USER", ""), "profile": os.environ.get("KNOWLEDGE_PROFILE", ""),
            "bench": self.bench, "device": device, "action": action, "command": command,
            "ok": ok, "result": result[:500],
        }
        with self.audit_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    # ---------- operations ----------
    def identify(self, name: str) -> dict:
        return {"device": name, "idn": self.query(name, "*IDN?")["response"]}

    def query(self, name: str, command: str) -> dict:
        dev = self.device(name)
        if not is_query(command):
            self._audit("query", name, command, False, "rejected: not a single query")
            raise BenchPolicyError("query() only accepts a single SCPI query (header ending in '?')")
        inst = self._open(dev)
        try:
            resp = str(inst.query(normalize(command))).strip()
        finally:
            inst.close()
        self._audit("query", name, command, True, resp)
        return {"device": name, "command": normalize(command), "response": resp}

    def write(self, name: str, command: str) -> dict:
        dev = self.device(name)
        ok, why = write_allowed(dev, command, self.allow_write)
        if not ok:
            self._audit("write", name, command, False, f"rejected: {why}")
            raise BenchPolicyError(why)
        inst = self._open(dev)
        try:
            inst.write(normalize(command))
        finally:
            inst.close()
        self._audit("write", name, command, True, "sent")
        return {"device": name, "command": normalize(command), "sent": True}
