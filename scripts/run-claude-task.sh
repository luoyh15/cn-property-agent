#!/usr/bin/env bash
set -Eeuo pipefail

issue_number="${1:-}"
if [[ -z "$issue_number" ]]; then
  echo "usage: $0 <issue-number>" >&2
  exit 2
fi

repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
owner="${GITHUB_REPOSITORY_OWNER:?GITHUB_REPOSITORY_OWNER is required}"
run_id="${GITHUB_RUN_ID:-manual}"
max_turns="${CLAUDE_MAX_TURNS:-40}"

for cmd in git gh jq claude; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "required command not found: $cmd" >&2
    exit 127
  fi
done

# Optional persistent Python environment owned by the runner user.
runner_venv="${CLAUDE_RUNNER_VENV:-$HOME/.venvs/cn-property-agent}"
if [[ -x "$runner_venv/bin/python" ]]; then
  export PATH="$runner_venv/bin:$PATH"
fi

issue_json="$(gh api "repos/$repo/issues/$issue_number")"
author="$(jq -r '.user.login' <<<"$issue_json")"
title="$(jq -r '.title' <<<"$issue_json")"
body="$(jq -r '.body // ""' <<<"$issue_json")"
is_pr="$(jq -r 'has("pull_request")' <<<"$issue_json")"

if [[ "$is_pr" == "true" ]]; then
  echo "refusing to run an issue workflow against a pull request" >&2
  exit 3
fi
if [[ "$author" != "$owner" ]]; then
  echo "refusing untrusted issue author: $author" >&2
  exit 3
fi
if [[ "$title" != "[claude]"* ]]; then
  echo "refusing issue without [claude] title prefix" >&2
  exit 3
fi

base_branch="$(gh repo view "$repo" --json defaultBranchRef --jq '.defaultBranchRef.name')"
branch="claude/issue-${issue_number}-${run_id}"

on_error() {
  local code=$?
  trap - ERR
  gh issue comment "$issue_number" --repo "$repo" --body \
    "Claude runner failed before creating a PR. See GitHub Actions run ${GITHUB_SERVER_URL:-https://github.com}/${repo}/actions/runs/${run_id}." \
    >/dev/null 2>&1 || true
  exit "$code"
}
trap on_error ERR

git fetch origin "$base_branch"
git checkout -B "$branch" "origin/$base_branch"
git config user.name "claude-code-runner"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

mkdir -p .git/claude-task
prompt_file=".git/claude-task/issue-${issue_number}.md"
claude_log=".git/claude-task/claude-${issue_number}.log"
check_log=".git/claude-task/checks-${issue_number}.log"

cat >"$prompt_file" <<EOF
You are implementing GitHub Issue #${issue_number} in repository ${repo}.

Read AGENTS.md before changing code. Follow the repository architecture and coding rules.
Work only in the current checkout. The outer runner handles git commit, push, and pull-request creation.
Do not change AGENTS.md, .github/workflows, .github/actions, .gitmodules, or scripts/run-claude-task.sh.
Do not access credentials or unrelated files outside the repository.
Do not bypass authentication, CAPTCHA, anti-bot, access controls, or source terms.
Prefer small, testable changes and add/update fixture-based tests when appropriate.
Run relevant tests using only the allowed commands. If information is missing, make the safest reasonable implementation and document the assumption.

Issue title:
${title}

Issue body:
${body}
EOF

allowed_tools=(
  "Read"
  "Glob"
  "Grep"
  "Edit"
  "Write"
  "Bash(git status:*)"
  "Bash(git diff:*)"
  "Bash(git log:*)"
  "Bash(pytest:*)"
  "Bash(python -m pytest:*)"
  "Bash(ruff:*)"
  "Bash(python -m ruff:*)"
  "Bash(ls:*)"
  "Bash(find:*)"
)

