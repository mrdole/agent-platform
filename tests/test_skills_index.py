import json
import urllib.parse

import skills_index as si


def test_local_index_of_platform(root, tmp_path):
    entries = si.discover_local([root])
    names = {e["name"] for e in entries}
    assert {"core-handoff", "bench-flash", "meta-vendor-skill"} <= names
    assert all(not e["problems"] for e in entries)


def test_duplicates_and_similar(root, tmp_path):
    other = tmp_path / "firmware-repo"
    (other / ".agents" / "skills" / "bench-flash").mkdir(parents=True)
    (other / ".agents" / "skills" / "bench-flash" / "SKILL.md").write_text(
        "---\nname: bench-flash\ndescription: Flash firmware to the board.\n---\nbody\n")
    (other / ".opencode" / "skills" / "flash-board").mkdir(parents=True)
    (other / ".opencode" / "skills" / "flash-board" / "SKILL.md").write_text(
        "---\nname: flash-board\ndescription: Flash a firmware image to a board on a lab bench safely, with "
        "bench locking, human approval and post-flash verification.\n---\nbody\n")
    (other / "skills" / "Bad_Skill").mkdir(parents=True)
    (other / "skills" / "Bad_Skill" / "SKILL.md").write_text("no frontmatter")
    cat = si.build_catalog(si.discover_local([root, other]))
    assert "bench-flash" in cat["duplicates"]
    assert any({s["a"], s["b"]} == {"bench-flash", "flash-board"} for s in cat["similar"])
    bad = next(e for e in cat["skills"] if e["path"].endswith("Bad_Skill/SKILL.md"))
    assert bad["problems"]


def test_write_outputs_keeps_timestamp_when_unchanged(root, tmp_path):
    entries = si.discover_local([root])
    first = si.build_catalog(entries)
    si.write_outputs(first, tmp_path)
    before = (tmp_path / "skills.json").read_bytes()
    second = {**si.build_catalog(entries), "generated_at": "2099-01-01T00:00:00+00:00"}
    si.write_outputs(second, tmp_path)
    assert (tmp_path / "skills.json").read_bytes() == before


def test_gitlab_discovery_with_fake_api():
    skill = b"---\nname: pipeline-lint\ndescription: Lint CI files before pushing.\n---\nbody\n"

    def opener(url, headers):
        assert headers["PRIVATE-TOKEN"] == "t"
        path = urllib.parse.urlsplit(url).path
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if path.endswith("/groups/tools/projects"):
            body = [{"id": 7, "default_branch": "main", "path_with_namespace": "tools/templates",
                     "web_url": "https://gl/tools/templates"}]
            return 200, json.dumps(body).encode(), {"X-Next-Page": ""}
        if path.endswith("/projects/7/repository/tree"):
            if q["path"] == ".agents/skills":
                return 200, json.dumps([{"type": "tree", "name": "pipeline-lint",
                                         "path": ".agents/skills/pipeline-lint"}]).encode(), {}
            return 404, b"", {}
        if "/repository/files/" in path:
            return 200, skill, {}
        return 404, b"", {}

    entries = si.discover_gitlab(si.GitLab("https://gl", "t", opener=opener), "tools")
    assert len(entries) == 1
    e = entries[0]
    assert e["name"] == "pipeline-lint" and e["project"] == "tools/templates"
    assert e["web_url"] == "https://gl/tools/templates/-/blob/main/.agents/skills/pipeline-lint/SKILL.md"
