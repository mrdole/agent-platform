---
name: notes-curate
description: Curate the user's personal knowledge repo - file rough notes, restructure them by kind, merge duplicates, move notes out of the inbox, keep tasks and the work log tidy, and pick notes ready to promote to the team lab-knowledge repo. Use in the notes profile.
---
# Curating personal notes

## Note shape
```markdown
---
title: <specific, searchable: name the system and the symptom>
kind: note | gotcha | howto | reference
tags: [3-6 lowercase tags]
created: YYYY-MM-DD
---
```
Section layout by kind:
- **gotcha:** Symptom (exact error text), Cause, Fix, How confirmed.
- **howto:** Steps, Verify.
- **reference:** Facts (tables welcome).
- **note:** free form.

## Inbox pass
1. `ls docs/inbox/`. For each note:
   - Duplicate of an existing note (`kn search "<title words>"`)? Merge into the older note,
     keeping every detail, and delete the new file.
   - Unclear? Fix the title and tags, and put the content into the sections for its kind.
     Missing info becomes `_unknown_`; never guess.
   - Settled? Move it to `docs/<topic>/` (network, tooling, bench, gitlab, firmware...).
2. Run `kn check`: no secrets, everything parses.

## Promotion candidates
A note is ready for the team repo when it is all of these:
- **Confirmed:** "How confirmed" names what was checked, where and when.
- **Useful beyond you:** it describes an environment fact or procedure, not a personal preference.
- **Not already covered:** `kn search --source team "<topic>"` finds nothing that covers it.

Propose the command; run it only after the user says yes:
```text
kn promote personal:<path> --area <network|tooling|bench|gitlab|gotchas> --kind <gotcha|howto|reference>
```

## Tracking
- Tasks: `kn todo add "..." --due YYYY-MM-DD --tag <topic>`, `kn todo`, `kn todo done <id>`.
- Log: `kn log "<what happened, with the key command or result>"`.
