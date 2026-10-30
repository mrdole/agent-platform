#!/usr/bin/env python3
"""Validate agent-platform. Run before every MR; CI runs it too.

    check.py [--root PATH]

Exit 0 when there are no errors (warnings are printed but don't fail).
Every rule here maps to an invariant in BLUEPRINT.md section 8.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render as render_mod  # noqa: E402

SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PROFILE_NAME = SKILL_NAME
TOKEN_ENV = re.compile(r"^GITLAB_TOKEN_[A-Z0-9_]+$")
MODEL_ENV = re.compile(r"\{env:(AP_MODEL_[A-Z_]+)\}")
FILE_REF = re.compile(r"\{file:([^}]+)\}")
SKILL_FIELDS = {"name", "description", "license", "compatibility", "metadata"}
PROFILE_KEYS = {"name", "description", "default_agent", "gitlab", "runner_tags", "skill_scopes"}
EVAL_KEYS = {"id", "profile", "agent", "fixture", "prompt", "check"}
ROLES = {"reporter", "developer"}
SECRET_PATTERNS = [
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{24,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}"),
]
TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".py", ".sh", ".env", ".j2", ".txt", ".cfg", ""}
SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", ".pytest_cache", ".eval-results", "node_modules"}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def frontmatter(text: str):
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    meta = yaml.safe_load(text[4:end]) or {}
    body = text[end + 4:].lstrip("\n")
    return meta, body


def iter_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def check_parse(root: Path, rep: Report) -> None:
    for path in iter_files(root):
        rel = path.relative_to(root)
        if rel.parts[:2] == ("evals", "fixtures"):
            continue  # fixtures may be broken on purpose
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                rep.error(f"{rel}: invalid JSON: {exc}")
        elif path.suffix in {".yaml", ".yml"}:
            try:
                list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
            except yaml.YAMLError as exc:
                rep.error(f"{rel}: invalid YAML: {exc}")


def check_secrets(root: Path, rep: Report) -> None:
    for path in iter_files(root):
        if path.suffix not in TEXT_SUFFIXES or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                rep.error(f"{path.relative_to(root)}: looks like it contains a secret ({pat.pattern[:30]}...)")
                break


def load_skills(root: Path, rep: Report) -> dict[str, dict]:
    skills: dict[str, dict] = {}
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        rep.error("skills/ directory missing")
        return skills
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        md = d / "SKILL.md"
        rel = md.relative_to(root)
        if not md.is_file():
            rep.error(f"{d.relative_to(root)}: missing SKILL.md")
            continue
        try:
            meta, body = frontmatter(md.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            rep.error(f"{rel}: bad frontmatter: {exc}")
            continue
        if meta is None:
            rep.error(f"{rel}: must start with YAML frontmatter")
            continue
        name = str(meta.get("name", ""))
        desc = str(meta.get("description", "") or "")
        if not SKILL_NAME.match(name) or len(name) > 64:
            rep.error(f"{rel}: name '{name}' must be lowercase letters/digits/single hyphens, max 64")
        if name != d.name:
            rep.error(f"{rel}: name '{name}' must equal directory name '{d.name}'")
        if not 1 <= len(desc) <= 1024:
            rep.error(f"{rel}: description must be 1-1024 characters (is {len(desc)})")
        if "compatibility" in meta and not 1 <= len(str(meta["compatibility"])) <= 500:
            rep.error(f"{rel}: compatibility must be 1-500 characters")
        if "metadata" in meta and not (
            isinstance(meta["metadata"], dict) and all(isinstance(v, str) for v in meta["metadata"].values())
        ):
            rep.error(f"{rel}: metadata must map strings to strings")
        unknown = set(meta) - SKILL_FIELDS
        if unknown:
            rep.warn(f"{rel}: frontmatter fields not recognized by OpenCode: {sorted(unknown)}")
        if len(body.splitlines()) > 500:
            rep.warn(f"{rel}: body over 500 lines; move detail into references/")
        skills[d.name] = meta
    return skills


def _walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def check_rendered(cfg: dict, label: str, model_vars: set[str], rep: Report) -> None:
    for s in _walk_strings(cfg):
        for var in MODEL_ENV.findall(s):
            if var not in model_vars:
                rep.error(f"{label}: uses {{env:{var}}} but core/platform.env does not define it")
        for ref in FILE_REF.findall(s):
            if not ref.startswith(("{env:", "~")) and not Path(ref).is_file():
                rep.error(f"{label}: {{file:{ref}}} does not exist")
    for path in cfg.get("instructions", []):
        if not path.startswith(("{env:", "~")) and not Path(path).is_file():
            rep.error(f"{label}: instructions file {path} does not exist")
    for name, agent in cfg.get("agent", {}).items():
        model = agent.get("model")
        if model is not None and not MODEL_ENV.search(model):
            rep.error(f"{label}: agent '{name}' model must be {{env:AP_MODEL_*}}, got '{model}'")


def skill_patterns_ok(cfg: dict, skills: dict[str, dict], label: str, rep: Report) -> list[str]:
    rules = cfg.get("permission", {}).get("skill")
    if not isinstance(rules, dict) or not rules:
        rep.error(f"{label}: permission.skill must be an object starting with \"*\": \"deny\"")
        return []
    keys = list(rules)
    if keys[0] != "*" or rules["*"] != "deny":
        rep.error(f"{label}: permission.skill must start with \"*\": \"deny\"")
    allowed = [k for k, v in rules.items() if k != "*" and v == "allow"]
    for pattern in allowed:
        regex = re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")
        if not any(regex.match(s) for s in skills):
            rep.error(f"{label}: skill permission '{pattern}' matches no skill in skills/")
    return allowed


def check_profiles(root: Path, skills: dict[str, dict], rep: Report) -> list[str]:
    model_vars = set(re.findall(r"^(AP_MODEL_[A-Z_]+)=", (root / "core/platform.env").read_text(), re.M))
    names = []
    for d in sorted(p for p in (root / "profiles").iterdir() if p.is_dir()):
        names.append(d.name)
        label = f"profiles/{d.name}"
        if not PROFILE_NAME.match(d.name):
            rep.error(f"{label}: bad profile name")
        for required in ("profile.yaml", "opencode.json", "RULES.md"):
            if not (d / required).is_file():
                rep.error(f"{label}: missing {required}")
        if not (d / "profile.yaml").is_file() or not (d / "opencode.json").is_file():
            continue
        meta = yaml.safe_load((d / "profile.yaml").read_text()) or {}
        missing = PROFILE_KEYS - set(meta)
        if missing:
            rep.error(f"{label}/profile.yaml: missing keys {sorted(missing)}")
        if meta.get("name") != d.name:
            rep.error(f"{label}/profile.yaml: name must be '{d.name}'")
        gl = meta.get("gitlab") or {}
        if not TOKEN_ENV.match(str(gl.get("token_env", ""))):
            rep.error(f"{label}/profile.yaml: gitlab.token_env must look like GITLAB_TOKEN_<NAME>")
        if gl.get("role") not in ROLES:
            rep.error(f"{label}/profile.yaml: gitlab.role must be one of {sorted(ROLES)}")

        raw = json.loads((d / "opencode.json").read_text())
        for key in ("provider", "model", "small_model", "enabled_providers"):
            if key in raw:
                rep.error(f"{label}/opencode.json: '{key}' belongs in core, not in a profile")

        try:
            cfg = render_mod.render(d.name, ap_home=root)
            headless = render_mod.render(d.name, headless=True, ap_home=root)
        except SystemExit as exc:
            rep.error(f"{label}: render failed: {exc}")
            continue
        check_rendered(cfg, label, model_vars, rep)

        agents = {k for k, v in cfg.get("agent", {}).items() if not v.get("disable")}
        builtin = {"build", "plan", "general", "explore"}
        if meta.get("default_agent") not in agents | builtin:
            rep.error(f"{label}: default_agent '{meta.get('default_agent')}' is not an enabled agent")

        allowed = skill_patterns_ok(cfg, skills, label, rep)
        scopes = set(meta.get("skill_scopes") or [])
        for pattern in allowed:
            scope = pattern[:-2] if pattern.endswith("-*") else pattern
            if scope not in scopes:
                rep.error(f"{label}: skill permission '{pattern}' not listed in profile.yaml skill_scopes")

        hb = headless.get("permission", {}).get("bash")
        if not isinstance(hb, dict) or hb.get("*") != "deny":
            rep.error(f"{label}: headless config must default bash to deny")
        acknowledged = set(meta.get("headless_auto_approved") or [])
        bash_sets = [("global", hb if isinstance(hb, dict) else {})]
        for agent_name, agent in headless.get("agent", {}).items():
            if agent.get("disable"):
                continue
            ab = agent.get("permission", {}).get("bash")
            if isinstance(ab, dict):
                if ab.get("*") not in (None, "deny"):
                    rep.error(f"{label}: headless agent '{agent_name}' bash '*' must be deny")
                bash_sets.append((f"agent '{agent_name}'", ab))
        for where, rules in bash_sets:
            for pattern, action in rules.items():
                if action == "ask" and pattern not in acknowledged:
                    rep.error(f"{label}: headless {where} bash '{pattern}' is 'ask', which --auto approves. "
                              f"Deny it in a headless overlay or list it in profile.yaml headless_auto_approved")

        if gl.get("role") == "reporter":
            if cfg.get("permission", {}).get("edit") != "deny":
                rep.error(f"{label}: reporter-role profile must set permission.edit to deny")
            for agent_name, agent in cfg.get("agent", {}).items():
                if not agent.get("disable") and agent.get("permission", {}).get("edit") == "allow":
                    rep.error(f"{label}: reporter-role profile agent '{agent_name}' allows edits")
    return names


def check_skill_scopes(skills: dict[str, dict], profile_names: list[str], rep: Report) -> None:
    scopes = {"core", *profile_names}
    for name in skills:
        if not any(name == s or name.startswith(s + "-") for s in scopes):
            rep.error(f"skills/{name}: scope prefix must be 'core' or a profile ({', '.join(sorted(scopes))})")


def check_evals(root: Path, profile_names: list[str], rep: Report) -> None:
    evals = root / "evals"
    for path in sorted(evals.glob("*/*.yaml")):
        rel = path.relative_to(root)
        task = yaml.safe_load(path.read_text()) or {}
        missing = EVAL_KEYS - set(task)
        if missing:
            rep.error(f"{rel}: missing keys {sorted(missing)}")
            continue
        if task["profile"] not in profile_names:
            rep.error(f"{rel}: unknown profile '{task['profile']}'")
            continue
        if not (evals / "fixtures" / task["fixture"]).is_dir():
            rep.error(f"{rel}: fixture evals/fixtures/{task['fixture']} not found")
        cfg = render_mod.render(task["profile"], ap_home=root)
        agents = set(cfg.get("agent", {})) | {"build", "plan"}
        if task["agent"] not in agents:
            rep.error(f"{rel}: agent '{task['agent']}' not defined for profile {task['profile']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    root = args.root.resolve()

    rep = Report()
    check_parse(root, rep)
    check_secrets(root, rep)
    skills = load_skills(root, rep)
    names = check_profiles(root, skills, rep)
    check_skill_scopes(skills, names, rep)
    check_evals(root, names, rep)

    for w in rep.warnings:
        print(f"warning: {w}")
    for e in rep.errors:
        print(f"error: {e}")
    print(f"check: {len(skills)} skills, {len(names)} profiles, {len(rep.errors)} errors, {len(rep.warnings)} warnings")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
