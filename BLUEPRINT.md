# agent-platform blueprint

This is the design reference. It should be complete enough for an agent (or a person) to
rebuild the repo from scratch, and to change it without breaking its invariants. When
the repo and this file disagree, fix one of them in the same MR.

## 1. Purpose

Run coding and operations agents on internal LLMs, for several kinds of work, with:
- one shared core (models, rules, launcher, CI, install), and
- per-job **profiles** that differ in trust: token, GitLab role, tools, hosts, hardware.

GitLab is the job queue and the record (issues in, draft MRs out). Deterministic checks
outside the agent decide whether work is good. Platform changes go through MRs and evals,
including changes the platform proposes to itself.

## 2. Concepts

| Concept | What it is | Where |
|---------|------------|-------|
| Core | Provider + model roles, base permissions, default agents, global rules, knowledge MCP | `core/` |
| Profile | Overlay for one kind of job: rules, extra tools, permissions, skill scopes, token, runner | `profiles/<name>/` |
| Agent | An OpenCode agent (primary or subagent) with a model role, prompt and permissions | core `agent` + profile overrides |
| Skill | Agent Skills `SKILL.md`, loaded on demand; name prefix = scope | `skills/<scope>-<topic>/` |
| Knowledge | Facts, gotchas, inventory, skills catalog, served by MCP | separate `lab-knowledge` repo |
| Eval | Golden task + deterministic check, run against real models | `evals/` |
| Dispatcher | CI job that turns labelled issues into agent runs and draft MRs | `tools/dispatch.py` |

## 3. Layout

```text
agent-platform/
├── AGENTS.md                 rules for agents editing this repo
├── BLUEPRINT.md              this file
├── README.md                 human quickstart + requirements table
├── bootstrap.sh              fresh-clone setup: dnf ansible-core/git -> site.yml -> playbook -> doctor
├── VERSION, CHANGELOG.md
├── .gitlab-ci.yml            check -> build image (tags) -> evals (schedule/manual)
├── core/
│   ├── opencode.core.json    provider, model roles, base permission, default agents, knowledge MCP
│   ├── opencode.headless.json overlay for unattended runs: bash "*" deny, no questions
│   ├── platform.env          AP_MODEL_* role -> model mapping, defaults
│   ├── rules/base.md         global instructions for every agent
│   ├── prompts/*.md          lead, scout, implementer, tester, reviewer
│   ├── bin/agent             launcher: env + render + exec opencode
│   ├── bin/bench-lock        flock wrapper for same-host bench locking
│   ├── Containerfile         CI image (UBI 9 + opencode + glab + venv)
│   └── certs/                corporate CA certs for the image (not committed)
├── profiles/<name>/
│   ├── profile.yaml          metadata: token, role, runner tags, skill scopes, default agent
│   ├── opencode.json         overlay on core (never provider/model)
│   ├── opencode.headless.json (optional) profile-specific headless overlay
│   ├── RULES.md              profile instructions
│   └── prompts/*.md          only for profile-specific agents
├── skills/<scope>-<topic>/SKILL.md
├── mcp/
│   ├── requirements.txt
│   ├── knowledge/            knowledge_core.py (logic + CLI) + server.py (MCP stdio)
│   └── bench/                bench_core.py (guard rails) + server.py (MCP stdio)
├── tools/
│   ├── render.py             core + profile (+ headless) -> one OpenCode config
│   ├── check.py              repo validator (invariants in section 8)
│   ├── skills_index.py       SKILL.md catalog across local dirs or a GitLab group
│   ├── models.py             model lookup per location: list, recommend, probe /models, provider block
│   ├── botenv.py             per-session bot identity (token, push route, author) - used by core/bin/agent
│   ├── gitlab_bots.py        create/rotate bot tokens per (profile, project) with your PAT
│   ├── doctor.py             `agent doctor`: checks every requirement, prints the fix
│   ├── setup_wizard.py       guided install: prompts for site.yml, then offers each setup step (stdlib only)
│   ├── kn.py                 personal notes: capture/ask/review (your models), todo, log, sync, promote
│   └── dispatch.py           issue -> agent -> draft MR
├── evals/
│   ├── run.py                runner + baseline comparison
│   ├── <profile>/*.yaml      tasks
│   ├── fixtures/<name>/      starting repo state per task
│   └── baseline.json         last accepted results (created by a deliberate MR)
├── ci/
│   ├── agent-dispatch.gitlab-ci.yml   include for project repos
│   └── knowledge.gitlab-ci.yml        include for lab-knowledge
├── ansible/                  installer (RHEL 9, ansible.builtin only)
│   ├── site.yml, ansible.cfg, inventory/hosts.yml
│   ├── site.example.yml      per-machine settings template -> ~/.config/agent-platform/site.yml
│   ├── bots.example.yaml     projects per profile for gitlab_bots.py
│   ├── group_vars/{all,workstations,agent_servers}.yml
│   └── roles/{base,tools,agent_user,platform,knowledge}/
├── docs/{security.md,skills.md}
└── tests/                    pytest; no network, no real models
```

