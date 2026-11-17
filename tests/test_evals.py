import json

import run as evals_run


def test_dry_run_lists_tasks(capsys):
    assert evals_run.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "fw-smoke-off-by-one" in out and "pipe-fix-ci-yaml" in out and "itd-find-headless-gotcha" in out


def test_stub_agent_pass_and_regression(tmp_path, monkeypatch):
    fixer = tmp_path / "fixer"
    fixer.write_text("#!/usr/bin/env bash\nsed -i 's/len(data) - 1/len(data)/' checksum.py\necho done\n")
    fixer.chmod(0o755)
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", str(fixer))
    out = tmp_path / "res"
    assert evals_run.main(["--task", "fw-smoke-off-by-one", "--out", str(out)]) == 0
    results = json.loads(next(out.glob("*/results.json")).read_text())["results"]
    assert results[0]["pass"] is True

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"results": results}))
    lazy = tmp_path / "lazy"
    lazy.write_text("#!/usr/bin/env bash\necho nothing\n")
    lazy.chmod(0o755)
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", str(lazy))
    assert evals_run.main(["--task", "fw-smoke-off-by-one", "--out", str(tmp_path / "res2"),
                           "--baseline", str(baseline)]) == 1  # regression


def test_knowledge_task_reads_agent_output(tmp_path, monkeypatch):
    answer = tmp_path / "answer"
    answer.write_text("#!/usr/bin/env bash\necho 'opencode run --auto approves ask; see gotchas/opencode-headless-ask.md'\n")
    answer.chmod(0o755)
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", str(answer))
    assert evals_run.main(["--task", "itd-find-headless-gotcha", "--out", str(tmp_path / "r")]) == 0
    results = json.loads(next((tmp_path / "r").glob("*/results.json")).read_text())["results"]
    assert results[0]["pass"] is True


def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)
    return str(p)


def _results(out):
    return json.loads(next(out.glob("*/results.json")).read_text())


def test_repeat_mixed_pass_rate_and_baseline(tmp_path, monkeypatch):
    counter = tmp_path / "n"
    # fixes the bug on every second invocation only
    stub = _stub(tmp_path, "flaky", f"""n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}
if [ $((n % 2)) -eq 0 ]; then sed -i 's/len(data) - 1/len(data)/' checksum.py; fi
echo done
""")
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", stub)
    out = tmp_path / "res"
    assert evals_run.main(["--task", "fw-smoke-off-by-one", "--repeat", "4", "--out", str(out)]) == 0
    res = _results(out)["results"][0]
    assert (res["passes"], res["runs_n"], res["pass_rate"]) == (2, 4, 0.5)
    assert len(res["runs"]) == 4 and res["pass"] is False
    assert "2/4" in next(out.glob("*/summary.md")).read_text()
    # baseline on rates: 0.5 -> 0.25 regresses, 0.5 -> 0.5 does not, old boolean format still works
    base = tmp_path / "b.json"
    base.write_text(json.dumps({"results": [{"id": "fw-smoke-off-by-one", "pass_rate": 0.75}]}))
    counter.write_text("0")
    assert evals_run.main(["--task", "fw-smoke-off-by-one", "--repeat", "4", "--out", str(tmp_path / "r2"),
                           "--baseline", str(base)]) == 1
    base.write_text(json.dumps({"results": [{"id": "fw-smoke-off-by-one", "pass": False}]}))
    counter.write_text("0")
    assert evals_run.main(["--task", "fw-smoke-off-by-one", "--repeat", "2", "--out", str(tmp_path / "r3"),
                           "--baseline", str(base)]) == 0


def test_versions_block(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", _stub(tmp_path, "x", "echo hi\n"))
    monkeypatch.setenv("AP_MODEL_BUILDER", "test/override")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # no opencode on PATH in tests
    monkeypatch.setattr(evals_run.shutil, "which", lambda _n: None)
    assert evals_run.main(["--task", "itd-find-headless-gotcha", "--out", str(tmp_path / "r")]) == 0
    v = _results(tmp_path / "r")["versions"]
    assert v["harness"]["opencode"] == "unknown"
    assert v["platform"]["version"] == (evals_run.ROOT / "VERSION").read_text().strip()
    assert v["model_roles"]["AP_MODEL_BUILDER"] == "test/override"
    assert "AP_MODEL_PLANNER" in v["model_roles"]
    assert "it-debug" in v["config_hash"]


STREAM = """\
{"type":"step_start","part":{"type":"step-start"}}
{"type":"tool_use","part":{"tool":"read","state":{"status":"completed","input":{"filePath":"a.py"}}}}
{"type":"tool_use","part":{"tool":"bash","state":{"status":"error","input":{"command":"git push"},"error":"The user has specified a rule which prevents you from using this tool: denied"}}}
{"type":"text","part":{"text":"Because of the --auto flag; see opencode-headless-ask."}}
"""


def test_tool_calls_extracted_from_json_stream(tmp_path, monkeypatch):
    sf = tmp_path / "stream.jsonl"
    sf.write_text(STREAM)
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", _stub(tmp_path, "j", f"cat {sf}\n"))
    out = tmp_path / "r"
    assert evals_run.main(["--task", "itd-find-headless-gotcha", "--out", str(out)]) == 0  # check greps text part
    data = _results(out)
    assert data["denied_calls"] == 1
    run_dir = next(out.glob("*/itd-find-headless-gotcha/run-1"))
    calls = [json.loads(line) for line in (run_dir / "tools.jsonl").read_text().splitlines()]
    assert [c["tool"] for c in calls] == ["read", "bash"]
    assert calls[1]["status"] == "error" and "denied" in calls[1]["error"] and "git push" in calls[1]["input"]
    assert (run_dir / "output.md").read_text().startswith("Because of the --auto")
    assert (run_dir / "raw.jsonl").read_text().startswith('{"type":"step_start"')


def test_plain_text_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", _stub(tmp_path, "p", "echo 'plain: --auto opencode-headless-ask'\n"))
    out = tmp_path / "r"
    assert evals_run.main(["--task", "itd-find-headless-gotcha", "--out", str(out)]) == 0
    run_dir = next(out.glob("*/itd-find-headless-gotcha/run-1"))
    assert "plain:" in (run_dir / "output.md").read_text()
    assert (run_dir / "tools.jsonl").read_text() == ""


def test_standalone_flag_placement(tmp_path, monkeypatch):
    log = tmp_path / "argv"
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", _stub(tmp_path, "a", f'echo "$@" > {log}\n'))
    evals_run.main(["--task", "itd-find-headless-gotcha", "--out", str(tmp_path / "r")])
    assert " run --standalone --format json " in " " + log.read_text()
    evals_run.main(["--task", "itd-find-headless-gotcha", "--no-standalone", "--out", str(tmp_path / "r2")])
    assert "--standalone" not in log.read_text()


def test_check_runs_in_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_EVAL_AGENT_CMD", _stub(tmp_path, "n", "echo nothing\n"))
    task = {"id": "t", "profile": "firmware", "agent": "lead", "fixture": "empty",
            "prompt": "x", "check": "touch check-was-here && git status --short >/dev/null && git diff --quiet HEAD"}
    res = evals_run.run_task(task, tmp_path / "o", [str(tmp_path / "n")])
    assert res["pass"] is True  # git works in the copy
    ws = tmp_path / "o" / "t" / "run-1" / "workspace"
    assert ws.is_dir() and not (ws / "check-was-here").exists()
