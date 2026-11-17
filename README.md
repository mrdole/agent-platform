# agent-platform

A versioned agent harness for firmware, CI pipeline, lab bench, IT debugging and
self-maintenance work. It runs OpenCode against the internal LLM server.

- **Core:** shared model config, global rules, the `agent` launcher, CI templates,
  the container image and the Ansible installer.
- **Profiles:** one per kind of job (`firmware`, `pipeline`, `bench`, `it-debug`,
  `meta`). Each profile has its own agents, permissions, tools, GitLab token and runner.
- **Skills:** reusable instructions in the Agent Skills format (`SKILL.md`). The name
  prefix scopes each skill to a profile.
- **Knowledge:** environment facts, gotchas and lab inventory. These live in the
  separate `lab-knowledge` repo and reach agents through `mcp/knowledge`.

`BLUEPRINT.md` is the full design and is detailed enough to rebuild the repo from
scratch. Agents working in this repo follow `AGENTS.md`. `docs/security.md` covers the
GitLab-side settings that actually enforce the limits.

## Requirements

**You need:**
- RHEL 9.x (WSL is fine), registered so `dnf` works, and sudo.
- Network access to:
  - dnf repos (BaseOS/AppStream, or your Satellite),
  - PyPI (or an internal mirror, set in `site.yml`),
  - an internal web or file server that hosts the OpenCode tar.gz (`opencode_archive_url`),
  - the model server,
  - GitLab.
- Your LLM API key, and a GitLab PAT (`api` scope) only if you set up bot identities.

**`./bootstrap.sh` installs everything else:**

| Tool | Why | Source |
|------|-----|--------|
| ansible-core, git | runs the install | dnf (bootstrap.sh) |
| python3.12 + venv | MCP servers need Python ≥ 3.10 (RHEL's default python3 is 3.9) | dnf AppStream + PyPI |
| python3-pyyaml | lets the repo tools run with system python3 | dnf |
| OpenCode 2.0.27 | the agent runtime (pinned) | tar.gz from `opencode_archive_url` (your mirror) |
| podman, util-linux (flock), acl | builds in containers, bench locking | dnf |
| corporate CA certs | TLS to internal hosts | your `.crt` files |
| glab *(optional)* | pipeline/it-debug skills, MRs from interactive sessions | your `.rpm` URL |

`agent doctor` checks all of this and prints the fix for anything missing.
`agent doctor --network` also tests the model server and GitLab.

## Install on a RHEL 9 workstation (WSL or bare metal)

**Guided:** after cloning, run the wizard. It asks for each site setting, shows what it will
write, and then offers each step below in turn. Nothing is applied without a yes, and it can
be re-run at any time (`--dry-run` changes nothing, `--step N` starts later).
```
python3 tools/setup_wizard.py
```
If VS Code is already set up for your model server, give the wizard the path to its
`chatLanguageModels.json` when it asks. It reads the server URL from that file and tells you which
platform role models the file serves.

**With an AI assistant:** point it at `docs/ai-install-guide.md`. It tells the assistant how to
guide you through the wizard, what it must never do (handle keys, loosen rules, push), and how to
fix the problems a fresh machine usually hits.

**By hand:**

1. Clone and bootstrap:
   ```
   git clone <agent-platform url> ~/repos/agent-platform
   cd ~/repos/agent-platform
   ./bootstrap.sh
   ```
   The first run creates `~/.config/agent-platform/site.yml` (outside the repo) and
   stops.
2. Fill in its CHANGEME values (LLM URL, GitLab host, CA files, mirrors), then run it
   again:
   ```
   ./bootstrap.sh
   ```
   It asks for your sudo password once, installs everything, and ends with `agent doctor`.
3. Put your LLM key in `~/.config/agent-platform/secrets.env`. The playbook creates this
   file with mode 0600 and never overwrites it.
   ```
   CORP_LLM_KEY=...
   ```
4. Check the model IDs. Fix any it reports in `lab-knowledge/models/registry.yaml`,
   `core/opencode.core.json` and `core/platform.env`:
   ```
   python3 tools/models.py --knowledge ~/repos/lab-knowledge probe --location corp-llm
   ```
5. Check and start:
   ```
   agent doctor --network
   agent firmware
   ```

Re-running `./bootstrap.sh` is safe; it only changes what's missing or out of date.

Server installs use `--limit agent_servers`. The playbook creates a dedicated `agent`
user, installs into `/opt/agent-platform` and keeps `lab-knowledge` pulled on a timer.

## Personal notes (kn)

Your own notes repo, searched by your agents alongside the team `lab-knowledge` repo and
backed up to a private GitLab project:
```
kn init ~/repos/knowledge          # once; then add a GitLab remote and set ap_personal_knowledge_root
kn capture "curl to llm times out from lab subnet, fixed by adding host to NO_PROXY"
kn search "proxy lab subnet"
kn ask "how do I reach the LLM server from the bench?"
kn todo add "probe model ids" --due 2026-11-05
kn log "installed platform on WSL"
kn review --days 7
kn sync
```
`capture`, `ask` and `review` use your models. For bigger cleanups, run `agent notes` inside
the repo. When a note is proven, `kn promote` turns it into a draft MR in the team repo.

## Everyday commands

```
agent firmware                                  # interactive TUI, firmware profile
agent firmware run --agent lead "task text"     # one-shot
agent --headless pipeline run --agent lead --auto "task text"
python3 tools/check.py                          # validate the repo
python3 -m pytest -q tests                      # unit tests
python3 evals/run.py --profile firmware         # golden tasks against the real models
python3 tools/skills_index.py local ~/repos/* -o /tmp/skills   # what skills exist already
```

## Running work from GitLab issues

A project repo includes `ci/agent-dispatch.gitlab-ci.yml` and adds a pipeline schedule.
Issues labelled `agent::ready` plus `profile::<name>` are then picked up and run
headless, and the result comes back as a draft MR. The full flow is in `BLUEPRINT.md`
section 9.
