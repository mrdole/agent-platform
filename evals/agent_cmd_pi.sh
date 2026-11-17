#!/usr/bin/env bash
# AP_EVAL_AGENT_CMD wrapper that runs the Pi coding agent instead of OpenCode.  UNTESTED.
#
# Usage:  AP_EVAL_AGENT_CMD=evals/agent_cmd_pi.sh python3 evals/run.py --profile firmware
#
# Accepts the arguments evals/run.py passes:
#   --headless <profile> run [--standalone] [--format json] --agent <agent> --auto <prompt>
# Assumptions (none verified here):
#   * `pi` is on PATH and runs non-interactively with `pi -p "<prompt>"`, printing the
#     final answer to stdout and editing files in the current directory.
#   * Pi needs no profile, agent or permission flags; they are ignored, so Pi runs with its
#     own settings and none of this platform's permission rules. Pi does not emit OpenCode's
#     JSON event stream, so tools.jsonl will be empty and output.md is the raw text.
#   * Set AP_PI_CMD to override the binary/flags, e.g. AP_PI_CMD="pi --model x -p".
set -euo pipefail
prompt=""
while [ $# -gt 0 ]; do
  case "$1" in
    --headless|--agent) shift 2 ;;
    run|--standalone|--auto|--format) [ "$1" = "--format" ] && shift; shift ;;
    *) prompt="$1"; shift ;;
  esac
done
[ -n "$prompt" ] || { echo "agent_cmd_pi: no prompt given" >&2; exit 2; }
# shellcheck disable=SC2086
exec ${AP_PI_CMD:-pi -p} "$prompt"
