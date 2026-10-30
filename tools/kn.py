#!/usr/bin/env python3
"""kn - your personal knowledge notes, searchable alongside the team repo, maintained with your models.

Notes are Markdown files in a personal git repo (GitLab is the backup: `kn sync` pushes).
Search is a SQLite FTS5 index built from the files on each run; nothing runs in the background.

  kn init ~/repos/knowledge             create the personal repo (then add a GitLab remote)
  kn new "Title" [--kind gotcha] [--tags a,b]       empty note from a template; prints its path
  kn capture "rough text..."            your model turns rough text into a structured note
  kn search "query" [--source personal|team] [-k 8]
  kn ask "question"                     your model answers from team + personal notes, with citations
  kn todo add "thing" [--due 2026-11-05] [--tag bench]
  kn todo [--all]                       list open (or all) items
  kn todo done 3                        mark item 3 done
  kn log "what happened"                append to this month's work log
  kn review [--days 7] [--save]         your model summarizes the log and open items
  kn sync [-m "msg"]                    commit, pull --rebase, push
  kn promote personal:inbox/x.md --area network [--kind gotcha]
                                        copy a note into the team repo as a draft on a new branch
  kn check                              secret scan + parse check of personal notes

Roots come from AP_KNOWLEDGE_PERSONAL and AP_KNOWLEDGE_ROOT (site.env), or --personal / --team.
Model calls go to AP_LLM_BASE_URL with CORP_LLM_KEY: capture uses AP_MODEL_SMALL, ask/review
use AP_MODEL_PLANNER (override with AP_KN_MODEL).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "mcp" / "knowledge"))
import knowledge_core as kc  # noqa: E402
from doctor import load_env  # noqa: E402

KINDS = ("note", "gotcha", "howto", "reference", "task")
SECTIONS = {
    "gotcha": ["Symptom", "Cause", "Fix", "How confirmed"],
    "howto": ["Steps", "Verify"],
    "reference": ["Facts"],
    "note": ["Notes"],
    "task": ["Details"],
}
TODO_RE = re.compile(r"^- \[(?P<done>[ x])\] \((?P<id>\d+)\) (?P<text>.*)$")


# ---------------------------------------------------------------- helpers
def slugify(text: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].rstrip("-") or "note"


def today() -> dt.date:
    return dt.date.fromisoformat(os.environ["KN_TODAY"]) if os.environ.get("KN_TODAY") else dt.date.today()


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"kn: git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def render_note(title: str, kind: str, tags: list[str], body: str = "") -> str:
    meta = {"title": title, "kind": kind, "tags": tags, "created": today().isoformat()}
    if not body:
        body = "\n\n".join(f"## {s}\n" for s in SECTIONS.get(kind, ["Notes"]))
    return "---\n" + yaml.safe_dump(meta, sort_keys=False).strip() + "\n---\n" + body.strip() + "\n"


def write_new(root: Path, rel: str, text: str) -> Path:
    path = root / "docs" / rel
    if path.exists():
        raise SystemExit(f"kn: {path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def model_id(role_var: str, env: dict) -> str:
    full = env.get("AP_KN_MODEL") or env.get(role_var) or ""
    return full.split("/", 1)[1] if "/" in full else full


def http_chat(messages: list[dict], model: str, env: dict) -> str:
    base = env.get("AP_LLM_BASE_URL", "").rstrip("/")
    if not base or not model:
        raise SystemExit("kn: AP_LLM_BASE_URL and a model (AP_MODEL_* or AP_KN_MODEL) are required")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps({"model": model, "messages": messages, "temperature": 0.2}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {env.get('CORP_LLM_KEY', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise SystemExit(f"kn: model call failed: {getattr(exc, 'reason', exc)}") from None
    return data["choices"][0]["message"]["content"] or ""


CHAT = http_chat  # tests replace this


def extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise SystemExit("kn: the model did not return JSON; try again or use `kn new`")
    return json.loads(m.group(0))


# ---------------------------------------------------------------- commands
def cmd_init(args, env) -> int:
    root = Path(args.dir).expanduser()
    (root / "docs" / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "journal").mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Personal knowledge\n\nMy notes, searchable by my agents alongside the team `lab-knowledge` repo.\n"
            "Managed with `kn` (agent-platform/tools/kn.py). Notes here are hints, not team facts:\n"
            "promote proven ones with `kn promote`.\n\n"
            "- `docs/inbox/` new notes\n- `docs/tasks.md` todo list\n- `docs/journal/` monthly work log\n\n"
            "No secrets in here, ever. `kn check` scans for them.\n")
    tasks = root / "docs" / "tasks.md"
    if not tasks.exists():
        tasks.write_text("---\ntitle: Tasks\nkind: task\ntags: [tracking]\n---\n")
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text("*.swp\n.DS_Store\n")
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    print(f"kn: personal knowledge repo ready at {root}")
    print("kn: next: create a private GitLab project, then:")
    print(f"  git -C {root} remote add origin <url>")
    print("  kn sync")
    return 0


def cmd_new(args, env, personal: Path) -> int:
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    path = write_new(personal, f"inbox/{slugify(args.title)}.md", render_note(args.title, args.kind, tags))
    print(path)
    return 0


def cmd_capture(args, env, personal: Path) -> int:
    raw = " ".join(args.text) if args.text else sys.stdin.read()
    if not raw.strip():
        raise SystemExit("kn: nothing to capture")
    prompt = (
        "Turn the user's rough notes into one knowledge note. Reply with JSON only:\n"
        '{"title": "...", "kind": "gotcha|howto|reference|note", "tags": ["..."], "body": "markdown"}\n'
        "Rules: keep every concrete detail (commands, error text, hostnames, versions) exactly as written; "
        "do not invent facts; for a gotcha use sections ## Symptom, ## Cause, ## Fix, ## How confirmed "
        "(leave a section as '_unknown_' if the notes don't say); 3-6 lowercase tags; never include "
        "passwords, tokens or keys - replace them with <redacted>.")
    out = extract_json(CHAT([{"role": "system", "content": prompt}, {"role": "user", "content": raw}],
                            model_id("AP_MODEL_SMALL", env), env))
    kind = out.get("kind") if out.get("kind") in KINDS else "note"
    tags = [slugify(str(t), 30) for t in out.get("tags") or []][:6]
    text = render_note(str(out.get("title") or raw[:60]), kind, tags, str(out.get("body") or raw))
    findings = [p.pattern for p in kc.SECRET_PATTERNS if p.search(text)]
    if findings:
        raise SystemExit("kn: the note looks like it contains a secret; not saved. Remove it and retry.")
    if args.dry_run:
        print(text)
        return 0
    print(write_new(personal, f"inbox/{slugify(str(out.get('title') or 'note'))}.md", text))
    return 0


def _store(env, personal: Path | None, team: Path | None) -> kc.KnowledgeStore:
    if team and team.is_dir():
        return kc.KnowledgeStore(team, personal=personal)
    if personal:
        # personal-only: treat the personal repo as the only source
        store = kc.KnowledgeStore(personal / ".no-team", personal=personal)
        return store
    raise SystemExit("kn: no knowledge roots; set AP_KNOWLEDGE_PERSONAL and/or AP_KNOWLEDGE_ROOT")


def cmd_search(args, env, personal, team) -> int:
    store = _store(env, personal, team)
    hits = store.search_docs(" ".join(args.query), args.k, args.source)
    for h in hits:
        flag = {"verified": "", "personal": " (personal)", "draft": " (draft)"}.get(h["status"], f" ({h['status']})")
        print(f"{h['path']}{flag}{' (stale)' if h['stale'] and h['source'] == 'team' else ''}")
        print(f"    {h['title']}")
        print(f"    {h['snippet']}")
    if not hits:
        print("kn: no matches")
    return 0


def cmd_ask(args, env, personal, team) -> int:
    store = _store(env, personal, team)
    question = " ".join(args.question)
    hits = store.search_docs(question, args.k)
    if not hits:
        print("kn: nothing in your notes matches; capture what you learn with `kn capture`")
        return 1
    context = []
    for h in hits:
        doc = store.get_doc(h["path"])
        context.append(f"### {h['path']} ({h['source']}, {h['status']})\n{doc['body'][:2500]}")
    system = ("Answer the question using only the notes below. Cite every claim with the note path in "
              "square brackets, e.g. [network/llm-server-access.md]. Team notes with status 'verified' are "
              "facts; 'draft' and 'personal' notes are unconfirmed - say so when you rely on them. If the "
              "notes don't answer it, say what is missing.")
    answer = CHAT([{"role": "system", "content": system},
                   {"role": "user", "content": f"Question: {question}\n\nNotes:\n\n" + "\n\n".join(context)}],
                  model_id("AP_MODEL_PLANNER", env), env)
    print(re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip())
    print("\nSources:")
    for h in hits:
        print(f"  {h['path']} ({h['source']}, {h['status']})")
    return 0


def _tasks_file(personal: Path) -> Path:
    path = personal / "docs" / "tasks.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntitle: Tasks\nkind: task\ntags: [tracking]\n---\n")
    return path


def cmd_todo(args, env, personal: Path) -> int:
    path = _tasks_file(personal)
    lines = path.read_text().splitlines()
    items = [(i, TODO_RE.match(ln)) for i, ln in enumerate(lines)]
    items = [(i, m) for i, m in items if m]
    action = args.action or "list"
    if action == "add":
        text = " ".join(args.rest).strip()
        if not text:
            raise SystemExit("kn: todo add needs text")
        nid = max((int(m["id"]) for _, m in items), default=0) + 1
        extra = (f" @due({args.due})" if args.due else "") + "".join(f" #{t}" for t in (args.tag or []))
        lines.append(f"- [ ] ({nid}) {text}{extra} @added({today().isoformat()})")
        path.write_text("\n".join(lines) + "\n")
        print(f"kn: added ({nid})")
    elif action == "done":
        ids = {a for a in args.rest}
        hit = False
        for i, m in items:
            if m["id"] in ids and m["done"] == " ":
                lines[i] = f"- [x] ({m['id']}) {m['text']} @done({today().isoformat()})"
                hit = True
        if not hit:
            raise SystemExit(f"kn: no open item {' '.join(ids)}")
        path.write_text("\n".join(lines) + "\n")
        print(f"kn: done {' '.join(sorted(ids))}")
    else:
        shown = 0
        for _, m in items:
            if m["done"] == " " or args.all:
                due = re.search(r"@due\((\d{4}-\d{2}-\d{2})\)", m["text"])
                late = " LATE" if m["done"] == " " and due and due.group(1) < today().isoformat() else ""
                print(f"[{'x' if m['done'] == 'x' else ' '}] {m['id']:>3}  {m['text']}{late}")
                shown += 1
        if not shown:
            print("kn: nothing open")
    return 0


def cmd_log(args, env, personal: Path) -> int:
    d = today()
    path = personal / "docs" / "journal" / f"{d:%Y-%m}.md"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: Work log {d:%Y-%m}\nkind: note\ntags: [journal]\n---\n")
    text = path.read_text()
    if f"## {d.isoformat()}" not in text:
        text = text.rstrip("\n") + f"\n\n## {d.isoformat()}\n"
    text = text.rstrip("\n") + f"\n- {' '.join(args.text).strip()}\n"
    path.write_text(text)
    print(f"kn: logged to {path.relative_to(personal)}")
    return 0


def _recent_log(personal: Path, days: int) -> str:
    since = today() - dt.timedelta(days=days)
    out, keep = [], False
    for f in sorted((personal / "docs" / "journal").glob("*.md")):
        for line in f.read_text().splitlines():
            m = re.match(r"^## (\d{4}-\d{2}-\d{2})$", line)
            if m:
                keep = dt.date.fromisoformat(m.group(1)) >= since
            if keep:
                out.append(line)
    return "\n".join(out)


def cmd_review(args, env, personal: Path) -> int:
    log = _recent_log(personal, args.days)
    tasks = "\n".join(ln for ln in _tasks_file(personal).read_text().splitlines() if TODO_RE.match(ln))
    if not log and not tasks:
        print("kn: no log entries or tasks to review")
        return 0
    system = ("You review an engineer's work log and task list. Write a short Markdown review: "
              "## Done (grouped by theme), ## Open (most important first, flag overdue @due dates as of "
              f"{today().isoformat()}), ## Worth writing down (things in the log that should become a "
              "knowledge note or a team doc), ## Next (3 suggestions). Use only what is given.")
    review = CHAT([{"role": "system", "content": system},
                   {"role": "user", "content": f"Log (last {args.days} days):\n{log or '(none)'}\n\nTasks:\n{tasks or '(none)'}"}],
                  model_id("AP_MODEL_PLANNER", env), env)
    review = re.sub(r"<think>.*?</think>", "", review, flags=re.S).strip()
    print(review)
    if args.save:
        iso = today().isocalendar()
        path = personal / "docs" / "journal" / f"review-{iso[0]}-w{iso[1]:02d}.md"
        path.write_text(f"---\ntitle: Review {iso[0]} week {iso[1]}\nkind: note\ntags: [journal, review]\n---\n{review}\n")
        print(f"\nkn: saved {path.relative_to(personal)}")
    return 0


def cmd_sync(args, env, personal: Path) -> int:
    if not (personal / ".git").exists():
        raise SystemExit(f"kn: {personal} is not a git repo (kn init)")
    findings = _scan(personal)
    if findings:
        print("\n".join(findings))
        raise SystemExit("kn: refusing to sync: possible secrets above")
    git(personal, "add", "-A")
    if git(personal, "status", "--porcelain"):
        git(personal, "-c", "user.name=" + (git(personal, "config", "user.name", check=False) or "kn"),
            "-c", "user.email=" + (git(personal, "config", "user.email", check=False) or "kn@localhost"),
            "commit", "-q", "-m", args.message or f"notes: {today().isoformat()}")
        print("kn: committed")
    if git(personal, "remote", check=False):
        if git(personal, "ls-remote", "--heads", "origin", check=False):
            git(personal, "pull", "-q", "--rebase", "--autostash", "origin", git(personal, "branch", "--show-current"))
        git(personal, "push", "-q", "-u", "origin", "HEAD")
        print("kn: pushed to GitLab")
    else:
        print("kn: no remote yet - notes are only on this machine (git remote add origin <url>)")
    return 0


def cmd_promote(args, env, personal: Path, team: Path | None) -> int:
    if not team or not team.is_dir():
        raise SystemExit("kn: team repo not found (AP_KNOWLEDGE_ROOT)")
    store = _store(env, personal, team)
    ref = args.ref if args.ref.startswith("personal:") else f"personal:{args.ref}"
    doc = store.get_doc(ref)
    kind = args.kind or (doc["kind"] if doc["kind"] in kc.KINDS else "reference")
    owner = args.owner or git(team, "config", "user.name", check=False) or os.environ.get("USER", "CHANGEME")
    meta = {"title": doc["title"], "kind": kind, "applies_to": args.applies_to.split(","), "status": "draft",
            "owner": owner, "tags": doc.get("tags") or []}
    slug = slugify(doc["title"])
    rel = f"{args.area}/{slug}.md"
    dest = team / "docs" / rel
    if dest.exists():
        raise SystemExit(f"kn: team doc {rel} already exists - update it instead")
    if (team / ".git").exists():
        git(team, "switch", "-q", "-c", f"notes/promote-{slug}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False).strip() + "\n---\n" + doc["body"].strip() + "\n")
    errors = [e for e in kc.validate(team).errors if rel in e]
    if errors:
        print("\n".join(errors))
        raise SystemExit("kn: promoted doc fails team validation; fix it before committing")
    if (team / ".git").exists():
        git(team, "add", str(dest))
        git(team, "commit", "-q", "-m", f"Add draft: {doc['title']}")
        print(f"kn: committed docs/{rel} on branch notes/promote-{slug} in {team}")
        print(f"kn: next: git -C {team} push -u origin HEAD, open an MR, delete the personal note after merge")
    else:
        print(f"kn: wrote {dest}")
    return 0


def _scan(personal: Path) -> list[str]:
    out = []
    for path in sorted((personal / "docs").rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in kc.SECRET_PATTERNS:
            if pat.search(text):
                out.append(f"secret? {path.relative_to(personal)}")
                break
    return out


def cmd_check(args, env, personal: Path) -> int:
    findings = _scan(personal)
    store = kc.KnowledgeStore(personal / ".no-team", personal=personal)
    store.refresh()
    findings += store.load_errors
    print("\n".join(findings) if findings else f"kn: {len(store.docs)} notes ok (search: {store.search_engine})")
    return 1 if findings else 0


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kn", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--personal", type=Path)
    ap.add_argument("--team", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("dir")
    p = sub.add_parser("new")
    p.add_argument("title")
    p.add_argument("--kind", choices=KINDS, default="note")
    p.add_argument("--tags")
    p = sub.add_parser("capture")
    p.add_argument("text", nargs="*")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("search")
    p.add_argument("query", nargs="+")
    p.add_argument("--source", choices=["team", "personal"])
    p.add_argument("-k", type=int, default=8)
    p = sub.add_parser("ask")
    p.add_argument("question", nargs="+")
    p.add_argument("-k", type=int, default=6)
    p = sub.add_parser("todo")
    p.add_argument("action", nargs="?", choices=["add", "done", "list"])
    p.add_argument("rest", nargs="*")
    p.add_argument("--due")
    p.add_argument("--tag", action="append")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("log")
    p.add_argument("text", nargs="+")
    p = sub.add_parser("review")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--save", action="store_true")
    p = sub.add_parser("sync")
    p.add_argument("-m", "--message")
    p = sub.add_parser("promote")
    p.add_argument("ref")
    p.add_argument("--area", required=True)
    p.add_argument("--kind", choices=sorted(kc.KINDS))
    p.add_argument("--applies-to", default="all")
    p.add_argument("--owner")
    sub.add_parser("check")
    args = ap.parse_args(argv)

    env = load_env()
    if args.cmd == "init":
        return cmd_init(args, env)
    personal = args.personal or (Path(env["AP_KNOWLEDGE_PERSONAL"]) if env.get("AP_KNOWLEDGE_PERSONAL") else None)
    team = args.team or (Path(env["AP_KNOWLEDGE_ROOT"]) if env.get("AP_KNOWLEDGE_ROOT") else None)
    if args.cmd in ("search", "ask"):
        return {"search": cmd_search, "ask": cmd_ask}[args.cmd](args, env, personal, team)
    if not personal or not personal.is_dir():
        raise SystemExit("kn: no personal repo; run `kn init ~/repos/knowledge` and set ap_personal_knowledge_root")
    if args.cmd == "promote":
        return cmd_promote(args, env, personal, team)
    return {"new": cmd_new, "capture": cmd_capture, "todo": cmd_todo, "log": cmd_log, "review": cmd_review,
            "sync": cmd_sync, "check": cmd_check}[args.cmd](args, env, personal)


if __name__ == "__main__":
    sys.exit(main())
