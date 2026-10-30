import os
import subprocess

import doctor


def by_name(results):
    return {name: (ok, required, detail) for name, ok, required, detail, _ in results}


def test_missing_setup_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    env = {"AP_VENV": str(tmp_path / "novenv"), "PATH": "/usr/bin:/bin"}
    r = by_name(doctor.checks(env))
    assert r["platform venv (Python >= 3.10)"][0] is False
    assert r["CORP_LLM_KEY"][0] is False and r["CORP_LLM_KEY"][1] is True
    assert r["lab-knowledge checkout"][0] is False
    assert r["GitLab bot tokens"][1] is False          # optional


def test_configured_machine_passes_core_checks(tmp_path, monkeypatch, knowledge_root):
    import sys
    cfg = tmp_path / "cfg" / "agent-platform"
    cfg.mkdir(parents=True)
    (cfg / "site.env").write_text("AP_LLM_BASE_URL=https://llm.corp/v1\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    env = {"AP_VENV": str(venv), "AP_LLM_BASE_URL": "https://llm.corp/v1", "CORP_LLM_KEY": "k",
           "AP_KNOWLEDGE_ROOT": str(knowledge_root), "NODE_EXTRA_CA_CERTS": str(ca)}
    r = by_name(doctor.checks(env))
    for name in ("git >= 2.31", "platform venv (Python >= 3.10)", "MCP SDK + PyYAML in venv", "site.env",
                 "model server URL set", "CORP_LLM_KEY", "lab-knowledge checkout", "CA bundle for Node (OpenCode)"):
        assert r[name][0], (name, r[name])


def test_agent_doctor_entrypoint(root, tmp_path):
    proc = subprocess.run([str(root / "core/bin/agent"), "doctor"], capture_output=True, text=True,
                          env={**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "c")})
    assert "doctor:" in proc.stdout and "[FAIL] CORP_LLM_KEY" in proc.stdout
