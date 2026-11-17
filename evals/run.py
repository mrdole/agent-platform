#!/usr/bin/env python3
"""Run golden tasks against the real models and compare with a baseline.

    evals/run.py [--profile P] [--task ID] [--repeat N] [--baseline evals/baseline.json]
                 [--out .eval-results] [--dry-run] [--no-standalone]

Each task (evals/<profile>/<id>.yaml):
    id, profile, agent, fixture (dir under evals/fixtures), prompt, check, timeout_s (optional)

For each task and each of N repeats (--repeat, default 1): copy the fixture into a fresh git
repo and run
    agent --headless <profile> run --standalone --format json --agent <agent> --auto <prompt>
in it (--standalone gives the eval a private OpenCode server; --no-standalone drops it).
Per run, <out>/<stamp>/<task>/run-<k>/ holds:
    raw.jsonl   the agent's raw stdout (+ stderr section)
    tools.jsonl one line per tool call: tool, status, input (shortened), error
    output.md   the text parts of the JSON stream (the raw output if it is not JSON);
                exposed to the check as $AP_EVAL_OUTPUT so tasks can grep the answer
    check.log   the check's output
The check (exit 0 = pass) runs in a COPY of the workspace, so it cannot change what the
agent left behind. results.json also records versions (OpenCode, platform, model roles,
rendered-config hash per profile), pass rate per task and the number of denied tool calls.

Exit 1 if any task's pass rate is below its baseline pass rate (an old baseline with only a
boolean "pass" counts as 1.0 or 0.0). Without a baseline, exit 0.
AP_EVAL_AGENT_CMD overrides the agent command (tests use a stub).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent


def load_tasks(profile: str | None, task_id: str | None) -> list[dict]:
    tasks = []
    for path in sorted(EVALS.glob("*/*.yaml")):
        task = yaml.safe_load(path.read_text()) or {}
        if profile and task.get("profile") != profile:
            continue
        if task_id and task.get("id") != task_id:
            continue
        task["_file"] = str(path.relative_to(EVALS))
        tasks.append(task)
    return tasks


def prepare_workspace(task: dict, dest: Path) -> Path:
    ws = dest / "workspace"
    shutil.copytree(EVALS / "fixtures" / task["fixture"], ws)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=eval", "-c", "user.email=eval@noreply.invalid",
                 "commit", "-q", "-m", "fixture", "--allow-empty"]):
        subprocess.run(cmd, cwd=ws, check=True, capture_output=True)
    return ws


DENIED_RE = re.compile(r"denied|rejected|not allowed|permission|forbidden", re.I)


def parse_stream(raw: str) -> tuple[bool, list[dict], str]:
    """Parse `opencode run --format json` output. Returns (is_json, tool calls, text)."""
    is_json, tools, texts = False, [], []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or "type" not in obj:
            continue
        is_json = True
        part = obj.get("part") if isinstance(obj.get("part"), dict) else {}
        if obj["type"] == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            inp = json.dumps(state.get("input"), ensure_ascii=False, sort_keys=True)
            tools.append({
                "tool": part.get("tool"), "status": state.get("status"),
                "input": inp if len(inp) <= 200 else inp[:197] + "...",
                "error": state.get("error"),
            })
        elif obj["type"] == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return is_json, tools, "\n".join(texts)


def is_denied(call: dict) -> bool:
    if call.get("status") == "denied":
        return True
    return bool(call.get("error")) and bool(DENIED_RE.search(str(call["error"])))


def run_once(task: dict, run_dir: Path, agent_cmd: list[str], standalone: bool) -> dict:
    run_dir.mkdir(parents=True)
    ws = prepare_workspace(task, run_dir)
    timeout = int(task.get("timeout_s", 1800))
    cmd = [*agent_cmd, "--headless", task["profile"], "run", *(["--standalone"] if standalone else []),
           "--format", "json", "--agent", task["agent"], "--auto", task["prompt"]]
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=timeout)
        agent_rc, raw, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        agent_rc, raw, err = 124, f"[agent timed out after {timeout}s]", ""
    except FileNotFoundError as exc:
        agent_rc, raw, err = 127, f"[agent command not found: {exc}]", ""
    agent_s = time.monotonic() - start
    (run_dir / "raw.jsonl").write_text(raw + ("\n[stderr]\n" + err if err else ""), encoding="utf-8")
    is_json, tools, text = parse_stream(raw)
    (run_dir / "tools.jsonl").write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tools),
                                         encoding="utf-8")
    output = text if is_json else raw + ("\n[stderr]\n" + err if err else "")
    (run_dir / "output.md").write_text(output, encoding="utf-8")

    # Run the check in a copy so it cannot affect (or be affected by) the agent's folder.
    check_ws = run_dir / "check-workspace"
    shutil.copytree(ws, check_ws, symlinks=True)
    env = {**os.environ, "AP_EVAL_OUTPUT": str(run_dir / "output.md")}
    try:
        check = subprocess.run(task["check"], shell=True, cwd=check_ws, capture_output=True, text=True,
                               env=env, timeout=600)
        check_rc, clog = check.returncode, check.stdout + check.stderr
    except subprocess.TimeoutExpired:
        check_rc, clog = 124, "[check timed out after 600s]"
    (run_dir / "check.log").write_text(f"$ {task['check']}\nexit {check_rc}\n{clog}")
    shutil.rmtree(check_ws, ignore_errors=True)
    return {
        "pass": check_rc == 0, "agent_rc": agent_rc, "check_rc": check_rc,
        "agent_seconds": round(agent_s, 1), "tool_calls": len(tools),
        "denied_calls": sum(is_denied(t) for t in tools), "json_output": is_json,
    }


def run_task(task: dict, out_dir: Path, agent_cmd: list[str], repeat: int = 1, standalone: bool = True) -> dict:
    tdir = out_dir / task["id"]
    runs = [run_once(task, tdir / f"run-{k}", agent_cmd, standalone) for k in range(1, repeat + 1)]
    passes = sum(r["pass"] for r in runs)
    return {
        "id": task["id"], "profile": task["profile"], "agent": task["agent"],
        "pass": passes == len(runs), "passes": passes, "runs_n": len(runs),
        "pass_rate": round(passes / len(runs), 3),
        "agent_rc": next((r["agent_rc"] for r in runs if r["agent_rc"]), 0),
        "check_rc": next((r["check_rc"] for r in runs if r["check_rc"]), 0),
        "agent_seconds": round(sum(r["agent_seconds"] for r in runs) / len(runs), 1),
        "denied_calls": sum(r["denied_calls"] for r in runs),
        "runs": runs,
    }


def _cmd_out(cmd: list[str], cwd: Path | None = None) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def model_roles(env: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if env is None else env
    roles: dict[str, str] = {}
    pe = ROOT / "core" / "platform.env"
    if pe.is_file():
        for line in pe.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip().startswith("AP_MODEL_"):
                roles[k.strip()] = v.strip()
    roles.update({k: v for k, v in env.items() if k.startswith("AP_MODEL_")})
    return dict(sorted(roles.items()))


def config_hashes(profiles: list[str]) -> dict[str, str]:
    """sha256 (first 16 hex) of each profile's rendered headless config, or 'unknown'."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import render as render_mod
    except Exception:  # noqa: BLE001
        return {p: "unknown" for p in profiles}
    out = {}
    for p in profiles:
        try:
            blob = json.dumps(render_mod.render(p, headless=True), sort_keys=True)
            out[p] = hashlib.sha256(blob.encode()).hexdigest()[:16]
        except BaseException:  # noqa: BLE001  (render exits via SystemExit on errors)
            out[p] = "unknown"
    return out


