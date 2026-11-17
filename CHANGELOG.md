# Changelog

## Unreleased
- G22: OpenCode 2.0.27 `run --agent <primary>` ignored the agent's own `model` (lead/analyst ran on the builder,
  gpt-oss-120b, not the planner). The `agent` launcher now passes `--model` from the rendered config
  (`tools/agent_model.py`) for `run --agent X` unless `--model`/`-m` is given; covers dispatch and evals.
- `tools/dispatch.py`: headless runs pass `--standalone` (G15: no more sharing the OpenCode 2.x background service's
  config); the target-repo refusal list now includes `.claude` and `.agents` (G20; OpenCode 2.0.27's config discovery
  reads `.claude`, `.agents`, `.opencode`, `opencode.json`, `opencode.jsonc`).
- G19: `render.py` merges 2.x `permissions` lists and re-appends hard core denies last, so a
  profile or headless overlay cannot drop or override them. `check.py` fails any profile whose
  render loosens a core deny (normal and headless, 1.x object and 2.x list shapes).
- G14: pin OpenCode **2.0.27** (D14, provisional; verified 2026-11-17 in a RHEL 9 container). npm has no
  2.x, so `opencode_install` now defaults to `archive` (tar.gz with the `opencode` executable at its top
  level, URL in `opencode_archive_url`, no built-in URL); the tools role fails early on npm + 2.x or
  archive + empty URL. `agent doctor` hint, setup wizard warning and defaults, README and BLUEPRINT
  section 11 updated (verified / not verified lists).
- `evals/run.py` (G18): `--repeat N` with pass rates (baseline compares rates), `--standalone` on by
  default (`--no-standalone` to drop), `--format json` runs with per-run `tools.jsonl`, `raw.jsonl` and
  denied-call counts, a `versions` block in `results.json`, and checks run in a copy of the workspace.
  `evals/agent_cmd_pi.sh`: untested Pi wrapper for `AP_EVAL_AGENT_CMD`.
- `tools/setup_wizard.py`: guided install. Asks for each site setting, shows the `site.yml` diff,
  then offers bootstrap, the model key (typed hidden into `secrets.env`), `agent doctor`, the
  model probe, the bot-token plan/apply and a smoke eval, one step at a time. `--dry-run`
  changes nothing.
- `tools/gitlab_bots.py`: group access tokens, `protected_paths` refusal and bot audit,
  `branch_lockdown` with `--branches`, and `export` for handing tokens to an agent user or
  container. `tools/botenv.py`: group token lookup and `AP_BOT_TOKENS`.
- Model registry + lookup: `tools/models.py` (list, recommend, probe /models, provider block);
  `list_models` / `recommend_model` knowledge tools; Copilot HydraFusion registered as interactive-only.
- Bot identities: `tools/gitlab_bots.py` (create/rotate project access tokens with your PAT, CI vars,
  protect main, labels); `tools/botenv.py` makes every `agent` session push/act as the profile's bot.
- RHEL 9 install: `bootstrap.sh`, site settings in `~/.config/agent-platform/site.yml`, Python 3.12
  venv (MCP SDK needs 3.10+), OpenCode pinned (originally npm 1.19.5, see G14 above), ansible-core 2.14 compatible,
  `agent doctor`.
- Knowledge: SQLite FTS5 search with BM25 fallback; personal notes repo as a second source;
  `kn` (capture/ask/review with your models, todo, log, sync, promote); `notes` profile + `notes-curate` skill.
- BLUEPRINT section 12: decisions.

## 0.1.0
- Initial layout: core config + headless overlay, five profiles (firmware, pipeline,
  bench, it-debug, meta), twelve skills, knowledge and bench MCP servers, skills index,
  issue dispatcher, evals, Ansible installer for RHEL 9.
