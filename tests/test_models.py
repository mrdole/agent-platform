import json

import pytest

import knowledge_core as kc
import models


@pytest.fixture
def lk(root):
    path = root.parent / "lab-knowledge"
    if not (path / "models" / "registry.yaml").is_file():
        pytest.skip("lab-knowledge not checked out next to agent-platform")
    return path


def top(lk, task, **kw):
    return [r["id"] for r in kc.recommend_models(lk, task, **kw)["results"]]


def test_registry_validates(lk):
    v = kc.Validation()
    kc.validate_models(lk, v)
    assert v.errors == []


def test_recommendations_match_intent(lk):
    assert top(lk, "write SQL against the test results database")[0] == "snowflake-arctic-text2sql"
    assert top(lk, "read a spectrum analyzer screenshot", modality="image")[0] == "nemotron-3-nano-omni-30b"
    assert "laguna-5.2.1" in top(lk, "plan and orchestrate a multi-step firmware change")[:2]
    assert top(lk, "review this merge request diff")[0] in {"gemma-4-31b", "laguna-5.2.1"}
    # small models are not suggested for orchestration
    assert "gemma-4-e4b" not in top(lk, "orchestrate agents", k=10)


def test_interactive_models_kept_out_of_agent_picks(lk):
    res = kc.recommend_models(lk, "plan and review a firmware change", k=10)
    assert not any(r["id"].startswith("copilot-") for r in res["results"])
    assert any(a["id"] == "copilot-hydrafusion" for a in res["interactive_only"])
    only = kc.recommend_models(lk, "review a diff", location="github-copilot")
    assert only["results"] and all(r["id"].startswith("copilot-") for r in only["results"])
    with pytest.raises(SystemExit):
        models.probe(lk, "github-copilot")


def test_location_filter_and_unknown_task(lk):
    assert kc.list_models(lk, "nowhere") == []
    res = kc.recommend_models(lk, "xyzzy")
    assert res["results"] == [] and res["hint"]


def test_validation_catches_bad_entries(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "registry.yaml").write_text(
        "locations: [{name: a, base_url: http://x}]\n"
        "models:\n"
        "  - {id: m1, name: M1, locations: [b], good_for: [juggling], tool_calling: great, confidence: measured}\n")
    v = kc.Validation()
    kc.validate_models(tmp_path, v)
    joined = "\n".join(v.errors)
    for needle in ("unknown location 'b'", "'juggling' not in vocabulary", "tool_calling must be",
                   "measured confidence needs evidence"):
        assert needle in joined


def test_probe_diffs_served_models(lk, monkeypatch):
    monkeypatch.setenv("AP_LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("CORP_LLM_KEY", "k")

    def opener(url, headers):
        assert url == "https://llm.test/v1/models" and headers["Authorization"] == "Bearer k"
        return json.dumps({"data": [{"id": "laguna-5.2.1"}, {"id": "gpt-oss-120b"}, {"id": "new-model"}]}).encode()

    res = models.probe(lk, "corp-llm", opener)
    assert res["ok"] == ["gpt-oss-120b", "laguna-5.2.1"]
    assert res["served_not_registered"] == ["new-model"]
    assert "phi-4" in res["registered_not_served"]
    assert not any(i.startswith("copilot-") for i in res["registered_not_served"])


def test_provider_json_covers_core_models(lk, root):
    block = models.provider_models(lk, "corp-llm")
    core = json.loads((root / "core" / "opencode.core.json").read_text())
    assert set(core["provider"]["corp"]["models"]) <= set(block)
