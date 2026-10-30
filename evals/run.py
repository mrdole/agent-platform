#!/usr/bin/env python3
"""Run golden tasks against the real models and compare with a baseline.

    evals/run.py [--profile P] [--task ID] [--baseline evals/baseline.json] [--out .eval-results] [--dry-run]

Each task (evals/<profile>/<id>.yaml):
    id, profile, agent, fixture (dir under evals/fixtures), prompt, check, timeout_s (optional)

For each task: copy the fixture into a fresh git repo, run
    agent --headless <profile> run --agent <agent> --auto <prompt>
in it, then run `check` there (exit 0 = pass). The agent's stdout is saved to
output.md and exposed to the check as $AP_EVAL_OUTPUT, so knowledge-retrieval tasks can
grep the answer.

Exit 1 if any task that passed in the baseline now fails. Without a baseline, exit 0.
AP_EVAL_AGENT_CMD overrides the agent command (tests use a stub).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

EVALS = Path(__file__).resolve().parent


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


def run_task(task: dict, out_dir: Path, agent_cmd: list[str]) -> dict:
    tdir = out_dir / task["id"]
    tdir.mkdir(parents=True)
    ws = prepare_workspace(task, tdir)
    timeout = int(task.get("timeout_s", 1800))
    cmd = [*agent_cmd, "--headless", task["profile"], "run", "--agent", task["agent"], "--auto", task["prompt"]]
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=timeout)
        agent_rc, output = proc.returncode, proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")
    except subprocess.TimeoutExpired:
        agent_rc, output = 124, f"[agent timed out after {timeout}s]"
    except FileNotFoundError as exc:
        agent_rc, output = 127, f"[agent command not found: {exc}]"
    agent_s = time.monotonic() - start
    (tdir / "output.md").write_text(output, encoding="utf-8")

    env = {**os.environ, "AP_EVAL_OUTPUT": str(tdir / "output.md")}
    check = subprocess.run(task["check"], shell=True, cwd=ws, capture_output=True, text=True, env=env, timeout=600)
    (tdir / "check.log").write_text(f"$ {task['check']}\nexit {check.returncode}\n{check.stdout}{check.stderr}")
    return {
        "id": task["id"], "profile": task["profile"], "agent": task["agent"],
        "pass": check.returncode == 0, "agent_rc": agent_rc, "check_rc": check.returncode,
        "agent_seconds": round(agent_s, 1),
    }


def summarize(results: list[dict], baseline: dict[str, bool] | None) -> tuple[str, list[str]]:
    regressions = []
    lines = ["| Task | Profile | Agent | Pass | Baseline | Agent rc | Seconds |",
             "|------|---------|-------|------|----------|----------|---------|"]
    for r in results:
        base = baseline.get(r["id"]) if baseline else None
        if base is True and not r["pass"]:
            regressions.append(r["id"])
        mark = "yes" if r["pass"] else "**no**"
        lines.append(f"| {r['id']} | {r['profile']} | {r['agent']} | {mark} | "
                     f"{'-' if base is None else ('yes' if base else 'no')} | {r['agent_rc']} | {r['agent_seconds']} |")
    passed = sum(r["pass"] for r in results)
    lines.append("")
    lines.append(f"{passed}/{len(results)} passed" + (f"; regressions: {', '.join(regressions)}" if regressions else ""))
    return "\n".join(lines) + "\n", regressions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--task")
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--out", type=Path, default=Path(".eval-results"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    tasks = load_tasks(args.profile, args.task)
    if not tasks:
        print("evals: no matching tasks", file=sys.stderr)
        return 2
    if args.dry_run:
        for t in tasks:
            print(f"{t['id']}: profile={t['profile']} agent={t['agent']} fixture={t['fixture']} ({t['_file']})")
        return 0

    agent_cmd = shlex.split(os.environ.get("AP_EVAL_AGENT_CMD", "agent"))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out / stamp
    out_dir.mkdir(parents=True)
    results = []
    for t in tasks:
        print(f"evals: running {t['id']} ...", flush=True)
        results.append(run_task(t, out_dir, agent_cmd))

    baseline = None
    if args.baseline:
        if args.baseline.is_file():
            data = json.loads(args.baseline.read_text())
            baseline = {r["id"]: r["pass"] for r in data.get("results", [])}
        else:
            print(f"evals: baseline {args.baseline} not found; comparing against nothing", file=sys.stderr)

    summary, regressions = summarize(results, baseline)
    (out_dir / "results.json").write_text(json.dumps({"run": stamp, "results": results}, indent=2) + "\n")
    (out_dir / "summary.md").write_text(summary)
    print(summary)
    print(f"evals: results in {out_dir}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