## 4. How a session is assembled

`agent [--headless] <profile> [opencode args]`:

1. Environment, low to high precedence: `core/platform.env` → `site.env` (from Ansible,
   `/etc/agent-platform/` then `~/.config/agent-platform/`) → `secrets.env` (same dirs) →
   the caller's environment (CI variables win).
2. Exports `AP_HOME`, `AP_VENV`, `AP_PROFILE`.
3. `tools/render.py <profile> [--headless]` merges, in order:
   `core/opencode.core.json` → `profiles/<p>/opencode.json` → (headless)
   `core/opencode.headless.json` → (headless) `profiles/<p>/opencode.headless.json`.
   Merge rules: objects merge recursively, with new keys appended and existing keys kept
   in place (this preserves permission order, so `"*"` stays first and the last match
   wins). `instructions` lists concatenate. Other lists are replaced. Relative
   `{file:...}` and `instructions` paths become absolute, resolved against the file
   that wrote them.
4. Sets `OPENCODE_CONFIG` to the rendered file.
5. `tools/botenv.py <profile>` exports the session's GitLab bot identity: the token from
   `$<token_env>` or `bot-tokens.json`, an HTTPS push route plus credential helper through
   `GIT_CONFIG_*`, and the commit author. It warns if no token is found, and refuses when
   `AP_REQUIRE_BOT=1`.
6. Execs `opencode`.

OpenCode then layers its own sources: global `~/.config/opencode/opencode.json` (below
ours), project `opencode.json` and `.opencode/` (**above** ours). Project config can
loosen profile permissions, so the dispatcher refuses repos that carry one
(strict mode).

Values that vary by site use OpenCode substitution: `{env:AP_MODEL_*}`,
`{env:AP_LLM_BASE_URL}`, `{env:CORP_LLM_KEY}`, `{env:AP_HOME}`, `{env:AP_VENV}`,
`{env:AP_PROFILE}`, `{env:AP_KNOWLEDGE_ROOT}`. An unset variable becomes an empty string.

## 5. Model roles

| Env var | Default | Used by |
|---------|---------|---------|
| `AP_MODEL_PLANNER` | `corp/laguna-5.2.1` | lead, plan, it-debug analyst |
| `AP_MODEL_BUILDER` | `corp/gpt-oss-120b` | build, implementer, bench operator, default model |
| `AP_MODEL_SMALL` | `corp/gpt-oss-20b` | tester, `small_model` (titles/summaries) |
| `AP_MODEL_REVIEWER` | `corp/gemma-4-31b` | reviewer (a different family from the builder on purpose) |
| `AP_MODEL_SCOUT` | `corp/gemma-4-26b` | scout |

