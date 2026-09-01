#!/usr/bin/env bash
set -euo pipefail

missing=0
for cmd in git gh jq claude; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '%-8s %s\n' "$cmd" "$(command -v "$cmd")"
  else
    echo "missing required command: $cmd" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 127
fi

echo
git --version
gh --version | head -n 1
jq --version
claude --version

runner_venv="${CLAUDE_RUNNER_VENV:-$HOME/.venvs/cn-property-agent}"
if [[ -x "$runner_venv/bin/python" ]]; then
  echo "python runner venv: $runner_venv"
  "$runner_venv/bin/python" --version
else
  echo "python runner venv not found at: $runner_venv"
  echo "This is optional, but pytest/ruff dependencies should be available to the runner."
fi

if [[ "${CLAUDE_RUNNER_SMOKE_TEST:-0}" == "1" ]]; then
  echo
  echo "Running one-turn Claude smoke test..."
  result="$(claude -p 'Reply exactly with RUNNER_OK' --max-turns 1 --output-format text)"
  echo "$result"
  grep -q 'RUNNER_OK' <<<"$result"
else
  echo
  echo "Set CLAUDE_RUNNER_SMOKE_TEST=1 to verify non-interactive Claude authentication."
fi
