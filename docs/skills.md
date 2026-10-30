# Skills: where they live, how they're found, how to reuse others'

## Where skills live

| Place | What goes there | Loaded by |
|-------|-----------------|-----------|
| `agent-platform/skills/` | Shared, reviewed skills for all profiles | Linked into `~/.config/opencode/skills/` by Ansible and the image |
| `<project>/.agents/skills/` or `.opencode/skills/` | Skills specific to one repo | OpenCode, when working in that repo |
| `lab-knowledge/catalog/` | **Index only**: no skills, just the list of all of them | knowledge server `find_skill` |

Which skills a profile may load is controlled by `permission.skill` (scope prefixes). The
discovery paths and format follow the Agent Skills spec
(https://agentskills.io/specification) and OpenCode's skills docs
(https://opencode.ai/docs/skills).

## Finding what already exists

`tools/skills_index.py` scans for `SKILL.md` under `skills/`, `.opencode/skills/`,
`.agents/skills/` and `.claude/skills/`:

```
python3 tools/skills_index.py local ~/repos/* -o /tmp/skills
GITLAB_TOKEN=... python3 tools/skills_index.py gitlab --url https://gitlab.example.internal --group tools -o catalog
```

It writes `skills.json` and `SKILLS.md`, and flags **duplicate names** and **similar
descriptions** (word overlap ≥ 0.5). In lab-knowledge, a scheduled CI job
(`ci/knowledge.gitlab-ci.yml`, `skills-index`) runs the GitLab scan and opens an MR when
the catalog changes. Agents query it with `find_skill("<what I need>")` before writing a
new skill. The `meta-add-skill` skill requires this check.

## Reusing public skills

Public collections worth browsing for patterns:
- https://github.com/anthropics/skills: Anthropic's example skills and the spec. The
  README marks them as demonstration/educational. Some (the document skills) are
  source-available, not open source. Check each skill's license.
- https://github.com/OpenHands/extensions: OpenHands' public skill registry (MIT), with
  one directory per skill under `skills/`.

Don't install from these directly on agent hosts. Vendor what you need through the
`meta-vendor-skill` process: a human brings the files in, every file is read, the skill
is renamed to platform scope, provenance goes in `metadata`, and it lands by MR with an
eval.
