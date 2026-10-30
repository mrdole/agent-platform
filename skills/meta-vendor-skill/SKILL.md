---
name: meta-vendor-skill
description: Import a third-party skill (from anthropics/skills, OpenHands/extensions or another repo) into agent-platform after reviewing every file, renaming it to platform scope and recording provenance. Use when someone wants to reuse an external skill.
---
# Vendoring an external skill

External skills are text plus optional scripts that agents will run. Treat them like
code from the internet.

1. Get the files through a human. Agents in this platform have no web access. Ask for
   the skill directory to be placed under `vendor-inbox/<name>/` on a branch.
2. Read **every** file: SKILL.md, `scripts/`, `references/`, `assets/`. Flag anything
   that:
   - makes network calls, downloads, or sends data anywhere,
   - runs shell commands not obviously needed,
   - touches credentials, home directories or system config,
   - has a license that isn't compatible (check `license` and any LICENSE file).
3. Rename it to `<scope>-<topic>` and move it to `skills/`. Fix the `name` field to
   match.
4. Record provenance in frontmatter:
   ```yaml
   metadata:
     source: "<repo URL>/<path>"
     source_commit: "<sha>"
     reviewed_by: "<person>"
     license: "<license>"
   ```
5. Delete `vendor-inbox/`. Run `python3 tools/check.py`, and add an eval for the skill.
6. MR title prefix: `[vendor]`. List what you removed or changed from the original, and
   why.
