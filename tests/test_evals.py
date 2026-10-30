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