The full model inventory, with what each model is good for, lives in
`lab-knowledge/models/registry.yaml` (schema in that repo's BLUEPRINT section 5).

To change a role's model:
1. Rank candidates with `python3 tools/models.py recommend "<task>"`.
2. Bake them off with evals.
3. Edit `core/platform.env`.
4. If the model is new, add it to the provider in `core/opencode.core.json`
   (`tools/models.py provider-json --location corp-llm` prints the block).
5. Record the eval ids in the registry and set `confidence: measured`.

Candidates worth a bake-off now: `nemotron-3-super-120b` against gpt-oss-120b (builder)
and against Laguna (planner). `nemotron-3-nano-omni-30b` as a vision sub-agent for
instrument screenshots and scope captures.

## 6. Profiles

| Profile | Purpose | Default agent | GitLab role / token env | Extra tools | Skill scopes | Runner tags |
|---------|---------|---------------|-------------------------|-------------|--------------|-------------|
| firmware | Firmware changes, vitis-dev builds, optional flashing | lead | developer / `GITLAB_TOKEN_FIRMWARE` | podman, make, bench-lock (ask) | core, firmware, bench-flash | agent, vitis |
| pipeline | CI configs, templates, job scripts | lead | developer / `GITLAB_TOKEN_PIPELINE` | glab ci (read), lint | core, pipeline | agent |
| bench | Instruments + flashing under lock | operator | developer / `GITLAB_TOKEN_BENCH` | bench MCP, bench-lock | core, bench | agent, bench |
| it-debug | Read-only diagnosis | analyst | reporter / `GITLAB_TOKEN_ANALYST` | read-only system probes | core, it-debug | agent |
| meta | Maintains this repo and lab-knowledge | lead | developer / `GITLAB_TOKEN_META` | check/render/index/evals | core, meta | agent |
| notes | Maintains your personal notes repo | librarian | works as you (`bot_identity: false`) | kn | core, notes | (workstation only) |

**Knowledge sources.** The knowledge server searches the team repo (`AP_KNOWLEDGE_ROOT`,
everywhere) plus, on workstations only, your personal notes (`AP_KNOWLEDGE_PERSONAL`).
Search runs on an in-memory SQLite FTS5 index built from the Markdown on each refresh
(porter stemming, field weights title 10 / tags 5 / body 1), with pure-Python BM25 as a
fallback. Personal notes rank below team docs and are always hints. Results carry `source`,
and personal notes that share a path with a team doc are flagged `also_in_team`.

Default agents in core: `lead` (primary, plans and delegates, no edits), `scout`,
`implementer`, `tester` and `reviewer` (subagents), plus OpenCode's built-in `build` and
`plan` with model roles set. Subagents can't launch subagents (`task: deny`).

## 7. Schemas

### profile.yaml
```yaml
name: <dir name>
description: <one line>
default_agent: <enabled agent>
gitlab:
  token_env: GITLAB_TOKEN_<NAME>
  role: reporter | developer
  scopes: [read_api, ...]
runner_tags: [agent, ...]
skill_scopes: [core, <profile>, <individual skill names>]
headless_auto_approved: ["<bash pattern>", ...]   # "ask" rules deliberately auto-approved in headless runs
```

### Profile opencode.json (overlay)
Allowed top-level keys: `instructions`, `mcp`, `permission`, `agent`. Never `provider`,
`model`, `small_model` or `enabled_providers`, which belong in core. Agent `model` is
always `{env:AP_MODEL_*}`. Agent `prompt` is `{file:./prompts/<agent>.md}`, or a
relative path to `core/prompts`.

### SKILL.md (Agent Skills spec)
Frontmatter: `name` (= directory, `^[a-z0-9]+(-[a-z0-9]+)*$`, ≤ 64), `description`
(1–1024, what + when), optional `license`, `compatibility` (≤ 500) and `metadata`
(string → string; use it for provenance). Body should stay under 500 lines; put long
material in `references/` and `scripts/`.

### Eval task (`evals/<profile>/<id>.yaml`)
```yaml
id: <unique>
profile: <profile>
agent: <agent in that profile>
fixture: <dir under evals/fixtures>
prompt: |
  <task with numbered acceptance criteria>
check: <shell; exit 0 = pass; $AP_EVAL_OUTPUT = agent output file>
timeout_s: 1800
```

### Knowledge docs and inventory
Defined in `lab-knowledge/BLUEPRINT.md`.

## 8. Invariants (enforced by tools/check.py unless noted)

1. No secrets in the repo (token, private key and API-key patterns).
2. JSON and YAML parse (except `evals/fixtures/`, which may be broken on purpose).
3. Skill `name` = directory, valid per spec. Scope prefix is `core` or a profile.
4. Every profile has `profile.yaml`, `opencode.json` and `RULES.md`. It renders in both
   normal and headless mode, and all `{file:}`/`instructions` paths exist.
5. Agent models are only `{env:AP_MODEL_*}`, and each variable is defined in
   `core/platform.env`.
6. The effective `permission.skill` starts with `"*": "deny"`. Every allow pattern matches
   at least one skill and is listed in `skill_scopes`.
7. Headless renders have bash `"*": "deny"` globally and in any agent that sets `"*"`.
   Every remaining bash `ask` rule (global or per agent) must be listed in profile.yaml
   `headless_auto_approved`, because `opencode run --auto` approves `ask`. Anything not
   acknowledged there must be denied in `profiles/<p>/opencode.headless.json`.
8. Reporter-role profiles deny edits globally, and no enabled agent allows edits.
9. Every eval task references an existing profile, agent and fixture.
10. *(GitLab, not checkable here)* `main` is protected with no direct pushes. MRs need an
    approval that isn't from the author. Each profile uses its own token with the role in
    profile.yaml. See docs/security.md.
11. *(Process)* Agents never merge. Platform changes that touch prompts, skills or
    permissions include an eval summary.

## 9. Dispatcher flow (CI)

```text
issue labelled agent::ready + profile::<p>
  -> scheduled pipeline in that project runs `agent-dispatch --profile <p>`
  -> strict check: repo has opencode.json/.opencode? -> agent::needs-human (stop)
  -> label agent::running; branch agent/<p>/<iid>-<slug>
  -> agent --headless <p> run --agent <default_agent> --auto "<issue + rules>"
  -> exit != 0            -> agent::failed + note with output tail
  -> no changes           -> agent::needs-human + note with report
  -> commit (agent-<p>), push with <p>'s token, open "Draft:" MR (agent::review)
  -> issue label agent::review + note linking the MR
```
The agent never commits or pushes. The dispatcher does. Output goes to `.agent-run/`
(CI artifact, excluded from git).

## 10. Rebuilding from scratch (ordered)

1. `core/platform.env`, `core/opencode.core.json` (provider, models, base permission, default
   agents, knowledge MCP), `core/opencode.headless.json`, `core/rules/base.md`,
   `core/prompts/*`.
2. `tools/render.py` (section 4 merge rules), then `core/bin/agent`.
3. `skills/core-*`, then each profile directory (section 6/7) and its skills.
4. `mcp/knowledge/knowledge_core.py` + `server.py`. Then `mcp/bench/bench_core.py` +
   `server.py`.
5. `tools/check.py` (section 8), `tools/skills_index.py`, `tools/dispatch.py` (section 9).
6. `evals/run.py`, tasks, fixtures.
7. `ci/*.yml`, `.gitlab-ci.yml`, `core/Containerfile`.
8. `ansible/` roles: base (CA, packages, lock dir) → tools (opencode, glab) → agent_user
   (servers) → platform (checkout, venv, site.env, secrets.env, links) → knowledge
   (clone, pull timer).
9. `tests/`, then verify with `python3 tools/check.py && python3 -m pytest -q tests` and
   `ansible-playbook ansible/site.yml --syntax-check`.

## 11. Re-check on OpenCode upgrades

These depend on OpenCode behavior that may change between versions:
- `{env:}` / `{file:}` substitution inside `model`, `prompt`, `instructions` and MCP
  `command`/`environment`.
- How `instructions` paths resolve (we always pass absolute paths).
- Per-agent `permission` merging with global, and `task`/`skill` permission keys.
- `opencode run --auto` approving `ask`.
- `agent.<name>.disable` for built-in agents.
- Provider packages (`@ai-sdk/openai-compatible`) may be fetched from npm on first use.
  Air-gapped hosts need an npm mirror or a pre-warmed OpenCode cache.

**Pinned version: OpenCode 2.0.27** (decision D14, still provisional). The npm package
`opencode-ai` has no 2.x release (latest 1.19.6 on 2026-11-17), so 2.x is not installed
with npm. `opencode_install` defaults to `archive`: the tools role unpacks a tar.gz from
`opencode_archive_url` (a site setting on your internal mirror, no public URL is baked in).
The archive must contain the single self-contained `opencode` executable at its top level.
`none` (install it yourself) also works. The role fails early on `npm` with a 2.x version
and on `archive` with an empty URL. `npm` remains valid only for 1.x pins (last 1.x
verified: 1.19.5).

Verified on 2026-11-17 against **OpenCode 2.0.27**, in a RHEL 9 container with the binary
copied in (`opencode_install: none`) and real models:
- 2.0.27 loads the rendered 1.x-shaped configs and converts `permission` to its ordered
  `permissions` list itself.
- `agent doctor` passed.
- Denies held under `opencode run --standalone --auto`.
- Three evals passed, once each (not repeated, so no pass rate).
- Before that, 1.19.5 was verified for: `{env:}` / `{file:}` resolution, per-agent
  permission merging, and both MCP servers connecting.

Not verified on 2.0.27:
- A per-agent model for primary agents did not apply under `run --agent`.
- The edit tool on paths outside the repo.
- A corporate CA (TLS to an internal model server or mirror).
- GitLab paths (MRs, bot tokens, protected branches).
- The Ansible `archive` install path itself (the role was not run against 2.0.27).

After every upgrade, re-check with:
```text
agent <profile> debug config            # resolved config (keys show as ***)
agent <profile> debug agent <name>      # effective permission rules for one agent
agent <profile> mcp list                # MCP servers connect
agent --headless <profile> debug agent <name>
```
Then run one eval per profile.

## 12. Decisions

Settled choices and the reasons for them. Change one only together with the reason that
changed.

| # | Decision | Why | Revisit when |
|---|----------|-----|--------------|
| D1 | Knowledge **content** lives in its own repos: team `lab-knowledge` and a private personal `knowledge` repo | Different review rules and audiences. Content changes don't need platform releases. | - |
| D2 | Knowledge **server code** stays in agent-platform (`mcp/knowledge/`) | Shares code with `kn`, models.py, skills index and validator. Tested and versioned with the profiles that use it. | Another team wants it alone, it needs its own release cadence, or it becomes a central service |
| D3 | No central knowledge service. Each agent machine runs the server per session (stdio) on local clones. | No ports, auth, TLS or uptime to manage, and nothing to go down | An embedding model arrives (shared index), or a machine can't hold a clone |
| D4 | Markdown files in git are the source of truth. Search is an in-memory SQLite FTS5 index rebuilt from them (BM25 fallback). | Git gives history, review and GitLab backup. The index can't go stale or corrupt anything. | Corpus large enough that rebuild time matters (then cache the index on disk) |
| D5 | Personal notes are a second, workstation-only source, ranked below team docs and always treated as hints | Fast capture without review. CI and the bench never see them. | - |
| D6 | One GitLab account. Agents act through project access tokens (bot users), one per profile and project, created with `tools/gitlab_bots.py`. | Separate identity and audit trail without extra accounts. Bots don't use license seats. | Group Owner access becomes available (one group token per profile) |
| D7 | No GitLab MCP server. Agents use `glab` and the REST API. | Instance is 18.11.7-ee and MCP isn't available. Its MCP would also reject project tokens and act as the user. | GitLab 19.5+ (GA), for read-only interactive use |
| D8 | Model servers are "locations" in `lab-knowledge/models/registry.yaml`, ranked by task. Copilot HydraFusion is interactive-only. | One place for what exists and what's good for what. Agents can't call Copilot. | - |
| D9 | MCP venv uses Python 3.12 from AppStream. Tools also run on system Python 3.9. | The MCP SDK needs Python 3.10+, and RHEL 9 ships 3.9. | uv allowed and reachable (managed Python) |
| D10 | Install = `./bootstrap.sh` with site settings in `~/.config/agent-platform/site.yml`, outside the repo. `agent doctor` lists anything missing. | Clone-and-run, repo stays site-neutral, requirements are discoverable | - |
