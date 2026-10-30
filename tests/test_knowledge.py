import datetime as dt
from pathlib import Path

import pytest

import knowledge_core as kc

TODAY = dt.date(2026, 10, 29)


def test_search_finds_gotcha_and_flags_draft(knowledge_root):
    store = kc.KnowledgeStore(knowledge_root, today=TODAY)
    hits = store.search_docs("why did --auto approve git push")
    assert hits[0]["path"] == "gotchas/opencode-headless-ask.md"
    assert hits[0]["status"] == "verified" and hits[0]["stale"] is False
    proxy = store.search_docs("NODE_EXTRA_CA_CERTS proxy")[0]
    assert proxy["path"] == "network/llm-server-access.md"
    assert proxy["status"] == "draft" and proxy["stale"] is True  # no last_verified


def test_profile_filter_hides_docs_and_devices(knowledge_root):
    fw = kc.KnowledgeStore(knowledge_root, profile="firmware", today=TODAY)
    it = kc.KnowledgeStore(knowledge_root, profile="it-debug", today=TODAY)
    assert not any(h["path"] == "it/core-switch-layout.md" for h in fw.search_docs("VLAN lab subnet"))
    assert it.search_docs("VLAN lab subnet")[0]["path"] == "it/core-switch-layout.md"
    with pytest.raises(PermissionError):
        fw.get_doc("it/core-switch-layout.md")
    assert it.list_devices() == []  # devices default to bench+firmware
    assert {d["name"] for d in fw.list_devices("bench-a")} == {"sa-01", "sg-01", "board-a"}


def test_get_doc_and_traversal(knowledge_root):
    store = kc.KnowledgeStore(knowledge_root, today=TODAY)
    doc = store.get_doc("docs/gotchas/opencode-headless-ask.md")
    assert "--auto" in doc["body"]
    with pytest.raises(ValueError):
        store.get_doc("../inventory/devices.yaml")
    with pytest.raises(FileNotFoundError):
        store.get_doc("nope.md")


def test_device_lookup_includes_bench(knowledge_root):
    dev = kc.KnowledgeStore(knowledge_root).get_device("sg-01")
    assert dev["visa"].startswith("TCPIP0::") and dev["bench_info"]["runner_tag"] == "bench-a"


def test_find_skill(knowledge_root):
    hits = kc.KnowledgeStore(knowledge_root).find_skill("program the board with new firmware")
    assert hits[0]["name"] == "bench-flash"


def test_refresh_picks_up_new_docs(knowledge_root):
    store = kc.KnowledgeStore(knowledge_root, today=TODAY)
    assert store.search_docs("zebrafish") == []
    (knowledge_root / "docs" / "network" / "zebrafish.md").write_text(
        "---\ntitle: Zebrafish proxy\nkind: gotcha\napplies_to: [all]\nstatus: draft\nowner: x\n---\nzebrafish\n")
    assert store.search_docs("zebrafish")[0]["path"] == "network/zebrafish.md"


def test_validate_fixture_clean_and_catches_problems(knowledge_root):
    profiles = {"firmware", "pipeline", "bench", "it-debug", "meta"}
    assert kc.validate(knowledge_root, profiles, today=TODAY).errors == []
    (knowledge_root / "docs" / "network" / "Bad Name.md").write_text(
        "---\ntitle: t\nkind: rumor\napplies_to: [nobody]\nstatus: verified\nowner: x\n---\nbody\n")
    errs = kc.validate(knowledge_root, profiles, today=TODAY).errors
    joined = "\n".join(errs)
    assert "lowercase-hyphenated" in joined
    assert "kind must be one of" in joined
    assert "applies_to 'nobody'" in joined
    assert "verified docs need last_verified" in joined


def test_real_lab_knowledge_repo_validates(root):
    lk = root.parent / "lab-knowledge"
    if not lk.is_dir():
        pytest.skip("lab-knowledge not checked out next to agent-platform")
    profiles = {p.name for p in (root / "profiles").iterdir() if p.is_dir()}
    res = kc.validate(lk, profiles)
    assert res.errors == [], res.errors
    hits = kc.KnowledgeStore(lk, profile="it-debug").search_docs("git push ask approved --auto")
    assert hits and hits[0]["path"] == "gotchas/opencode-headless-ask.md"
