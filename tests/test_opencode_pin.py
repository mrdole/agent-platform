"""G14: OpenCode 2.x is pinned and installed without npm."""
import re
from pathlib import Path

import yaml

import setup_wizard as sw

ROOT = Path(__file__).resolve().parent.parent


def test_group_vars_and_example_pin_2x_with_archive_and_no_url():
    for rel in ("ansible/group_vars/all.yml", "ansible/site.example.yml"):
        data = yaml.safe_load((ROOT / rel).read_text())
        assert data["opencode_version"] == "2.0.27", rel
        assert data["opencode_install"] == "archive", rel
        assert data["opencode_archive_url"] == "", rel   # a site setting, never a baked-in URL


def test_tools_role_fails_early_and_stays_builtin():
    text = (ROOT / "ansible/roles/tools/tasks/main.yml").read_text()
    tasks = yaml.safe_load(text)
    first_assert = next(t for t in tasks if "ansible.builtin.assert" in t)
    conds = " ".join(first_assert["ansible.builtin.assert"]["that"])
    assert "npm" in conds and "archive" in conds and "opencode_archive_url" in conds
    assert tasks.index(first_assert) < next(i for i, t in enumerate(tasks) if t.get("name", "").startswith("Install OpenCode"))
    modules = set(re.findall(r"^\s+(ansible\.\w+\.\w+):", text, re.M))
    assert modules and all(m.startswith("ansible.builtin.") for m in modules)


def test_no_text_tells_reader_to_install_2x_with_npm():
    for rel in ("tools/doctor.py", "tools/setup_wizard.py"):
        assert "opencode_install: npm)" not in (ROOT / rel).read_text()


def test_wizard_2x_npm_warning_names_alternatives():
    values = {"ap_llm_base_url": "https://llm.corp/v1", "opencode_install": "npm", "opencode_version": "2.0.27"}
    warning = " ".join(sw.site_warnings(values))
    assert "archive" in warning and "none" in warning and "ETARGET" in warning
