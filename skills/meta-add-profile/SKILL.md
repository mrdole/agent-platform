---
name: meta-add-profile
description: Add a new agent profile (a new kind of job) to agent-platform - profile.yaml, opencode.json overlay, RULES.md, skills scope, token, evals. Use when someone wants the platform to handle a new kind of work.
---
# Adding a profile

A new profile is justified when the job needs a different **trust boundary**: different
token or role, different hosts or networks, or different hardware. A different prompt
alone is not enough. Use a skill instead.

1. Pick a name: lowercase, hyphens, no `--`. It becomes the skill scope and the
   `profile::<name>` label.
2. Create `profiles/<name>/`:
   - `profile.yaml`: name, description, default_agent, gitlab.{token_env, role, scopes},
     runner_tags, skill_scopes. Use `reporter` role unless the profile must push.
   - `opencode.json`: an overlay only. `instructions: ["./RULES.md"]`, bash allowances,
     `skill: {"<name>-*": "allow"}`, any MCP servers, and agent overrides. Don't copy core
     settings.
   - `RULES.md`: 4-8 rules specific to this job.
   - `prompts/<agent>.md` only for new primary agents.
3. Add at least one skill `skills/<name>-<topic>/SKILL.md` (skill `meta-add-skill`).
4. Add at least two evals in `evals/<name>/` with fixtures.
5. Add a row to the profiles table in BLUEPRINT.md section 6, and a token row to
   docs/security.md.
6. Verify:
   ```text
   python3 tools/check.py
   python3 tools/render.py <name> --headless --print
   python3 -m pytest -q tests
   ```
7. In the MR, list the GitLab token a maintainer must create (name, role, scopes, which
   projects) and the CI variable name.
