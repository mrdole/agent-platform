#!/usr/bin/env bash
# First-time setup on RHEL 9 (WSL or bare metal), from a fresh clone:
#
#   ./bootstrap.sh                 install for your user (asks for your sudo password once)
#   ./bootstrap.sh --check         dry run
#   ./bootstrap.sh --tags platform any extra args go to ansible-playbook
#
# 1. installs ansible-core and git with dnf if missing
# 2. on first run, creates ~/.config/agent-platform/site.yml from ansible/site.example.yml
#    and stops so you can fill it in
# 3. runs the playbook against this checkout (nothing is cloned or copied)
# 4. runs `agent doctor` to show anything still missing
set -euo pipefail

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cfg_dir="${XDG_CONFIG_HOME:-$HOME/.config}/agent-platform"
site="$cfg_dir/site.yml"

if [[ -r /etc/os-release ]]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  if [[ "${ID:-}" != "rhel" && "${ID_LIKE:-}" != *rhel* ]] || [[ "${VERSION_ID%%.*}" != "9" ]]; then
    echo "bootstrap: this targets RHEL 9; found ${PRETTY_NAME:-unknown}. Continuing anyway." >&2
  fi
fi

if ! command -v ansible-playbook >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  echo "bootstrap: installing ansible-core and git (sudo)" >&2
  sudo dnf install -y ansible-core git
fi

if [[ ! -f "$site" ]]; then
  mkdir -p "$cfg_dir"
  cp "$repo/ansible/site.example.yml" "$site"
  echo "bootstrap: created $site"
  echo "bootstrap: fill in the CHANGEME values (LLM URL, GitLab host, CA files, mirrors), then run ./bootstrap.sh again."
  echo "bootstrap: or let the wizard ask for them: python3 tools/setup_wizard.py"
  exit 1
fi
if grep -qE "^[^#]*CHANGEME" "$site"; then
  echo "bootstrap: $site still has CHANGEME values:" >&2
  grep -nE "^[^#]*CHANGEME" "$site" >&2
  exit 1
fi

cd "$repo/ansible"
ansible-playbook site.yml --limit workstation -K \
  -e "@$site" -e ap_install_from=none -e "ap_home=$repo" "$@"

if [[ " $* " != *" --check "* ]]; then
  "$repo/core/bin/agent" doctor || true
fi
