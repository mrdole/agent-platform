---
name: core-handoff
description: Structured format for passing work between agents (scout to implementer, implementer to tester and reviewer). Use whenever you finish a step another agent will continue, or when you receive work from another agent.
---
# Handoff format

Hand off a small, structured artifact, never your transcript. The next agent starts from
this and nothing else.

## Scout → implementer

```text
TASK: <one line>
ACCEPTANCE:
- <criterion, copied verbatim from the issue/spec>
FILES:
- <path>: <why it matters, one line>
EXCERPTS:
- <path>:<start>-<end>: <the few lines that matter, or a one-line summary>
DEPENDENCIES: <build/test commands, containers, services, other repos involved>
ASSUMPTIONS:
- <something you believe but did not verify>
OPEN QUESTIONS:
- <something that blocks a correct change>
```

Rules:
- 300 words or fewer. At most 8 files.
- Read every file you list. Put guesses under ASSUMPTIONS, never under FILES.
- If OPEN QUESTIONS block an acceptance criterion, say so on the first line.

## Implementer → tester

Give the tester only ACCEPTANCE (verbatim) and CHECKS: the exact commands from the repo's
AGENTS.md or the profile rules. Do not say what you changed or that it works. The tester
must judge from the repo state alone.

## Implementer → reviewer

Give the reviewer ACCEPTANCE and the diff (`git diff`, or `git diff <base>...HEAD`). Leave
out your explanation. The diff has to stand on its own.

## Receiving a handoff

- Work from the handoff. Read more only when it's missing something you need, and name
  what was missing in your report.
- If a listed file or excerpt doesn't match the repo, trust the repo and report the
  mismatch.
