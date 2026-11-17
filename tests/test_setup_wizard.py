import stat

import pytest

import setup_wizard as sw


class ScriptedUI(sw.UI):
    """Answers prompts from a list; an empty string means 'press Enter'."""

    def __init__(self, answers, secrets=()):
        self.answers, self.secrets, self.lines = list(answers), list(secrets), []

    def say(self, text=""):
        self.lines.append(text)

    def ask(self, prompt, default=""):
        return self.answers.pop(0) or default

    def confirm(self, prompt, default=False):
        answer = self.answers.pop(0)
        return default if answer == "" else answer == "y"

    def secret(self, prompt):
        return self.secrets.pop(0)

    @property
    def text(self):
        return "\n".join(self.lines)


SITE_ANSWERS = ["https://llm.corp/v1", "https://gitlab.corp/", "", "", "", "", "none", "2.0.27", "", "", ""]


def test_set_value_keeps_comment_and_quotes_when_needed():
    text = 'ap_llm_base_url: https://CHANGEME/v1         # model server\nopencode_version: "1.19.5"  # pinned\n'
    text = sw.set_value(text, "ap_llm_base_url", "https://llm.corp/v1")
    text = sw.set_value(text, "opencode_version", "2.0.27")
    assert 'ap_llm_base_url: "https://llm.corp/v1"         # model server' in text
    assert 'opencode_version: "2.0.27"  # pinned' in text
    assert sw.get_value(text, "ap_llm_base_url") == "https://llm.corp/v1"
    assert sw.get_value(sw.set_value(text, "ap_corp_ca_files", ["/a.crt", "/b.crt"]), "ap_corp_ca_files") == ["/a.crt", "/b.crt"]


def test_written_site_file_is_valid_yaml_with_no_placeholders(tmp_path):
    yaml = pytest.importorskip("yaml")
    site = tmp_path / "site.yml"
    ui = ScriptedUI(SITE_ANSWERS + ["y"])
    sw.step_site(ui, site, dry_run=False)
    data = yaml.safe_load(site.read_text())
    assert data["ap_llm_base_url"] == "https://llm.corp/v1"
    assert data["ap_gitlab_host"] == "gitlab.corp"          # scheme and slash stripped
    assert data["opencode_install"] == "none" and data["opencode_version"] == "2.0.27"
    assert data["ap_corp_ca_files"] == []
    assert sw.PLACEHOLDER not in "\n".join(l.split("#")[0] for l in site.read_text().splitlines())


def test_rerun_defaults_to_existing_values_and_dry_run_writes_nothing(tmp_path):
    site = tmp_path / "site.yml"
    sw.step_site(ScriptedUI(SITE_ANSWERS + ["y"]), site, dry_run=False)
    before = site.read_text()
    ui = ScriptedUI([""] * len(SITE_ANSWERS))               # Enter on every prompt
    sw.step_site(ui, site, dry_run=False)
    assert site.read_text() == before and "already up to date" in ui.text

    other = tmp_path / "other.yml"
    sw.step_site(ScriptedUI(SITE_ANSWERS), other, dry_run=True)
    assert not other.exists()


def test_bad_values_stop_before_writing(tmp_path):
    site = tmp_path / "site.yml"
    ui = ScriptedUI(["llm.corp", "gitlab.corp", "/no/such/ca.crt", "", "", "", "archive", "2.0.27", "", "", "", ""])
    with pytest.raises(sw.Quit):
        sw.step_site(ui, site, dry_run=False)
    assert not site.exists()
    assert "ap_llm_base_url must be a full URL" in ui.text
    assert "CA file not found" in ui.text and "needs opencode_archive_url" in ui.text


def test_npm_with_opencode_2_warns():
    values = {"ap_llm_base_url": "https://llm.corp/v1", "opencode_install": "npm", "opencode_version": "2.0.27"}
    assert any("npm" in w for w in sw.site_warnings(values))
    assert not sw.site_warnings({**values, "opencode_version": "1.19.5"})


def test_key_goes_only_into_secrets_file_with_mode_600(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text("# KEY=value lines\n# CORP_LLM_KEY=\n# GITLAB_TOKEN_FIRMWARE=\n")
    ui = ScriptedUI([], secrets=["s3cr3t-value"])
    sw.step_key(ui, tmp_path, dry_run=False)
    assert sw.read_env(secrets) == {"CORP_LLM_KEY": "s3cr3t-value"}
    assert "# GITLAB_TOKEN_FIRMWARE=" in secrets.read_text()
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert "s3cr3t-value" not in ui.text

    sw.step_key(ScriptedUI(["n"]), tmp_path, dry_run=False)   # already set, keep it: no secret asked
    assert sw.read_env(secrets)["CORP_LLM_KEY"] == "s3cr3t-value"


def test_wizard_skips_and_quits_without_running_anything(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(sw.subprocess, "run", lambda *a, **k: ran.append(a) or pytest.fail("ran a command"))
    ui = ScriptedUI(["s", "s", "q"])
    assert sw.wizard(ui, tmp_path) == 1
    assert ran == [] and ui.text.count("skipped") == 2 and not (tmp_path / "site.yml").exists()


def test_dry_run_prints_commands_but_runs_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sw.subprocess, "run", lambda *a, **k: pytest.fail("ran a command"))
    ui = ScriptedUI(["y", "s", "y", "y", "s", "s"])           # from step 2: install, skip key, doctor, probe
    assert sw.wizard(ui, tmp_path, dry_run=True, first=2) == 0
    assert "$ ./bootstrap.sh" in ui.text and "$ core/bin/agent doctor --network" in ui.text
    assert "tools/models.py probe --location corp-llm" in ui.text
