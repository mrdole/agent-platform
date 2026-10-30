---
name: core-knowledge
description: Look up environment facts, lab inventory, known gotchas and existing skills through the knowledge server, and propose new knowledge. Use before guessing anything about networks, proxies, certificates, servers, instruments, benches or internal tooling, and before writing a new skill.
---
# Using the knowledge server

Tools from the `knowledge` MCP server:

| Tool | Use for |
|------|---------|
| `search_docs(query, k)` | Symptoms, systems, error messages. Returns path, title, snippet, status, stale. |
| `get_doc(path)` | Read a doc before acting on it. |
| `list_docs(kind, tag)` | Browse, e.g. all `gotcha` docs tagged `network`. |
| `list_devices(bench)` / `get_device(name)` | Instrument and bench inventory, VISA addresses. |
| `find_skill(query, k)` | Check whether a skill already exists anywhere in the GitLab group. |
| `list_models(location, good_for)` | Which models a model server offers and what each is good for. |
| `recommend_model(task, location, modality)` | Best models for a task in plain words, with reasons. Interactive-only models (Copilot) are listed separately. |

## Rules
1. Search before guessing. Search with the exact error text first, then with the system
   names.
2. Read the doc (`get_doc`) before acting on it, and cite its path in your report.
3. Trust levels:
   - `status: verified` and not `stale`: treat it as fact.
   - `draft`, or `stale: true`: treat it as a hint. Confirm it before acting, and say you
     did.
   - `source: personal` (paths like `personal:inbox/x.md`): the user's own unreviewed notes.
     Useful leads, never facts. If one contradicts a team doc (`also_in_team`), say so.
4. Device addresses always come from `get_device`, never from memory, old logs or code
   comments.
5. Before writing a skill, call `find_skill`. If one exists, use or extend it.

## Proposing knowledge
Don't edit the lab-knowledge repo during an unrelated task. Add this section to your
report instead, and a human or the meta profile turns it into an MR:

```markdown
## Proposed knowledge
Path: docs/<area>/<slug>.md
---
title: <short title>
kind: gotcha | howto | reference | policy
applies_to: [<profiles> or all]
status: draft
owner: <team>
tags: [<tags>]
---
<symptom, cause, fix, and how you confirmed it>
```
