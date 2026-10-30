#!/usr/bin/env python3
"""Index Agent Skills (SKILL.md) across repos so agents and people can see what exists.

    skills_index.py local DIR [DIR ...] -o OUT_DIR
    skills_index.py gitlab --url https://gitlab.example.internal --group tools -o OUT_DIR

The gitlab mode reads the token from $GITLAB_TOKEN (needs read_api) and scans every
project in the group and its subgroups. Skills are looked for under: skills/,
.opencode/skills/, .agents/skills/, .claude/skills/  (one directory per skill).

Writes OUT_DIR/skills.json (read by the knowledge server's find_skill tool) and
OUT_DIR/SKILLS.md (for people), and flags duplicate names and near-duplicate descriptions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

SKILL_ROOTS = ("skills", ".opencode/skills", ".agents/skills", ".claude/skills")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = set(
    "a an and are as at be by for from in into is it of on or that the this to use used uses using when with "
    "your you whenever before after any can not".split()
)
SIMILAR_THRESHOLD = 0.5


def parse_skill(text: str) -> tuple[dict, list[str]]:
    problems: list[str] = []
    if not text.startswith("---\n") or "\n---" not in text[4:]:
        return {}, ["no YAML frontmatter"]
    end = text.find("\n---", 4)
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        return {}, [f"bad frontmatter: {exc}"]
    name = str(meta.get("name", ""))
    desc = str(meta.get("description", "") or "")
    if not NAME_RE.match(name) or len(name) > 64:
        problems.append("invalid name")
    if not 1 <= len(desc) <= 1024:
        problems.append("description must be 1-1024 chars")
    return meta, problems


def make_entry(meta: dict, problems: list[str], project: str, path: str, dirname: str,
               web_url: str | None = None, ref: str | None = None) -> dict:
    name = str(meta.get("name") or dirname)
    if meta.get("name") and name != dirname:
        problems = [*problems, f"name '{name}' != directory '{dirname}'"]
    entry = {
        "name": name,
        "description": " ".join(str(meta.get("description", "")).split()),
        "project": project,
        "path": path,
        "scope": name.split("-", 1)[0] if "-" in name else "",
        "problems": problems,
    }
    if meta.get("license"):
        entry["license"] = str(meta["license"])
    if isinstance(meta.get("metadata"), dict):
        entry["metadata"] = {str(k): str(v) for k, v in meta["metadata"].items()}
    if web_url:
        entry["web_url"] = web_url
    if ref:
        entry["ref"] = ref
    return entry


def discover_local(dirs: list[Path]) -> list[dict]:
    entries = []
    for repo in dirs:
        repo = repo.resolve()
        for root in SKILL_ROOTS:
            base = repo / root
            if not base.is_dir():
                continue
            for sub in sorted(p for p in base.iterdir() if p.is_dir()):
                md = sub / "SKILL.md"
                if md.is_file():
                    meta, problems = parse_skill(md.read_text(encoding="utf-8"))
                    rel = md.relative_to(repo).as_posix()
                    entries.append(make_entry(meta, problems, repo.name, rel, sub.name))
    return entries


class GitLab:
    """Minimal GitLab REST client. `opener(url, headers) -> (status, body_bytes, headers)` is
    injectable for tests."""

    def __init__(self, url: str, token: str, opener=None):
        self.api = url.rstrip("/") + "/api/v4"
        self.token = token
        self.opener = opener or self._urlopen

    @staticmethod
    def _urlopen(url: str, headers: dict):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers or {})

    def _get(self, path: str, params: dict | None = None):
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        status, body, headers = self.opener(self.api + path + qs, {"PRIVATE-TOKEN": self.token})
        if status == 404:
            return None, headers
        if status >= 400:
            raise RuntimeError(f"GitLab {status} for {path}: {body[:200]!r}")
        return body, headers

    def _paged(self, path: str, params: dict):
        page = "1"
        while page:
            body, headers = self._get(path, {**params, "per_page": 100, "page": page})
            if body is None:
                return
            yield from json.loads(body)
            page = headers.get("X-Next-Page") or headers.get("x-next-page") or ""

    def projects(self, group: str):
        gid = urllib.parse.quote(group, safe="")
        yield from self._paged(f"/groups/{gid}/projects", {"include_subgroups": "true", "archived": "false"})

    def tree(self, pid: int, path: str, ref: str):
        yield from self._paged(f"/projects/{pid}/repository/tree", {"path": path, "ref": ref})

    def raw(self, pid: int, file_path: str, ref: str) -> str | None:
        fp = urllib.parse.quote(file_path, safe="")
        body, _ = self._get(f"/projects/{pid}/repository/files/{fp}/raw", {"ref": ref})
        return body.decode("utf-8", "replace") if body is not None else None


def discover_gitlab(gl: GitLab, group: str) -> list[dict]:
    entries = []
    for proj in gl.projects(group):
        ref = proj.get("default_branch")
        if not ref:
            continue
        for root in SKILL_ROOTS:
            for item in gl.tree(proj["id"], root, ref):
                if item.get("type") != "tree":
                    continue
                file_path = f"{item['path']}/SKILL.md"
                text = gl.raw(proj["id"], file_path, ref)
                if text is None:
                    continue
                meta, problems = parse_skill(text)
                web = f"{proj['web_url']}/-/blob/{ref}/{file_path}"
                entries.append(make_entry(meta, problems, proj["path_with_namespace"], file_path,
                                          item["name"], web_url=web, ref=ref))
    return entries


def _words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 2}


def build_catalog(entries: list[dict]) -> dict:
    entries = sorted(entries, key=lambda e: (e["name"], e["project"], e["path"]))
    by_name = defaultdict(list)
    for e in entries:
        by_name[e["name"]].append(f"{e['project']}:{e['path']}")
    duplicates = {n: locs for n, locs in by_name.items() if len(locs) > 1}

    similar = []
    uniq = {}
    for e in entries:
        uniq.setdefault(e["name"], e)
    names = sorted(uniq)
    words = {n: _words(uniq[n]["description"]) for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            wa, wb = words[a], words[b]
            if wa and wb:
                score = len(wa & wb) / len(wa | wb)
                if score >= SIMILAR_THRESHOLD:
                    similar.append({"a": a, "b": b, "score": round(score, 2)})

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(entries),
        "skills": entries,
        "duplicates": duplicates,
        "similar": similar,
    }


def to_markdown(cat: dict) -> str:
    lines = [
        "# Skills catalog",
        "",
        f"Generated {cat['generated_at']} by `tools/skills_index.py`. Do not edit by hand.",
        "",
        "| Skill | Project | Description | Problems |",
        "|-------|---------|-------------|----------|",
    ]
    for e in cat["skills"]:
        desc = e["description"] if len(e["description"]) <= 140 else e["description"][:137] + "..."
        name = f"[{e['name']}]({e['web_url']})" if e.get("web_url") else e["name"]
        lines.append(f"| {name} | {e['project']} | {desc.replace('|', '/')} | {'; '.join(e['problems'])} |")
    if cat["duplicates"]:
        lines += ["", "## Duplicate names", ""]
        for name, locs in cat["duplicates"].items():
            lines.append(f"- `{name}`: " + ", ".join(locs))
    if cat["similar"]:
        lines += ["", "## Similar descriptions (possible overlap)", ""]
        for s in cat["similar"]:
            lines.append(f"- `{s['a']}` and `{s['b']}` (overlap {s['score']})")
    return "\n".join(lines) + "\n"


def write_outputs(cat: dict, out_dir: Path) -> None:
    """Write the catalog. If nothing but the timestamp changed, keep the old timestamp so the
    files stay byte-identical and CI can detect "no change" with git status."""
    out_dir.mkdir(parents=True, exist_ok=True)
    old_path = out_dir / "skills.json"
    if old_path.is_file():
        try:
            old = json.loads(old_path.read_text())
            strip = lambda c: {k: v for k, v in c.items() if k != "generated_at"}  # noqa: E731
            if strip(old) == strip(cat):
                cat = {**cat, "generated_at": old.get("generated_at", cat["generated_at"])}
        except (json.JSONDecodeError, AttributeError):
            pass
    (out_dir / "skills.json").write_text(json.dumps(cat, indent=2) + "\n", encoding="utf-8")
    (out_dir / "SKILLS.md").write_text(to_markdown(cat), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    loc = sub.add_parser("local", help="scan local checkouts")
    loc.add_argument("dirs", nargs="+", type=Path)
    loc.add_argument("-o", "--out", type=Path, required=True)
    gl = sub.add_parser("gitlab", help="scan a GitLab group")
    gl.add_argument("--url", required=True)
    gl.add_argument("--group", required=True)
    gl.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.mode == "local":
        entries = discover_local(args.dirs)
    else:
        token = os.environ.get("GITLAB_TOKEN", "")
        if not token:
            print("skills_index: set GITLAB_TOKEN (read_api)", file=sys.stderr)
            return 2
        entries = discover_gitlab(GitLab(args.url, token), args.group)

    cat = build_catalog(entries)
    write_outputs(cat, args.out)
    print(f"skills_index: {cat['count']} skills, {len(cat['duplicates'])} duplicate names, "
          f"{len(cat['similar'])} similar pairs -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
