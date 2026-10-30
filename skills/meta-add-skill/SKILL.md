---
name: meta-add-skill
description: Write a new skill (SKILL.md) for agent-platform following the Agent Skills spec and platform naming rules, after checking that no equivalent skill already exists. Use when a repeated procedure should become reusable instructions.
---
# Adding a skill

1. **Check for duplicates first.** Run `find_skill("<what it does>")` on the knowledge
   server, and look in `skills/`. If something close exists, extend it instead.
2. **Name it** `<scope>-<topic>`. Scope is `core` (all profiles) or a profile name.
   Lowercase letters, digits and single hyphens; 64 characters max; the directory name
   equals `name`.
3. **Frontmatter.** Only these fields are recognized: `name`, `description`,
   `license`, `compatibility`, `metadata`.
   - `description` (max 1024 characters) says **what** the skill does and **when** to
     use it, with the keywords an agent would search for.
   - `compatibility` (max 500 characters) only if it needs specific tools or access.
   - `metadata` holds string values only, e.g. provenance for vendored skills.
4. **Body.** Steps, a table or template for the output, and stop conditions. Keep it
   under about 150 lines. Put long references in `references/` and scripts in
   `scripts/`, and refer to them with relative paths.
5. **Make sure the profile can load it.** Its scope must be allowed in that profile's
   `permission.skill`.
6. **Verify:**
   ```text
   python3 tools/check.py
   ```
   Then add or adjust an eval that exercises the skill, run it, and include the result
   in the MR.
