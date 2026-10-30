#!/usr/bin/env python3
"""Knowledge store logic for a lab-knowledge checkout: load docs, inventory and the skills
catalog; search them; validate the repo. No MCP dependency (server.py wraps this).

    knowledge_core.py validate ROOT [--profiles-from AP_HOME/profiles] [--stale-days N]
    knowledge_core.py search ROOT "query" [--profile P] [-k N]
    knowledge_core.py skills ROOT "query" [-k N]

Schema: lab-knowledge/BLUEPRINT.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KINDS = {"gotcha", "howto", "reference", "policy"}
STATUSES = {"draft", "verified"}
REQUIRED = ("title", "kind", "applies_to", "status", "owner")
DEFAULT_STALE_DAYS = 180
DEFAULT_DEVICE_PROFILES = ["bench", "firmware"]
DOC_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.md$")
TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
SECRET_PATTERNS = [
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{24,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}"),
]


def tokens(text: str) -> list[str]:
    out = []
    for tok in TOKEN.findall(text.lower()):
        out.append(tok)
        parts = re.split(r"[._-]", tok)
        if len(parts) > 1:
            out.extend(p for p in parts if p)
    return out


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    meta = yaml.safe_load(text[4:end]) or {}
    return meta, text[end + 4:].lstrip("\n")


def _as_date(value) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


@dataclass
class Doc:
    path: str
    meta: dict
    body: str
    stale: bool = False
    source: str = "team"      # team | personal

    @property
    def ref(self) -> str:
        """What get_doc accepts: team docs by path, personal docs as 'personal:<path>'."""
        return self.path if self.source == "team" else f"{self.source}:{self.path}"

    @property
    def title(self) -> str:
        return str(self.meta.get("title", self.path))

    @property
    def applies_to(self) -> list[str]:
        v = self.meta.get("applies_to") or []
        return [str(x) for x in (v if isinstance(v, list) else [v])]

    def visible_to(self, profile: str | None) -> bool:
        return profile is None or "all" in self.applies_to or profile in self.applies_to

    def summary(self) -> dict:
        lv = _as_date(self.meta.get("last_verified"))
        out = {
            "path": self.ref,
            "source": self.source,
            "title": self.title,
            "kind": self.meta.get("kind"),
            "status": self.meta.get("status"),
            "stale": self.stale,
            "last_verified": lv.isoformat() if lv else None,
            "tags": self.meta.get("tags") or [],
        }
        if self.meta.get("_also_in_team"):
            out["also_in_team"] = True
        return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, ln in zip(self.tf, self.len):
            s = 0.0
            for t in set(query):
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / (self.avg or 1)))
            out.append(s)
        return out


def _snippet(body: str, qtok: set[str], width: int = 240) -> str:
    best, best_score = "", -1
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        score = len(qtok & set(tokens(line)))
        if score > best_score:
            best, best_score = line, score
    return best if len(best) <= width else best[: width - 3] + "..."


class KnowledgeStore:
    """Team knowledge (lab-knowledge: docs, inventory, catalog, models) plus an optional personal
    notes repo (docs only, always treated as hints). Search uses an in-memory SQLite FTS5 index
    built from the Markdown files (stemming, ranked, highlighted snippets); if FTS5 is missing it
    falls back to the pure-Python BM25 ranking. The files in git are the source of truth."""

    def __init__(self, root: str | Path, profile: str | None = None,
                 stale_days: int = DEFAULT_STALE_DAYS, today: dt.date | None = None,
                 personal: str | Path | None = None):
        self.root = Path(root).resolve()
        self.personal = Path(personal).resolve() if personal and Path(personal).is_dir() else None
        self.profile = profile or None
        self.stale_days = stale_days
        self.today = today
        self._sig = None
        self._fts = None
        self.docs: list[Doc] = []
        self.load_errors: list[str] = []
        self.devices: list[dict] = []
        self.benches: list[dict] = []
        self.skills: list[dict] = []

    def _roots(self) -> list[tuple[str, Path]]:
        return [("team", self.root)] + ([("personal", self.personal)] if self.personal else [])

    # ---------- loading ----------
    def _signature(self):
        files = [p for sub in ("docs", "inventory", "catalog", "models") for p in (self.root / sub).rglob("*")
                 if p.is_file()]
        if self.personal:
            files += [p for p in (self.personal / "docs").rglob("*") if p.is_file()]
        return len(files), max((p.stat().st_mtime_ns for p in files), default=0)

    def _load_docs(self, source: str, root: Path, today: dt.date) -> None:
        docs_dir = root / "docs"
        if not docs_dir.is_dir():
            return
        for path in sorted(docs_dir.rglob("*.md")):
            rel = path.relative_to(docs_dir).as_posix()
            if any(part.startswith("_") for part in rel.split("/")):
                continue
            label = f"{source}:docs/{rel}"
            try:
                meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                self.load_errors.append(f"{label}: bad frontmatter: {exc}")
                continue
            if source == "team":
                if meta is None:
                    self.load_errors.append(f"{label}: missing frontmatter")
                    continue
                lv = _as_date(meta.get("last_verified"))
                stale = lv is None or (today - lv).days > self.stale_days
            else:  # personal notes: frontmatter optional, never authoritative
                meta = {"kind": "note", "applies_to": ["all"], **(meta or {}), "status": "personal"}
                if not meta.get("title"):
                    heading = next((ln.lstrip("# ").strip() for ln in body.splitlines() if ln.startswith("#")), "")
                    meta["title"] = heading or path.stem.replace("-", " ")
                stale = False
            self.docs.append(Doc(rel, meta, body, stale, source))

    def _build_fts(self) -> None:
        self._fts = None
        try:
            con = sqlite3.connect(":memory:")
            con.execute("CREATE VIRTUAL TABLE fts USING fts5(title, tags, body, tokenize='porter unicode61')")
            con.executemany("INSERT INTO fts(rowid, title, tags, body) VALUES (?, ?, ?, ?)", [
                (i + 1, d.title, " ".join(map(str, d.meta.get("tags") or [])), d.body)
                for i, d in enumerate(self.docs)])
            self._fts = con
        except sqlite3.OperationalError:  # SQLite built without FTS5
            self._fts = None

    def refresh(self) -> None:
        sig = self._signature()
        if sig == self._sig:
            return
        self._sig = sig
        today = self.today or dt.date.today()
        self.docs, self.load_errors = [], []
        for source, root in self._roots():
            self._load_docs(source, root, today)
        team_paths = {d.path for d in self.docs if d.source == "team"}
        for d in self.docs:
            if d.source != "team" and d.path in team_paths:
                d.meta["_also_in_team"] = True
        self._index = BM25([
            tokens(d.title) * 3 + tokens(" ".join(map(str, d.meta.get("tags") or []))) * 2 + tokens(d.body)
            for d in self.docs
        ])
        self._build_fts()
        self.devices = self._yaml_list("inventory/devices.yaml", "devices")
        self.benches = self._yaml_list("inventory/benches.yaml", "benches")
        cat = self.root / "catalog" / "skills.json"
        self.skills = json.loads(cat.read_text()).get("skills", []) if cat.is_file() else []
        self._skill_index = BM25([
            tokens(s.get("name", "")) * 3 + tokens(s.get("description", "")) + tokens(s.get("project", ""))
            for s in self.skills
        ])

    def _yaml_list(self, rel: str, key: str) -> list[dict]:
        path = self.root / rel
        if not path.is_file():
            return []
        data = yaml.safe_load(path.read_text()) or {}
        return [d for d in (data.get(key) or []) if isinstance(d, dict)]

    # ---------- docs ----------
    def _candidates(self, q: list[str], limit: int) -> list[tuple[Doc, float, str | None]]:
        if self._fts is not None:
            match = " OR ".join(f'"{t}"' for t in dict.fromkeys(q))
            try:
                rows = self._fts.execute(
                    "SELECT rowid, -bm25(fts, 10.0, 5.0, 1.0), snippet(fts, 2, '[', ']', '...', 20) "
                    "FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 10.0, 5.0, 1.0) LIMIT ?", (match, limit)).fetchall()
                return [(self.docs[rowid - 1], score, snip or None) for rowid, score, snip in rows]
            except sqlite3.OperationalError:
                pass
        return [(d, s, None) for d, s in zip(self.docs, self._index.scores(q)) if s > 0]

    @property
    def search_engine(self) -> str:
        self.refresh()
        return "sqlite-fts5" if self._fts is not None else "bm25"

    def search_docs(self, query: str, k: int = 5, source: str | None = None) -> list[dict]:
        self.refresh()
        q = tokens(query)
        if not q:
            return []
        scored = []
        for doc, score, snip in self._candidates(q, max(50, k * 10)):
            if not doc.visible_to(self.profile) or (source and doc.source != source):
                continue
            if doc.source != "team":
                score *= 0.8          # personal notes rank below team docs
            elif doc.meta.get("status") != "verified":
                score *= 0.9
            scored.append((score, doc, snip))
        scored.sort(key=lambda x: -x[0])
        # FTS ranks; the snippet is the body line that best matches (more readable than FTS fragments)
        return [{**d.summary(), "score": round(s, 3), "snippet": _snippet(d.body, set(q)) or (snip or "")}
                for s, d, snip in scored[:k]]

    def get_doc(self, path: str) -> dict:
        self.refresh()
        ref = path.strip()
        sources = ["team", "personal"]
        head, sep, rest = ref.partition(":")
        if sep and head in ("team", "personal"):
            sources, ref = [head], rest
        rel = ref.removeprefix("docs/").lstrip("/")
        for source in sources:
            root = dict(self._roots()).get(source)
            if root is None:
                continue
            target = (root / "docs" / rel).resolve()
            if not str(target).startswith(str(root / "docs") + "/"):
                raise ValueError("path must be inside docs/")
            for doc in self.docs:
                if doc.source == source and doc.path == rel:
                    if not doc.visible_to(self.profile):
                        raise PermissionError(f"{rel} is not available to profile {self.profile}")
                    meta = {k: v for k, v in doc.meta.items() if not k.startswith("_")}
                    return {**doc.summary(), "meta": _jsonable(meta), "body": doc.body}
        raise FileNotFoundError(f"no doc {path}")

    def list_docs(self, kind: str | None = None, tag: str | None = None, source: str | None = None) -> list[dict]:
        self.refresh()
        return [d.summary() for d in self.docs
                if d.visible_to(self.profile)
                and (kind is None or d.meta.get("kind") == kind)
                and (tag is None or tag in (d.meta.get("tags") or []))
                and (source is None or d.source == source)]

    # ---------- inventory ----------
    def _device_visible(self, dev: dict) -> bool:
        allowed = dev.get("applies_to") or DEFAULT_DEVICE_PROFILES
        return self.profile is None or "all" in allowed or self.profile in allowed

    def list_devices(self, bench: str | None = None) -> list[dict]:
        self.refresh()
        return [_jsonable(d) for d in self.devices
                if self._device_visible(d) and (bench is None or d.get("bench") == bench)]

    def get_device(self, name: str) -> dict:
        self.refresh()
        for dev in self.devices:
            if dev.get("name") == name:
                if not self._device_visible(dev):
                    raise PermissionError(f"device {name} is not available to profile {self.profile}")
                bench = next((b for b in self.benches if b.get("name") == dev.get("bench")), None)
                return {**_jsonable(dev), "bench_info": _jsonable(bench) if bench else None}
        raise KeyError(f"no device named {name}")

    # ---------- skills ----------
    def find_skill(self, query: str, k: int = 5) -> list[dict]:
        self.refresh()
        q = tokens(query)
        if not q or not self.skills:
            return []
        scored = sorted(zip(self._skill_index.scores(q), self.skills), key=lambda x: -x[0])
        return [{**s, "score": round(sc, 3)} for sc, s in scored[:k] if sc > 0]


# ---------- models ----------
# Controlled vocabulary for good_for / avoid_for, with words that map a free-text task onto it.
TASK_TAGS: dict[str, list[str]] = {
    "planning": ["plan", "planning", "spec", "acceptance", "breakdown", "decompose", "design"],
    "orchestration": ["orchestrat", "lead", "delegate", "coordinate", "multi-step", "agent loop", "agentic"],
    "coding": ["code", "coding", "implement", "fix", "bug", "refactor", "firmware", "patch", "write function"],
    "code-review": ["review", "diff", "merge request", "audit"],
    "tool-calling": ["tool", "function call", "api call", "mcp"],
    "repo-scouting": ["scout", "explore", "find files", "discovery", "search repo", "read repo"],
    "test-running": ["run tests", "run the tests", "unit test", "tester", "run checks", "lint", "verify build"],
    "reasoning": ["reason", "math", "logic", "analy", "diagnos", "debug", "why"],
    "summarization": ["summar", "digest", "condense", "tl;dr", "log"],
    "docs-writing": ["document", "docs", "readme", "write up", "explain", "report"],
    "classification": ["classif", "label", "categor", "triage", "tag"],
    "routing": ["route", "routing", "pick agent", "dispatch"],
    "long-context": ["long context", "large file", "whole repo", "many files", "long log", "datasheet"],
    "document-understanding": ["pdf", "datasheet", "manual", "document", "scan"],
    "vision": ["image", "screenshot", "picture", "photo", "plot", "diagram", "spectrum", "scope capture", "screen"],
    "screen-reading": ["screen", "screenshot", "ui", "display"],
    "audio": ["audio", "speech", "transcri", "voice", "recording"],
    "sql": ["sql", "query database", "database", "table", "text2sql"],
}
TOOL_CALLING = {"good", "fair", "none", "unknown"}
CONFIDENCE = {"prior", "measured"}
TOOL_WEIGHT = {"good": 1.0, "fair": 0.4, "unknown": 0.0, "none": -1.0}
AGENTIC_TAGS = {"orchestration", "tool-calling", "coding", "test-running", "repo-scouting"}
# Current platform role assignments act as a tiebreak (+0.5) for the tasks they cover.
ROLE_TAGS = {
    "planner": {"planning", "orchestration"},
    "builder": {"coding", "tool-calling"},
    "reviewer": {"code-review"},
    "scout": {"repo-scouting"},
    "small": {"test-running", "summarization"},
}


def task_tags(task: str) -> list[str]:
    """Map free text ("review this diff", "read a spectrum analyzer screenshot") to vocabulary tags."""
    text = task.lower()
    tags = [t for t, words in TASK_TAGS.items() if t in text or any(w in text for w in words)]
    return tags


def _load_registry(root: Path) -> dict:
    path = root / "models" / "registry.yaml"
    if not path.is_file():
        return {"locations": [], "models": []}
    data = yaml.safe_load(path.read_text()) or {}
    return {"locations": data.get("locations") or [], "models": data.get("models") or []}


def list_models(root: str | Path, location: str | None = None, good_for: str | None = None) -> list[dict]:
    reg = _load_registry(Path(root))
    return [_jsonable(m) for m in reg["models"]
            if (location is None or location in (m.get("locations") or []))
            and (good_for is None or good_for in (m.get("good_for") or []))]


def recommend_models(root: str | Path, task: str, location: str | None = None,
                     modality: str | None = None, k: int = 3) -> dict:
    """Rank models for a task. Score: +2 per matching good_for tag, -3 per avoid_for tag,
    tool-calling ability when the task is agentic, +0.5 if backed by our own evals."""
    tags = task_tags(task)
    if modality and modality != "text":
        tags = sorted(set(tags) | ({"vision"} if modality in ("image", "video") else {modality}))
    agentic = bool(set(tags) & AGENTIC_TAGS)
    locs = {loc.get("name"): loc for loc in _load_registry(Path(root))["locations"]}

    def api_reachable(m: dict) -> bool:
        return any(locs.get(n, {}).get("access", "api") == "api" for n in m.get("locations") or [])

    ranked, also = [], []
    for m in list_models(root, location):
        if modality and modality not in (m.get("modalities") or ["text"]):
            continue
        good = [t for t in tags if t in (m.get("good_for") or [])]
        bad = [t for t in tags if t in (m.get("avoid_for") or [])]
        score = 2.0 * len(good) - 3.0 * len(bad)
        # Tool-calling only matters when the model would be used for the agentic part of the task.
        agentic_use = agentic and bool(set(good) & AGENTIC_TAGS)
        if agentic_use:
            score += TOOL_WEIGHT.get(m.get("tool_calling", "unknown"), 0.0)
        if set(tags) & ROLE_TAGS.get(m.get("platform_role") or "", set()):
            score += 0.5
        if m.get("confidence") == "measured":
            score += 0.5
        if score <= 0 or not good:
            continue
        why = [f"good for {', '.join(good)}"]
        if bad:
            why.append(f"but avoid for {', '.join(bad)}")
        if agentic_use:
            why.append(f"tool calling: {m.get('tool_calling', 'unknown')}")
        if m.get("platform_role"):
            why.append(f"current role: {m['platform_role']}")
        why.append(f"confidence: {m.get('confidence', 'prior')}")
        entry = {"id": m["id"], "name": m.get("name"), "score": round(score, 2),
                 "platform_role": m.get("platform_role"), "locations": m.get("locations"),
                 "why": "; ".join(why), "notes": m.get("notes")}
        # Interactive-only models (e.g. Copilot) can't be used by agents: list them separately
        # unless that location was asked for explicitly.
        (ranked if location or api_reachable(m) else also).append(entry)
    ranked.sort(key=lambda r: -r["score"])
    also.sort(key=lambda r: -r["score"])
    return {"task": task, "matched_tags": tags, "location": location, "results": ranked[:k],
            "interactive_only": [{"id": a["id"], "locations": a["locations"]} for a in also[:k]],
            "hint": None if tags else f"No task tags matched. Use words for: {', '.join(TASK_TAGS)}"}


def validate_models(root: Path, v: "Validation") -> None:
    path = root / "models" / "registry.yaml"
    if not path.is_file():
        return
    try:
        reg = _load_registry(root)
    except yaml.YAMLError as exc:
        v.errors.append(f"models/registry.yaml: invalid YAML: {exc}")
        return
    locs = set()
    for loc in reg["locations"]:
        if not loc.get("name"):
            v.errors.append("models/registry.yaml: location without name")
        access = loc.get("access", "api")
        if access not in ("api", "interactive"):
            v.errors.append(f"models/registry.yaml: location {loc.get('name')} access must be api or interactive")
        if access == "api" and not (loc.get("base_url") or loc.get("base_url_env")):
            v.errors.append(f"models/registry.yaml: location {loc.get('name')} needs base_url or base_url_env")
        if loc.get("data_boundary") not in (None, "internal", "external"):
            v.errors.append(f"models/registry.yaml: location {loc.get('name')} data_boundary must be internal or external")
        locs.add(loc.get("name"))
    seen = set()
    for m in reg["models"]:
        label = f"models/registry.yaml: {m.get('id', '<no id>')}"
        for key in ("id", "name", "locations", "good_for", "tool_calling", "confidence"):
            if not m.get(key):
                v.errors.append(f"{label}: missing '{key}'")
        if m.get("id") in seen:
            v.errors.append(f"{label}: duplicate id")
        seen.add(m.get("id"))
        for loc in m.get("locations") or []:
            if loc not in locs:
                v.errors.append(f"{label}: unknown location '{loc}'")
        for key in ("good_for", "avoid_for"):
            for tag in m.get(key) or []:
                if tag not in TASK_TAGS:
                    v.errors.append(f"{label}: {key} tag '{tag}' not in vocabulary ({', '.join(TASK_TAGS)})")
        if m.get("tool_calling") and m["tool_calling"] not in TOOL_CALLING:
            v.errors.append(f"{label}: tool_calling must be one of {sorted(TOOL_CALLING)}")
        if m.get("confidence") and m["confidence"] not in CONFIDENCE:
            v.errors.append(f"{label}: confidence must be one of {sorted(CONFIDENCE)}")
        if m.get("confidence") == "measured" and not m.get("evidence"):
            v.errors.append(f"{label}: measured confidence needs evidence (eval ids)")


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    return obj


# ---------- validation ----------
@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate(root: str | Path, profiles: set[str] | None = None, stale_days: int = DEFAULT_STALE_DAYS,
             today: dt.date | None = None) -> Validation:
    root = Path(root).resolve()
    today = today or dt.date.today()
    v = Validation()
    known = (profiles or set()) | {"all"}

    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        v.errors.append("docs/ missing")
    for path in sorted(docs_dir.rglob("*.md")) if docs_dir.is_dir() else []:
        rel = path.relative_to(root).as_posix()
        if any(p.startswith("_") for p in path.relative_to(docs_dir).parts):
            continue
        if not DOC_NAME.match(path.name):
            v.errors.append(f"{rel}: file name must be lowercase-hyphenated .md")
        text = path.read_text(encoding="utf-8")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                v.errors.append(f"{rel}: looks like it contains a secret")
                break
        try:
            meta, body = parse_frontmatter(text)
        except yaml.YAMLError as exc:
            v.errors.append(f"{rel}: bad frontmatter: {exc}")
            continue
        if meta is None:
            v.errors.append(f"{rel}: missing frontmatter")
            continue
        for key in REQUIRED:
            if not meta.get(key):
                v.errors.append(f"{rel}: missing '{key}'")
        if meta.get("kind") and meta["kind"] not in KINDS:
            v.errors.append(f"{rel}: kind must be one of {sorted(KINDS)}")
        if meta.get("status") and meta["status"] not in STATUSES:
            v.errors.append(f"{rel}: status must be one of {sorted(STATUSES)}")
        applies = meta.get("applies_to")
        if applies is not None and not isinstance(applies, list):
            v.errors.append(f"{rel}: applies_to must be a list")
        elif profiles is not None:
            for p in applies or []:
                if p not in known:
                    v.errors.append(f"{rel}: applies_to '{p}' is not a profile")
        lv_raw = meta.get("last_verified")
        lv = _as_date(lv_raw)
        if lv_raw is not None and lv is None:
            v.errors.append(f"{rel}: last_verified must be YYYY-MM-DD")
        if meta.get("status") == "verified":
            if lv is None:
                v.errors.append(f"{rel}: verified docs need last_verified")
            elif lv > today:
                v.errors.append(f"{rel}: last_verified is in the future")
            elif (today - lv).days > stale_days:
                v.warnings.append(f"{rel}: stale (last verified {lv}, over {stale_days} days)")
        if not body.strip():
            v.errors.append(f"{rel}: empty body")

    inv = root / "inventory"
    benches = set()
    if (inv / "benches.yaml").is_file():
        data = yaml.safe_load((inv / "benches.yaml").read_text()) or {}
        for b in data.get("benches") or []:
            if not b.get("name"):
                v.errors.append("inventory/benches.yaml: bench without name")
            benches.add(b.get("name"))
    if (inv / "devices.yaml").is_file():
        data = yaml.safe_load((inv / "devices.yaml").read_text()) or {}
        seen = set()
        for d in data.get("devices") or []:
            name = d.get("name")
            label = f"inventory/devices.yaml: {name or '<unnamed>'}"
            required = ("name", "kind", "bench") if d.get("kind") == "dut" else ("name", "kind", "bench", "visa")
            for key in required:
                if not d.get(key):
                    v.errors.append(f"{label}: missing '{key}'")
            if name in seen:
                v.errors.append(f"{label}: duplicate name")
            seen.add(name)
            if d.get("bench") and d["bench"] not in benches:
                v.errors.append(f"{label}: bench '{d['bench']}' not in benches.yaml")
            if not isinstance(d.get("read_only", True), bool):
                v.errors.append(f"{label}: read_only must be true/false")
            wl = d.get("write_allowlist", [])
            if not isinstance(wl, list) or not all(isinstance(x, str) for x in wl):
                v.errors.append(f"{label}: write_allowlist must be a list of strings")
            if d.get("read_only", True) and wl:
                v.warnings.append(f"{label}: has write_allowlist but read_only is true (allowlist unused)")

    cat = root / "catalog" / "skills.json"
    if cat.is_file():
        try:
            json.loads(cat.read_text())
        except json.JSONDecodeError as exc:
            v.errors.append(f"catalog/skills.json: invalid JSON: {exc}")
    validate_models(root, v)
    return v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    va = sub.add_parser("validate")
    va.add_argument("root", type=Path)
    va.add_argument("--profiles-from", type=Path, help="AP_HOME/profiles; checks applies_to values")
    va.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    se = sub.add_parser("search")
    se.add_argument("root", type=Path)
    se.add_argument("query")
    se.add_argument("--profile")
    se.add_argument("-k", type=int, default=5)
    sk = sub.add_parser("skills")
    sk.add_argument("root", type=Path)
    sk.add_argument("query")
    sk.add_argument("-k", type=int, default=5)
    mo = sub.add_parser("models", help="list models, optionally per location / tag")
    mo.add_argument("root", type=Path)
    mo.add_argument("--location")
    mo.add_argument("--good-for")
    rc = sub.add_parser("recommend", help="rank models for a task")
    rc.add_argument("root", type=Path)
    rc.add_argument("task")
    rc.add_argument("--location")
    rc.add_argument("--modality")
    rc.add_argument("-k", type=int, default=3)
    args = ap.parse_args(argv)

    if args.cmd == "models":
        for m in list_models(args.root, args.location, args.good_for):
            print(f"{m['id']:28} {m.get('platform_role') or '':9} {', '.join(m.get('good_for', []))}")
        return 0
    if args.cmd == "recommend":
        print(json.dumps(recommend_models(args.root, args.task, args.location, args.modality, args.k), indent=2))
        return 0

    if args.cmd == "validate":
        profiles = None
        if args.profiles_from:
            profiles = {p.name for p in args.profiles_from.iterdir() if (p / "profile.yaml").is_file()}
        res = validate(args.root, profiles, args.stale_days)
        for w in res.warnings:
            print(f"warning: {w}")
        for e in res.errors:
            print(f"error: {e}")
        print(f"validate: {len(res.errors)} errors, {len(res.warnings)} warnings")
        return 1 if res.errors else 0
    store = KnowledgeStore(args.root, profile=getattr(args, "profile", None))
    results = store.search_docs(args.query, args.k) if args.cmd == "search" else store.find_skill(args.query, args.k)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