def collect_versions(profiles: list[str]) -> dict:
    vfile = ROOT / "VERSION"
    return {
        "harness": {"opencode": (_cmd_out(["opencode", "--version"]) if shutil.which("opencode") else None) or "unknown"},
        "platform": {
            "version": vfile.read_text().strip() if vfile.is_file() else "unknown",
            "git": _cmd_out(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT) or "unknown",
        },
        "model_roles": model_roles(),
        "config_hash": config_hashes(profiles),
    }


def rate_of(entry: dict) -> float:
    if "pass_rate" in entry:
        return float(entry["pass_rate"])
    return 1.0 if entry.get("pass") else 0.0


def summarize(results: list[dict], baseline: dict[str, float] | None) -> tuple[str, list[str]]:
    regressions = []
    lines = ["| Task | Profile | Agent | Pass | Baseline | Denied | Agent rc | Seconds |",
             "|------|---------|-------|------|----------|--------|----------|---------|"]
    for r in results:
        base = baseline.get(r["id"]) if baseline else None
        rate = rate_of(r)
        if base is not None and rate < base - 1e-9:
            regressions.append(r["id"])
        n = r.get("runs_n", 1)
        passes = r.get("passes", int(r["pass"]))
        cell = f"{passes}/{n}"
        if passes < n:
            cell = f"**{cell}**"
        bcell = "-" if base is None else (f"{base:.0%}")
        lines.append(f"| {r['id']} | {r['profile']} | {r['agent']} | {cell} | {bcell} | "
                     f"{r.get('denied_calls', 0)} | {r['agent_rc']} | {r['agent_seconds']} |")
    tp, tn = sum(r.get("passes", int(r["pass"])) for r in results), sum(r.get("runs_n", 1) for r in results)
    lines.append("")
    lines.append(f"{tp}/{tn} runs passed" + (f"; regressions: {', '.join(regressions)}" if regressions else ""))
    return "\n".join(lines) + "\n", regressions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--task")
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--out", type=Path, default=Path(".eval-results"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repeat", type=int, default=1, metavar="N", help="runs per task, each in a fresh workspace")
    ap.add_argument("--standalone", action=argparse.BooleanOptionalAction, default=True,
                    help="pass --standalone to `run` (private OpenCode server per eval; default on)")
    args = ap.parse_args(argv)

    tasks = load_tasks(args.profile, args.task)
    if not tasks:
        print("evals: no matching tasks", file=sys.stderr)
        return 2
    if args.dry_run:
        for t in tasks:
            print(f"{t['id']}: profile={t['profile']} agent={t['agent']} fixture={t['fixture']} ({t['_file']})")
        return 0

    if args.repeat < 1:
        ap.error("--repeat must be >= 1")
    agent_cmd = shlex.split(os.environ.get("AP_EVAL_AGENT_CMD", "agent"))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out / stamp
    out_dir.mkdir(parents=True)
    results = []
    for t in tasks:
        print(f"evals: running {t['id']} ...", flush=True)
        results.append(run_task(t, out_dir, agent_cmd, args.repeat, args.standalone))

    baseline = None
    if args.baseline:
        if args.baseline.is_file():
            data = json.loads(args.baseline.read_text())
            baseline = {r["id"]: rate_of(r) for r in data.get("results", [])}
        else:
            print(f"evals: baseline {args.baseline} not found; comparing against nothing", file=sys.stderr)

    summary, regressions = summarize(results, baseline)
    (out_dir / "results.json").write_text(json.dumps({
        "run": stamp, "repeat": args.repeat, "standalone": args.standalone,
        "versions": collect_versions(sorted({t["profile"] for t in tasks})),
        "denied_calls": sum(r["denied_calls"] for r in results), "results": results}, indent=2) + "\n")
    (out_dir / "summary.md").write_text(summary)
    print(summary)
    print(f"evals: results in {out_dir}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
