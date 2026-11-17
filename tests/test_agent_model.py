import agent_model

CFG = {"model": "{env:AP_MODEL_BUILDER}",
       "agent": {"lead": {"model": "{env:AP_MODEL_PLANNER}", "mode": "primary"}, "x": {"mode": "primary"}}}
ENV = {"AP_MODEL_PLANNER": "corp/laguna-5.2.1", "AP_MODEL_BUILDER": "corp/gpt-oss-120b"}


def test_agent_model_resolves_env():
    assert agent_model.agent_model(CFG, "lead", ENV) == "corp/laguna-5.2.1"


def test_agent_model_missing_cases():
    assert agent_model.agent_model(CFG, "x", ENV) is None        # no model on agent
    assert agent_model.agent_model(CFG, "nope", ENV) is None     # unknown agent
    assert agent_model.agent_model(CFG, "lead", {}) is None      # unset variable


def test_cli(tmp_path, monkeypatch, capsys):
    import json
    f = tmp_path / "c.json"
    f.write_text(json.dumps(CFG))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    assert agent_model.main(["lead", "--config", str(f)]) == 0
    assert capsys.readouterr().out.strip() == "corp/laguna-5.2.1"
    assert agent_model.main(["x", "--config", str(f)]) == 1
