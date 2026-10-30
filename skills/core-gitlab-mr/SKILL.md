---
name: core-gitlab-mr
description: Branch, commit, push and open a draft GitLab merge request with an evidence packet. Use when work is ready for human review in an interactive session. Not used in dispatcher runs, where the dispatcher commits and opens the MR.
compatibility: Requires git and glab configured for the internal GitLab (GITLAB_HOST and a profile token).
---
# Opening a merge request

1. **Branch:** `agent/<profile>/<issue-iid>-<short-slug>`, e.g.
   `agent/firmware/142-uart-baud`. Never commit on `main`.
2. **Commit:** imperative subject of 72 characters or fewer, a body that says why, and
   these trailers:
   ```text
   Agent-Profile: <profile>
   Refs: #<issue-iid>
   ```
3. **Push only your branch:** `git push -u origin HEAD`. Never `--force`, never to main.
4. **Write the description** to `.agent-run/mr.md` using the template below, then:
   ```text
   glab mr create --draft --yes --title "<subject>" --description "$(cat .agent-run/mr.md)" --label "agent::review" --remove-source-branch
   ```
5. Report the MR URL. Never merge, approve, or mark ready. A human does that.

## Description template (evidence packet)

```markdown
## What changed
<files and one-line reasons>

## Why
<link to issue, acceptance criteria>

## Evidence
| Check | Command | Exit | Key output |
|-------|---------|------|------------|

## Not verified
<anything you could not check, and why>

## Risks / rollback
<what could break, how to revert>

Refs #<iid>
```

Only list commands you actually ran in this session.
