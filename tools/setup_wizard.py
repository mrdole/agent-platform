#!/usr/bin/env python3
"""Guided install: asks for each site setting, then offers each setup step in turn.

    python3 tools/setup_wizard.py              run the wizard
    python3 tools/setup_wizard.py --dry-run    ask everything, change nothing
    python3 tools/setup_wizard.py --step 3     start at a later step

It does by prompt what README "Install on a RHEL 9 workstation" does by hand:

  1. site settings   -> ~/.config/agent-platform/site.yml (shown as a diff before writing).
                        Give it the path to a VS Code chatLanguageModels.json and it reads the
                        model server URL from it and checks the platform's role models against it.
  2. install         -> ./bootstrap.sh
  3. model key       -> ~/.config/agent-platform/secrets.env (typed hidden, mode 0600)
  4. check           -> agent doctor --network
  5. model IDs       -> tools/models.py probe
  6. GitLab bots     -> tools/gitlab_bots.py plan, then apply
  7. smoke test      -> one eval task

Nothing is applied without a yes. Every step can be skipped and the wizard can be re-run:
answers default to what is already in site.yml. Secrets are typed hidden, never echoed,
and never written anywhere except secrets.env (the GitLab token is not written at all).

Runs on the system Python 3.9 with the standard library only, because it runs before the
platform venv exists.
"""
from __future__ import annotations

import argparse
import difflib
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path

AP_HOME = Path(os.environ.get("AP_HOME") or Path(__file__).resolve().parent.parent)
PLACEHOLDER = "CHANGEME"
INSTALL_MODES = ("npm", "archive", "none")


# --------------------------------------------------------------------------- site.yml text
def _line_re(key: str) -> "re.Pattern[str]":
    return re.compile(rf"^(?P<head>{re.escape(key)}:[ \t]*)(?P<value>.*?)(?P<tail>[ \t]+#.*)?$", re.M)


def get_value(text: str, key: str):
    """Value of a top-level `key: value` line as str or list; None if the key is absent."""
    m = _line_re(key).search(text)
    if not m:
        return None
    raw = m.group("value").strip()
    if raw.startswith("["):
        try:
            return [str(v) for v in json.loads(raw)]
        except ValueError:
            return [v.strip().strip("\"'") for v in raw.strip("[]").split(",") if v.strip()]
    return raw.strip("\"'")


def format_value(value) -> str:
    """YAML flow scalars and lists. JSON strings are valid YAML, so quote with json."""
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v) for v in value) + "]"
    if value == "" or re.search(r"[:#\[\]{}\s,&*!|>'\"%@`]", value) or value[0].isdigit():
        return json.dumps(value)
    return value


def set_value(text: str, key: str, value) -> str:
    """Replace the value on the key's line, keeping its trailing comment. Append if absent."""
    new = format_value(value)
    rx = _line_re(key)
    if not rx.search(text):
        return text.rstrip("\n") + f"\n{key}: {new}\n"
    return rx.sub(lambda m: f"{m.group('head')}{new}{m.group('tail') or ''}", text, count=1)


# --------------------------------------------------------------------------- env files
def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("\"'")
    return out


def set_env_line(text: str, key: str, value: str) -> str:
    """Set KEY=value, replacing an existing or commented-out line for the same key."""
    rx = re.compile(rf"^[ \t]*#?[ \t]*{re.escape(key)}=.*$", re.M)
    line = f"{key}={value}"
    if rx.search(text):
        return rx.sub(lambda _m: line, text, count=1)
    return text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"


def platform_env(cfg_dir: Path) -> dict[str, str]:
    """The environment `agent` would load, for the tools this wizard runs directly."""
    env: dict[str, str] = {"AP_HOME": str(AP_HOME)}
    for path in (AP_HOME / "core" / "platform.env", cfg_dir / "site.env", cfg_dir / "secrets.env"):
        env.update(read_env(path))
    env.update(os.environ)  # same precedence as core/bin/agent: the caller's environment wins
    return env


