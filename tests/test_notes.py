import json
import subprocess
import textwrap

import pytest

import kn
import knowledge_core as kc


@pytest.fixture
def personal(tmp_path, monkeypatch):
    monkeypatch.setenv("KN_TODAY", "2026-10-30")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = tmp_path / "knowledge"
    assert kn.main(["init", str(root)]) == 0
    return root


def run(personal, team, *args):
    return kn.main(["--personal", str(personal), "--team", str(team), *args])


# ---------- store: two sources + FTS ----------
def test_personal_notes_searchable_and_ranked_below_team(personal, knowledge_root):
    (personal / "docs" / "inbox" / "auto.md").write_text("# auto flag\nopencode run --auto approves ask, seen in CI.\n")
    store = kc.KnowledgeStore(knowledge_root, personal=personal)
    assert store.search_engine == "sqlite-fts5"
    hits = store.search_docs("--auto approves ask")
    assert hits[0]["source"] == "team" and hits[1]["path"] == "personal:inbox/auto.md"
    assert hits[1]["status"] == "personal" and hits[1]["title"] == "auto flag"
    assert store.get_doc("personal:inbox/auto.md")["source"] == "personal"
    assert [h["source"] for h in store.search_docs("approves", source="personal")] == ["personal"]


def test_stemming_and_shadow_flag(personal, knowledge_root):
    (personal / "docs" / "gotchas").mkdir()
    (personal / "docs" / "gotchas" / "opencode-headless-ask.md").write_text("my older take on flashing boards\n")
    store = kc.KnowledgeStore(knowledge_root, personal=personal)
    hit = store.search_docs("flash board")[0]          # porter stemming: flash ~ flashing
    assert hit["path"] == "personal:gotchas/opencode-headless-ask.md" and hit["also_in_team"] is True


def test_bm25_fallback_without_fts(personal, knowledge_root, monkeypatch):
    monkeypatch.setattr(kc.KnowledgeStore, "_build_fts", lambda self: setattr(self, "_fts", None))
    store = kc.KnowledgeStore(knowledge_root, personal=personal)
    assert store.search_engine == "bm25"
    assert store.search_docs("--auto approve git push")[0]["path"] == "gotchas/opencode-headless-ask.md"


# ---------- kn ----------
def test_new_todo_log(personal, knowledge_root, capsys):
    assert run(personal, knowledge_root, "new", "LLM proxy on lab subnet", "--kind", "gotcha", "--tags", "proxy,lab") == 0
    note = personal / "docs" / "inbox" / "llm-proxy-on-lab-subnet.md"
    assert "## Symptom" in note.read_text() and "kind: gotcha" in note.read_text()
    run(personal, knowledge_root, "todo", "add", "Probe", "model", "ids", "--due", "2026-10-29", "--tag", "models")
    run(personal, knowledge_root, "todo", "add", "Create bot tokens")
    capsys.readouterr()
    run(personal, knowledge_root, "todo")
    out = capsys.readouterr().out
    assert "1  Probe model ids @due(2026-10-29) #models" in out and "LATE" in out and "2  Create bot tokens" in out
    run(personal, knowledge_root, "todo", "done", "1")
    capsys.readouterr()
    run(personal, knowledge_root, "todo")
    assert "Probe" not in capsys.readouterr().out
    run(personal, knowledge_root, "log", "installed", "platform", "on", "WSL")
    journal = (personal / "docs" / "journal" / "2026-10.md").read_text()
    assert "## 2026-10-30" in journal and "- installed platform on WSL" in journal
    capsys.readouterr()
    run(personal, knowledge_root, "search", "bot tokens")
    assert "personal:tasks.md" in capsys.readouterr().out


def test_capture_ask_review_with_fake_model(personal, knowledge_root, monkeypatch, capsys):
    calls = []

    def fake_chat(messages, model, env):
        calls.append((model, messages))
        if "JSON only" in messages[0]["content"]:
            return ('<think>hmm</think>{"title": "Lab subnet needs proxy for LLM", "kind": "gotcha", '
                    '"tags": ["Proxy", "lab"], "body": "## Symptom\\ncurl timeout\\n## Cause\\nno NO_PROXY"}')
        if messages[0]["content"].startswith("Answer"):
            return "Use deny, not ask [gotchas/opencode-headless-ask.md]."
        return "## Done\n- installed"

    monkeypatch.setattr(kn, "CHAT", fake_chat)
    monkeypatch.setenv("AP_MODEL_SMALL", "corp/gpt-oss-20b")
    monkeypatch.setenv("AP_MODEL_PLANNER", "corp/laguna-5.2.1")
    assert run(personal, knowledge_root, "capture", "curl", "to", "llm", "times", "out", "from", "lab") == 0
    note = (personal / "docs" / "inbox" / "lab-subnet-needs-proxy-for-llm.md").read_text()
    assert "kind: gotcha" in note and "- proxy" in note and "curl timeout" in note
    assert calls[0][0] == "gpt-oss-20b"
    capsys.readouterr()
    assert run(personal, knowledge_root, "ask", "why did --auto push") == 0
    out = capsys.readouterr().out
    assert "[gotchas/opencode-headless-ask.md]" in out and "Sources:" in out
    assert calls[-1][0] == "laguna-5.2.1" and "opencode run --auto" in calls[-1][1][1]["content"]
    run(personal, knowledge_root, "log", "installed")
    assert run(personal, knowledge_root, "review", "--save") == 0
    assert (personal / "docs" / "journal" / "review-2026-w44.md").is_file()


def test_capture_refuses_secrets(personal, knowledge_root, monkeypatch):
    fake = "glpat" + "-" + "y" * 22
    monkeypatch.setattr(kn, "CHAT", lambda m, model, env: json.dumps(
        {"title": "token", "kind": "note", "tags": [], "body": f"token: {fake}"}))
    with pytest.raises(SystemExit, match="secret"):
        run(personal, knowledge_root, "capture", "x")


def test_sync_pushes_to_remote(personal, knowledge_root, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "-C", str(personal), "remote", "add", "origin", str(remote)], check=True)
    run(personal, knowledge_root, "todo", "add", "thing")
    assert run(personal, knowledge_root, "sync") == 0
    log = subprocess.run(["git", "-C", str(remote), "log", "--oneline", "main"], capture_output=True, text=True).stdout
    assert "notes: 2026-10-30" in log


def test_promote_into_team_repo_as_valid_draft(personal, knowledge_root, tmp_path):
    team = tmp_path / "team"
    subprocess.run(["cp", "-r", str(knowledge_root), str(team)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(team)], check=True)
    for cmd in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"]):
        subprocess.run(["git", "-C", str(team), *cmd], check=True)
    (personal / "docs" / "inbox" / "wsl-dns.md").write_text(textwrap.dedent("""\
        ---
        title: WSL loses DNS on VPN
        kind: gotcha
        tags: [wsl, dns]
        ---
        ## Symptom
        getent hosts fails
        """))
    assert run(personal, team, "promote", "inbox/wsl-dns.md", "--area", "network", "--owner", "rf-test") == 0
    doc = (team / "docs" / "network" / "wsl-loses-dns-on-vpn.md").read_text()
    assert "status: draft" in doc and "owner: rf-test" in doc and "getent hosts fails" in doc
    branch = subprocess.run(["git", "-C", str(team), "branch", "--show-current"], capture_output=True, text=True)
    assert branch.stdout.strip() == "notes/promote-wsl-loses-dns-on-vpn"
    assert kc.validate(team).errors == []
