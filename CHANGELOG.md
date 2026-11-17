# Changelog

## Unreleased
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
  venv (MCP SDK needs 3.10+), OpenCode via npm (pinned 1.19.5), ansible-core 2.14 compatible,
  `agent doctor`.
- Knowledge: SQLite FTS5 search with BM25 fallback; personal notes repo as a second source;
  `kn` (capture/ask/review with your models, todo, log, sync, promote); `notes` profile + `notes-curate` skill.
- BLUEPRINT section 12: decisions.

## 0.1.0
- Initial layout: core config + headless overlay, five profiles (firmware, pipeline,
  bench, it-debug, meta), twelve skills, knowledge and bench MCP servers, skills index,
  issue dispatcher, evals, Ansible installer for RHEL 9.
