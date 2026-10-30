# Examples

`opencode.minimal.json` — a hand-written OpenCode config for the internal LLM server
with the five core agents (plan, build, scout, tester, reviewer) and no platform
machinery. Use it as `~/.config/opencode/opencode.json` if you want the same models
and agent split on a machine without installing the platform.

Fill in `provider.corp.options.baseURL` (or switch it to `{env:AP_LLM_BASE_URL}`) and
keep the key in `CORP_LLM_KEY`. The platform's own config is
`../core/opencode.core.json`; it adds permissions, the knowledge MCP server and the
rules file, and takes its values from `../core/platform.env`.

Do not put an `opencode.json` in a project repo that agents run in: it is loaded after
the profile config and overrides it. See `docs/gotchas/project-opencode-config-override.md`
in the `lab-knowledge` repo.
