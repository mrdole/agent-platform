# Rules for agents working in agent-platform

This repo is the agent platform itself. Every profile depends on it, so changes need care.

## Before you change anything
- Read `BLUEPRINT.md`. It defines the layout, the schemas and the invariants. If your
  change conflicts with it, either update `BLUEPRINT.md` in the same MR or stop and ask.
- Check whether a skill already exists before you write one. Use the knowledge server's
  `find_skill` tool, or look in `skills/` and `catalog/skills.json` in lab-knowledge.

## Invariants (never break these)
1. No secrets in the repo. Refer to tokens and keys only by environment-variable name.
2. Each skill lives at `skills/<scope>-<name>/SKILL.md`. Its `name` matches the
   directory, and `<scope>` is `core` or an existing profile name.
3. Each profile has `profile.yaml`, `opencode.json` and `RULES.md`. Models are
   referenced only as `{env:AP_MODEL_*}`.
4. The effective `skill` permission starts with `"*": "deny"` and then allows specific
   scopes.
5. Anything that must never happen in a headless run is `deny`, never `ask`, because
   `opencode run --auto` approves `ask`. The headless overlay
   (`core/opencode.headless.json`) defaults bash to `deny`.
6. GitLab enforces the hard limits: no pushes to main, no self-approval. Profile tokens
   and protected branches do that. OpenCode permissions are a second layer only.
7. Agents never merge. They open draft MRs.

## Verify every change
```
python3 tools/check.py
python3 -m pytest -q tests
```
If you changed prompts, skills or a profile's permissions, also run the evals for the
affected profiles and paste the summary table into the MR:
```
python3 evals/run.py --profile <name>
```

## Git
- Branch: `agent/meta/<issue>-<slug>`. Never push to `main`.
- One logical change per MR. Add a line to `CHANGELOG.md` under "Unreleased".
