---
name: pipeline-debug-ci
description: Diagnose and fix failing GitLab CI pipelines and jobs - YAML and include errors, template ref problems, runner tags, missing images or variables, script failures. Use when a pipeline is red or a .gitlab-ci.yml or CI template change is needed.
compatibility: Requires glab configured for the internal GitLab.
---
# Debugging a pipeline

1. **Find it:** `glab ci list` / `glab ci view` and pick the failing job.
2. **Read the log:** `glab ci trace <job-id>`. Find the first real error. Ignore cleanup
   noise after it.
3. **Classify:**
   | Class | Signs | Action |
   |-------|-------|--------|
   | Config | lint errors, unknown keys, bad `rules` | fix YAML, `glab ci lint` |
   | Include/template | wrong ref, missing file, changed template | check pinned ref; template changes go to the template repo |
   | Environment | image pull, runner tag, missing variable, CA/proxy | report; search knowledge for the gotcha |
   | Code | real test/build failure | not a pipeline problem: hand off with the error |
4. **Lint before handing off:** `glab ci lint`.
5. **Make the minimal fix.** Don't bump images, template refs or tool versions unless
   that is the fix.
6. Never read or change CI/CD variables or runner settings. List what a maintainer needs
   to change under Follow-ups.

## Report
- Failing job and first error line
- Class and root cause
- Fix (or why there is no fix in this repo)
- Lint result
