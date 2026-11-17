"""The `agent` launcher: env precedence, render, exec."""
import json
import os
import subprocess


def run_launcher(root, tmp_path, args, extra_env=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "opencode"
    fake.write_text('#!/usr/bin/env bash\n'
                    'echo "CONFIG=$OPENCODE_CONFIG"\necho "MODEL=$AP_MODEL_BUILDER"\n'
                    'echo "URL=$AP_LLM_BASE_URL"\necho "KEY=$CORP_LLM_KEY"\necho "PROFILE=$AP_PROFILE"\n'
                    'echo "ARGS=$*"\n')
    fake.chmod(0o755)
    home = tmp_path / "home"
    cfg = home / ".config" / "agent-platform"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "site.env").write_text("AP_LLM_BASE_URL=https://site.example/v1\nAP_MODEL_BUILDER=corp/site-model\n")
    (cfg / "secrets.env").write_text("CORP_LLM_KEY=from-secrets\n")
    env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(home), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           **(extra_env or {})}
    proc = subprocess.run([str(root / "core/bin/agent"), *args], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


def test_env_precedence_and_render(root, tmp_path):
    out = run_launcher(root, tmp_path, ["--headless", "pipeline", "run", "--agent", "lead", "hi"],
                       extra_env={"CORP_LLM_KEY": "from-ci"})
    assert out["URL"] == "https://site.example/v1"         # site.env beats platform.env
    assert out["MODEL"] == "corp/site-model"
    assert out["KEY"] == "from-ci"                         # caller env beats secrets.env
    assert out["PROFILE"] == "pipeline"
    assert out["ARGS"] == "run --model corp/laguna-5.2.1 --agent lead hi"   # G22: agent model passed explicitly
    cfg = json.loads(open(out["CONFIG"]).read())
    assert cfg["permission"]["bash"]["*"] == "deny"        # headless overlay applied
    assert out["CONFIG"].endswith("pipeline.headless.opencode.json")


def test_secrets_used_when_caller_has_none(root, tmp_path):
    out = run_launcher(root, tmp_path, ["firmware"])
    assert out["KEY"] == "from-secrets"


def test_session_runs_as_bot_inside_repo(root, tmp_path):
    repo = tmp_path / "fw"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@gitlab.test:grp/fw.git"], check=True)
    store = tmp_path / "home" / ".config" / "agent-platform" / "bot-tokens.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"firmware": {"grp/fw": {"token": "bot-tok"}}}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "opencode").write_text('#!/usr/bin/env bash\necho "TOKEN=$GITLAB_TOKEN"\n'
                                     'echo "PUSH=$(git remote get-url --push origin)"\n'
                                     'echo "AUTHOR=$GIT_AUTHOR_NAME"\n')
    (bindir / "opencode").chmod(0o755)
    env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path / "home"),
           "XDG_CACHE_HOME": str(tmp_path / "cache")}
    proc = subprocess.run([str(root / "core/bin/agent"), "firmware"], cwd=repo, env=env, capture_output=True,
                          text=True)
    assert proc.returncode == 0, proc.stderr
    out = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    assert out["TOKEN"] == "bot-tok"
    assert out["PUSH"] == "https://gitlab.test/grp/fw.git"
    assert out["AUTHOR"].startswith("agent-firmware (via ")
    assert "acting as bot" in proc.stderr


def test_unknown_profile_fails(root, tmp_path):
    proc = subprocess.run([str(root / "core/bin/agent"), "nope"], capture_output=True, text=True,
                          env={**os.environ, "HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path)})
    assert proc.returncode != 0 and "unknown profile" in proc.stderr


def test_bench_lock_busy(root, tmp_path):
    env = {**os.environ, "AP_LOCK_DIR": str(tmp_path)}
    holder = subprocess.Popen([str(root / "core/bin/bench-lock"), "bench-a", "--", "sleep", "3"], env=env,
                              stderr=subprocess.DEVNULL)
    try:
        import time
        time.sleep(0.5)
        busy = subprocess.run([str(root / "core/bin/bench-lock"), "bench-a", "--", "true"], env=env,
                              capture_output=True)
        assert busy.returncode == 75
        bad = subprocess.run([str(root / "core/bin/bench-lock"), "Bench A", "--", "true"], env=env,
                             capture_output=True)
        assert bad.returncode == 2
    finally:
        holder.wait()


def test_explicit_model_not_overridden_and_builder_agent(root, tmp_path):
    out = run_launcher(root, tmp_path, ["--headless", "pipeline", "run", "-m", "corp/x", "--agent", "lead", "hi"])
    assert out["ARGS"] == "run -m corp/x --agent lead hi"
    out = run_launcher(root, tmp_path, ["--headless", "pipeline", "run", "--agent=implementer", "hi"])
    assert out["ARGS"] == "run --model corp/site-model --agent=implementer hi"   # site.env beats platform.env
    out = run_launcher(root, tmp_path, ["--headless", "pipeline", "run", "--agent", "nosuch", "hi"])
    assert out["ARGS"] == "run --agent nosuch hi"           # unknown agent: leave args alone
    out = run_launcher(root, tmp_path, ["pipeline", "--agent", "lead"])
    assert out["ARGS"] == "--agent lead"                    # only `run` is touched
