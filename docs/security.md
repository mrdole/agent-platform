# Security model

Three layers. Only the last one actually enforces anything. Assume an agent will
eventually try whatever its token allows.

| Layer | Mechanism | Stops |
|-------|-----------|-------|
| 1. Prompt | RULES.md, base rules | honest mistakes |
| 2. Harness | OpenCode `permission` (deny / ask), headless overlay, bench MCP guard rails | most mistakes, some misuse |
| 3. Server | GitLab roles, token scopes, protected branches, approval rules, runner placement | everything the token doesn't allow |

## GitLab setup checklist (per project the agents work in)

- [ ] **Protected branch** `main`: *Allowed to push and merge* = No one / Maintainers.
      *Allowed to force push* = off.
- [ ] **Merge request approvals:** at least 1 approval required, with *Prevent approval by
      author* and *Prevent approvals by users who add commits* turned on (if your GitLab
      tier has approval rules).
- [ ] **Labels:** `agent::ready`, `agent::running`, `agent::review`, `agent::needs-human`,
      `agent::failed`, `profile::<name>`.
- [ ] **CI/CD variables:** masked and protected: `CORP_LLM_KEY` and the profile token(s)
      below. Protected variables are only exposed on protected refs, which is why the
      dispatch schedule runs on the default branch.

## Bot identities with one GitLab account

You don't need extra accounts. A **project access token** makes GitLab create a bot user
(`project_<id>_bot_<random>`, display name = the token name). Everything done with that
token (pushes, MRs, comments) is attributed to the bot. Bots don't use license seats. You
need Maintainer on the project to create one. A personal access token, by contrast, acts
as **you**.

Two kinds of "who" in GitLab:
- **Actor** (who pushed, opened the MR, commented) is set by the token and enforced by
  the server. Use it to tell agents from people.
- **Commit author** is set by `git config` or environment variables. It's a label anyone
  can set, so don't rely on it.

### Setup with your PAT (tools/gitlab_bots.py)
```
cp ansible/bots.example.yaml ~/.config/agent-platform/bots.yaml    # list projects per profile
export GITLAB_PAT=<your PAT, scope api>
python3 tools/gitlab_bots.py plan
python3 tools/gitlab_bots.py apply --ci-vars --protect --labels
unset GITLAB_PAT
```
For each (profile, project) this creates a token `agent-<profile>` with the role and scopes
from `profile.yaml`. It saves the value to `~/.config/agent-platform/bot-tokens.json`
(0600) and never prints it. Optionally it also:
- stores the token as a masked, protected CI variable (`--ci-vars`),
- protects `main` (`--protect`),
- creates the `agent::*` labels (`--labels`).

Re-running is safe: tokens you already have are kept, and missing ones are rotated. Run
`status` to see expiry dates and any `main` that still allows direct pushes. Run
`apply --rotate` before tokens expire.

Use a short-lived PAT just for this, and don't keep it in `secrets.env`. Run the script
yourself, never through an agent.

Also available, all configured in `bots.yaml`:
- `group_profiles` makes one group access token per profile. It reaches the whole group.
- `protected_paths` refuses any token that would reach those paths, and flags any bot found
  among their members.
- `branch_lockdown` with `apply --branches` limits Developer bots to agent branches. GitLab
  applies the most permissive matching rule, so read the trade-off in `tools/gitlab_bots.py`.
- `gitlab_bots.py export --profile <p> --out <file>` writes only the named profiles to a new
  0600 file for an agent user or container. Set `AP_BOT_TOKENS` to read it from another path.

### Every agent session acts as the bot (tools/botenv.py, called by `agent`)
The launcher looks up the token for the current repo and profile: the CI variable first,
then `bot-tokens.json`. For that session only:
- `glab` uses the token.
- Pushes to the GitLab host go over HTTPS with the token, even if your remote is SSH
  (`pushInsteadOf` plus a session credential helper, all through `GIT_CONFIG_*` env vars,
  so no config file changes).
- Commits are authored `agent-<profile> (via <your name>)`.

Fetches still use your normal access. With no token found, it warns that actions will be
yours. Set `AP_REQUIRE_BOT=1` to refuse instead.

## Tokens (one bot identity per profile and project)

Project access tokens cover a single project, so a profile that works in four repos has four
tokens with the same name. A group access token covers a whole group with one bot, but
creating one needs Owner on the group.

| CI variable | Type | Role | Scopes | Where |
|-------------|------|------|--------|-------|
| `GITLAB_TOKEN_FIRMWARE` | project | Developer | api, write_repository | firmware repo |
| `GITLAB_TOKEN_PIPELINE` | project/group | Developer | api, write_repository | pipeline + template repos |
| `GITLAB_TOKEN_BENCH` | project | Developer | api, write_repository | bench test repo |
| `GITLAB_TOKEN_ANALYST` | group | Reporter | read_api, read_repository | everything it may read |
| `GITLAB_TOKEN_META` | project | Developer | api, write_repository | agent-platform, lab-knowledge |
| `GITLAB_TOKEN_CATALOG_READ` | group | Reporter | read_api | group scanned for skills |
| `GITLAB_TOKEN_KNOWLEDGE` | project | Developer | api, write_repository | lab-knowledge (bot branch + MR) |

Set expiry dates and rotate them. A Developer can push branches and open MRs but can't
push to protected `main` or merge without approval.

## Known holes and how they're closed

| Hole | Mitigation |
|------|------------|
| `opencode run --auto` approves every `ask` | Headless overlay sets bash `"*": "deny"`. Unattended safety rules must be `deny`. `check.py` enforces this. |
| A project's own `opencode.json`/`.opencode/` overrides profile permissions | The dispatcher (strict mode) refuses such repos and labels the issue `agent::needs-human`. |
| Agent pushes to main | Protected branch + Developer token. Harness denies `git push * main*`. In dispatcher runs, the agent can't push at all. |
| Agent approves/merges its own MR | It has no approve/merge tools, and GitLab's author approval rules block it. |
| Secrets in prompts/logs | Tokens only in masked CI variables or `secrets.env` (0600/0640). The dispatcher redacts its token from errors. it-debug rules forbid printing env. |
| Instrument damage | Bench writes need `AP_BENCH_ALLOW_WRITE=1` **and** a per-device allowlist in inventory. No `;`-chained commands. Every call is audit-logged (`~/.local/state/agent-platform/bench-audit.jsonl`). |
| Two jobs on one bench | GitLab `resource_group: bench-<name>` across runners, plus `bench-lock` (flock) on the host. |
| Poisoned knowledge | Agents can't write to lab-knowledge directly. New docs arrive as `status: draft` via MR, and a human sets `verified`. |
| Malicious third-party skill | Skills are vendored through the `meta-vendor-skill` process: every file read, provenance in `metadata`, MR review. |
| Agent "improves" itself into a worse state | Platform changes need check + tests + evals with no regressions against the baseline, and human merge. |
