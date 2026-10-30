# Notes profile rules

You work in the user's personal knowledge repo (run `agent notes` from inside it).

- Edit only files under `docs/` in this repo. Never edit the team lab-knowledge repo. Use
  `kn promote` for that, and only after the user agrees.
- Keep every concrete detail the user wrote (commands, error text, versions, hostnames).
  Restructure notes; don't paraphrase facts away. Never invent facts to fill a section;
  write `_unknown_` instead.
- No secrets. If you find a password, token or key, replace it with `<redacted>` and tell
  the user.
- Tasks live in `docs/tasks.md` and are changed with `kn todo`. The work log lives in
  `docs/journal/` and is changed with `kn log`. Don't hand-edit their format.
- Before creating a note, `kn search` for an existing one and update it instead.
- `kn sync` pushes to GitLab. Ask before running it.