# --------------------------------------------------------------------------- VS Code model list
CHAT_MODELS_NAME = "chatLanguageModels.json"
_API_SUFFIX = re.compile(r"/(chat/completions|responses|messages)/?$")


def load_jsonc(text: str):
    """JSON with // and /* */ comments and trailing commas, as VS Code writes it."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def base_url(url: str) -> str:
    return _API_SUFFIX.sub("", url.strip()).rstrip("/")


def chat_model_groups(data) -> list[dict]:
    """Provider groups that name a server URL: [{name, url, models: [ids], key: none|reference|raw, raw_key}]."""
    groups = []
    for group in data if isinstance(data, list) else []:
        if not isinstance(group, dict):
            continue
        models = [m for m in group.get("models") or [] if isinstance(m, dict) and m.get("id")]
        urls = [base_url(u) for u in [group.get("url")] + [m.get("url") for m in models] if isinstance(u, str) and u]
        if not urls:
            continue
        key = group.get("apiKey")
        kind = "none" if not key else "reference" if str(key).startswith("${") else "raw"
        groups.append({"name": str(group.get("name") or group.get("vendor") or "?"),
                       "url": max(set(urls), key=urls.count), "models": [str(m["id"]) for m in models],
                       "key": kind, "raw_key": str(key) if kind == "raw" else ""})
    return groups


def role_models() -> dict[str, str]:
    """AP_MODEL_* role -> model id (without the provider prefix) from core/platform.env."""
    env = read_env(AP_HOME / "core" / "platform.env")
    return {k[len("AP_MODEL_"):].lower(): v.split("/", 1)[-1] for k, v in env.items() if k.startswith("AP_MODEL_")}


def pick_chat_models(ui: "UI", ctx: dict) -> str:
    """Offer to read the model server from a chatLanguageModels.json. Returns a URL or ''."""
    answer = ui.ask(f"Path to a VS Code {CHAT_MODELS_NAME} to read the model server from (Enter to skip)")
    if not answer or answer == "-":
        return ""
    path = Path(answer).expanduser()
    try:
        groups = chat_model_groups(load_jsonc(path.read_text()))
    except (OSError, ValueError) as exc:
        ui.say(f"  could not read {path}: {exc}")
        return ""
    if not groups:
        ui.say(f"  {path} has no provider with a server URL, so it is ignored.")
        return ""
    group = groups[0]
    if len(groups) > 1:
        for number, g in enumerate(groups, 1):
            ui.say(f"  {number}. {g['name']}: {g['url']} ({len(g['models'])} models)")
        choice = ui.ask("Which one serves the agents?", "1")
        group = groups[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(groups) else groups[0]
    ui.say(f"  using {group['name']}: {group['url']}")
    served, roles = set(group["models"]), role_models()
    for role, model in sorted(roles.items()):
        ui.say(f"  {role:9} {model:24} {'listed' if model in served else 'NOT in this file'}")
    unused = sorted(served - set(roles.values()))
    if unused:
        ui.say(f"  also listed there: {', '.join(unused[:12])}{' ...' if len(unused) > 12 else ''}")
    if any(m not in served for m in roles.values()):
        ui.say("  Fix the missing ones in core/platform.env, core/opencode.core.json and the model registry.")
    if group["key"] == "raw":
        ctx["raw_key"], ctx["key_source"] = group["raw_key"], str(path)
    elif group["key"] == "reference":
        ui.say("  Its key is a VS Code secret reference, so you still type the key in step 3.")
    return group["url"]


# --------------------------------------------------------------------------- prompts
class UI:
    """Terminal prompts. Tests replace this with scripted answers."""

    def say(self, text: str = "") -> None:
        print(text)

    def ask(self, prompt: str, default: str = "") -> str:
        shown = f" [{default}]" if default else ""
        answer = input(f"{prompt}{shown}: ").strip()
        return answer or default

    def confirm(self, prompt: str, default: bool = False) -> bool:
        answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer in ("y", "yes")

    def secret(self, prompt: str) -> str:
        return getpass.getpass(f"{prompt}: ").strip()


class Quit(Exception):
    pass


def offer(ui: UI, title: str, detail: str) -> bool:
    """Ask before a step. True = do it, False = skip. `q` leaves the wizard."""
    ui.say(f"\n== {title}\n{detail}")
    answer = ui.ask("Apply this step? (y = yes, s = skip, q = quit)", "y").lower()
    if answer.startswith("q"):
        raise Quit
    return answer.startswith("y")


# --------------------------------------------------------------------------- validation
def clean_host(value: str) -> str:
    return re.sub(r"^[a-z]+://", "", value.strip()).split("/")[0]


def check_site(values: dict) -> list[str]:
    """Problems that would make bootstrap fail or misbehave. Empty list = good to write."""
    problems = []
    url = values.get("ap_llm_base_url", "")
    if not re.match(r"^https?://[^/\s]+", url) or PLACEHOLDER in url:
        problems.append("ap_llm_base_url must be a full URL such as https://llm.example.internal/v1")
    host = values.get("ap_gitlab_host", "")
    if not host or PLACEHOLDER in host or "/" in host:
        problems.append("ap_gitlab_host must be a host name such as gitlab.example.internal")
    for ca in values.get("ap_corp_ca_files", []):
        if not Path(ca).expanduser().is_file():
            problems.append(f"CA file not found: {ca}")
    mode = values.get("opencode_install", "")
    if mode not in INSTALL_MODES:
        problems.append(f"opencode_install must be one of {', '.join(INSTALL_MODES)}")
    if mode == "archive" and not values.get("opencode_archive_url"):
        problems.append("opencode_install: archive needs opencode_archive_url")
    return problems


def site_warnings(values: dict) -> list[str]:
    warnings = []
    url = values.get("ap_llm_base_url", "")
    if url and not url.rstrip("/").endswith("/v1"):
        warnings.append("the model URL usually ends in /v1 for an OpenAI-compatible server")
    version = values.get("opencode_version", "")
    if values.get("opencode_install") == "npm" and version.split(".")[0].isdigit() and int(version.split(".")[0]) >= 2:
        warnings.append("npm cannot install OpenCode 2.x (`opencode-ai` has no 2.x release, checked "
                        "2026-11-17); the install would fail with ETARGET. Use `archive` with "
                        "opencode_archive_url pointing at a tar.gz on your internal mirror that holds the "
                        "`opencode` executable, or `none` and install the binary yourself.")
    return warnings


# --------------------------------------------------------------------------- steps
def step_site(ui: UI, site: Path, dry_run: bool, ctx: dict | None = None) -> None:
    example = (AP_HOME / "ansible" / "site.example.yml").read_text()
    old = site.read_text() if site.is_file() else ""
    text = old or example

    def current(key: str, fallback: str = "") -> str:
        value = get_value(text, key)
        if isinstance(value, list):
            return ", ".join(value)
        return fallback if value is None or PLACEHOLDER in value else value

    ui.say("Press Enter to keep the value in brackets. Type a single - to clear an optional value.")

    def ask(key: str, prompt: str, fallback: str = "") -> str:
        answer = ui.ask(prompt, current(key, fallback))
        return "" if answer == "-" else answer

    values: dict = {}
    from_file = pick_chat_models(ui, ctx if ctx is not None else {})
    values["ap_llm_base_url"] = (ui.ask("Model server URL (OpenAI-compatible, ends in /v1)", from_file)
                                 if from_file else
                                 ask("ap_llm_base_url", "Model server URL (OpenAI-compatible, ends in /v1)"))
    values["ap_gitlab_host"] = clean_host(ask("ap_gitlab_host", "GitLab host name (no https://)"))
    cas = ask("ap_corp_ca_files", "Corporate CA .crt files, comma-separated (optional)")
    values["ap_corp_ca_files"] = [c.strip() for c in cas.split(",") if c.strip()]
    values["ap_https_proxy"] = ask("ap_https_proxy", "HTTPS proxy (optional)")
    values["ap_no_proxy"] = ask("ap_no_proxy", "No-proxy list (optional)") if values["ap_https_proxy"] else ""
    values["ap_pip_index_url"] = ask("ap_pip_index_url", "Internal PyPI mirror URL (optional)")
    values["npm_registry"] = ask("npm_registry", "Internal npm mirror URL (optional; only for npm installs of OpenCode 1.x)")
    values["opencode_install"] = ask("opencode_install", f"How to install OpenCode ({' | '.join(INSTALL_MODES)})", "archive")
    values["opencode_version"] = ask("opencode_version", "OpenCode version to pin", "2.0.27")
    values["opencode_archive_url"] = (ask("opencode_archive_url", "URL of the OpenCode tar.gz (internal mirror; `opencode` executable at its top level)")
                                      if values["opencode_install"] == "archive" else current("opencode_archive_url"))
    values["glab_rpm_url"] = ask("glab_rpm_url", "glab .rpm URL (optional; empty = skip glab)")
    values["ap_knowledge_repo_url"] = ask("ap_knowledge_repo_url",
                                          "lab-knowledge repo URL (empty = you already cloned ~/repos/lab-knowledge)")
    values["ap_personal_knowledge_repo_url"] = ask("ap_personal_knowledge_repo_url",
                                                   "Your private notes repo URL (optional)")

    problems = check_site(values)
    for w in site_warnings(values):
        ui.say(f"  note: {w}")
    if problems:
        for p in problems:
            ui.say(f"  problem: {p}")
        ui.say("Not writing site.yml. Run the wizard again to fix these.")
        raise Quit

    new = text
    for key, value in values.items():
        new = set_value(new, key, value)
    if new == old:
        ui.say(f"{site} is already up to date.")
        return
    diff = difflib.unified_diff((old or example).splitlines(), new.splitlines(),
                                "current" if old else "site.example.yml", str(site), lineterm="")
    ui.say("\n".join(diff))
    if dry_run:
        ui.say(f"(dry run) would write {site}")
    elif ui.confirm(f"Write {site}?", default=True):
        site.parent.mkdir(parents=True, exist_ok=True)
        site.write_text(new)
        ui.say(f"wrote {site}")


def run(ui: UI, cmd: list[str], dry_run: bool, env: dict | None = None) -> int:
    ui.say("$ " + " ".join(cmd))
    if dry_run:
        ui.say("(dry run) not run")
        return 0
    rc = subprocess.run(cmd, cwd=AP_HOME, env=env).returncode
    if rc != 0:
        ui.say(f"-> exited {rc}")
    return rc


def step_key(ui: UI, cfg_dir: Path, dry_run: bool, ctx: dict | None = None) -> None:
    path = cfg_dir / "secrets.env"
    if read_env(path).get("CORP_LLM_KEY") and not ui.confirm("CORP_LLM_KEY is already set. Replace it?"):
        return
    ctx = ctx or {}
    if ctx.get("raw_key") and ui.confirm(f"Use the key stored in {ctx['key_source']}? (it is not shown)", default=True):
        key = ctx["raw_key"]
    else:
        key = ui.secret("Model server key (typing is hidden)")
    if not key:
        ui.say("Nothing typed, so secrets.env is unchanged.")
        return
    if dry_run:
        ui.say(f"(dry run) would set CORP_LLM_KEY in {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.is_file() else ""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(set_env_line(text, "CORP_LLM_KEY", key))
    os.chmod(path, 0o600)
    ui.say(f"set CORP_LLM_KEY in {path} (mode 600)")


def tool_python() -> str:
    venv = AP_HOME / "venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def step_bots(ui: UI, cfg_dir: Path, dry_run: bool) -> None:
    config = cfg_dir / "bots.yaml"
    if not config.is_file():
        ui.say(f"No {config} yet. Copy ansible/bots.example.yaml there, list your projects and "
               "profiles, then run:  python3 tools/setup_wizard.py --step 6")
        return
    env = platform_env(cfg_dir)
    if not env.get("GITLAB_PAT"):
        pat = ui.secret("Your own GitLab token with `api` scope (hidden; used for this step only, not saved)")
        if not pat:
            ui.say("No token typed, so this step is skipped.")
            return
        env["GITLAB_PAT"] = pat
    base = [tool_python(), "tools/gitlab_bots.py"]
    ui.say("First the plan. It changes nothing.")
    run(ui, [*base, "plan"], dry_run, env)
    extras = [flag for flag, question in (
        ("--protect", "Also protect `main` on those projects?"),
        ("--labels", "Also create the agent labels?"),
        ("--ci-vars", "Also store each bot token as a masked CI variable?"),
        ("--branches", "Also apply branch_lockdown from bots.yaml?")) if ui.confirm(question)]
    if ui.confirm("Apply the plan now? This creates bot tokens in GitLab."):
        run(ui, [*base, "apply", *extras], dry_run, env)


def wizard(ui: UI, cfg_dir: Path, dry_run: bool = False, first: int = 1) -> int:
    site = cfg_dir / "site.yml"
    ctx: dict = {}  # what step 1 learned from a chatLanguageModels.json, for step 3
    steps = [
        ("Site settings", f"Asks for the model server, GitLab host, CA files, mirrors and the OpenCode "
                          f"install, then writes {site}.", lambda: step_site(ui, site, dry_run, ctx)),
        ("Install", "Runs ./bootstrap.sh: installs packages with dnf, builds the venv, links the commands "
                    "and ends with `agent doctor`. It asks for your sudo password.",
         lambda: run(ui, ["./bootstrap.sh"], dry_run)),
        ("Model key", f"Stores your model server key as CORP_LLM_KEY in {cfg_dir / 'secrets.env'}.",
         lambda: step_key(ui, cfg_dir, dry_run, ctx)),
        ("Check the machine", "Runs `agent doctor --network`, which lists anything still missing.",
         lambda: run(ui, ["core/bin/agent", "doctor", "--network"], dry_run)),
        ("Check model IDs", "Asks the model server which models it serves and compares them with "
                            "lab-knowledge/models/registry.yaml.",
         lambda: run(ui, [tool_python(), "tools/models.py", "probe", "--location", "corp-llm"], dry_run,
                     platform_env(cfg_dir))),
        ("GitLab bot tokens", "Shows the plan from tools/gitlab_bots.py, then asks before creating bot "
                              "tokens. Needs your own GitLab token, which is not saved.",
         lambda: step_bots(ui, cfg_dir, dry_run)),
        ("Smoke test", "Runs one small eval (it-debug: find a known gotcha in lab-knowledge). "
                       "It calls the model server.",
         lambda: run(ui, [sys.executable, "evals/run.py", "--task", "itd-find-headless-gotcha"], dry_run,
                     {**platform_env(cfg_dir), "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"})),
    ]
    ui.say(f"agent-platform setup wizard ({AP_HOME}){' - dry run' if dry_run else ''}")
    try:
        for number, (title, detail, action) in enumerate(steps, 1):
            if number < first:
                continue
            if offer(ui, f"Step {number} of {len(steps)}: {title}", detail):
                action()
            else:
                ui.say("skipped")
    except Quit:
        ui.say("Stopped. Run the wizard again to continue; it keeps what you already set.")
        return 1
    ui.say("\nDone. Start an agent with:  agent firmware")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="ask everything, change nothing")
    ap.add_argument("--step", type=int, default=1, help="start at this step (1-7)")
    ap.add_argument("--config-dir", type=Path,
                    default=Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-platform")
    args = ap.parse_args(argv)
    try:
        return wizard(UI(), args.config_dir, args.dry_run, args.step)
    except (EOFError, KeyboardInterrupt):
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