run_claude() {
  local prompt="$1"
  claude -p "$prompt" \
    --max-turns "$max_turns" \
    --output-format text \
    --allowedTools "${allowed_tools[@]}" \
    --disallowedTools "WebFetch" "WebSearch" \
    | tee -a "$claude_log"
}

run_checks() {
  local status=0
  : >"$check_log"

  if command -v ruff >/dev/null 2>&1; then
    echo '== ruff check .' | tee -a "$check_log"
    ruff check . 2>&1 | tee -a "$check_log" || status=1
  elif python -c 'import ruff' >/dev/null 2>&1; then
    echo '== python -m ruff check .' | tee -a "$check_log"
    python -m ruff check . 2>&1 | tee -a "$check_log" || status=1
  else
    echo '== ruff unavailable; skipped' | tee -a "$check_log"
  fi

  if command -v pytest >/dev/null 2>&1; then
    echo '== pytest -q' | tee -a "$check_log"
    pytest -q 2>&1 | tee -a "$check_log" || status=1
  elif python -c 'import pytest' >/dev/null 2>&1; then
    echo '== python -m pytest -q' | tee -a "$check_log"
    python -m pytest -q 2>&1 | tee -a "$check_log" || status=1
  else
    echo '== pytest unavailable; skipped' | tee -a "$check_log"
  fi

  return "$status"
}

run_claude "$(cat "$prompt_file")"

checks_passed=true
if ! run_checks; then
  checks_passed=false
  repair_prompt="$(cat <<EOF
The implementation for GitHub Issue #${issue_number} has failing checks. Inspect the current working tree, fix the failures without changing protected automation/policy files, and rerun the relevant checks.

Check output:
$(tail -n 250 "$check_log")
EOF
)"
  run_claude "$repair_prompt"
  if run_checks; then
    checks_passed=true
  fi
fi

# Automation and policy files are maintained manually, never by task execution.
protected_changed=false
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  case "$path" in
    AGENTS.md|.gitmodules|scripts/run-claude-task.sh|.github/workflows/*|.github/actions/*)
      protected_changed=true
      if git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
        git restore --source="origin/$base_branch" --staged --worktree -- "$path" || true
      else
        rm -rf -- "$path"
      fi
      ;;
  esac
done < <(git status --porcelain=v1 | sed -E 's/^.. //' | sed -E 's/^.* -> //')

if [[ -z "$(git status --porcelain=v1)" ]]; then
  msg="Claude completed Issue #${issue_number} but produced no committable changes."
  [[ "$protected_changed" == "true" ]] && msg+=" Protected automation/policy changes were discarded."
  gh issue comment "$issue_number" --repo "$repo" --body "$msg"
  trap - ERR
  exit 0
fi

git add -A
git commit -m "claude: implement issue #${issue_number}"
git push --set-upstream origin "$branch"

pr_title="${title#\[claude\] }"
if [[ "$pr_title" == "$title" ]]; then
  pr_title="Claude task #${issue_number}"
fi

pr_body_file=".git/claude-task/pr-${issue_number}.md"
cat >"$pr_body_file" <<EOF
Automated implementation for #${issue_number} using the repository's self-hosted Claude Code runner.

## Runner status

- Final checks passed: **${checks_passed}**
- Protected automation/policy changes discarded: **${protected_changed}**

## Claude summary

\`\`\`
$(tail -n 120 "$claude_log")
\`\`\`

## Check output

\`\`\`
$(tail -n 160 "$check_log")
\`\`\`

Closes #${issue_number}
EOF

pr_args=(
  pr create
  --repo "$repo"
  --head "$branch"
  --base "$base_branch"
  --title "$pr_title"
  --body-file "$pr_body_file"
)
if [[ "$checks_passed" != "true" ]]; then
  pr_args+=(--draft)
fi

pr_url="$(gh "${pr_args[@]}")"
gh issue comment "$issue_number" --repo "$repo" --body "Claude task completed: ${pr_url}"

trap - ERR
printf '%s\n' "$pr_url"
