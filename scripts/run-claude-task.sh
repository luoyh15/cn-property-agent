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

for cmd in git gh jq uv claude; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "required command not found: $cmd" >&2
    exit 127
  fi
done

# Keep automated sessions isolated from repository-supplied Claude settings,
# persistent project memory, and MCP servers. User-level settings remain
# available so the local machine can keep its trusted auth/gateway setup.
export CLAUDE_CODE_DISABLE_AUTO_MEMORY=1

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

# The repository-local .venv is fully managed by uv. Once uv.lock is committed,
# --locked makes every task reproduce the exact dependency graph. Before the
# first lockfile is committed, sync normally but do not let that incidental
# generated lockfile leak into an unrelated task PR.
lock_tracked=false
if git ls-files --error-unmatch uv.lock >/dev/null 2>&1; then
  lock_tracked=true
  uv sync --locked
else
  uv sync
  rm -f uv.lock
fi

mkdir -p .git/claude-task
prompt_file=".git/claude-task/issue-${issue_number}.md"
claude_log=".git/claude-task/claude-${issue_number}.log"
check_log=".git/claude-task/checks-${issue_number}.log"

cat >"$prompt_file" <<EOF
You are implementing GitHub Issue #${issue_number} in repository ${repo}.

Read AGENTS.md before changing code. Follow the repository architecture and coding rules.
Work only in the current checkout. The outer runner handles git commit, push, and pull-request creation.
Do not change AGENTS.md, CLAUDE.md, CLAUDE.local.md, .claude/, .mcp.json, .vscode/tasks.json, .github/workflows, .github/actions, .gitmodules, or scripts/run-claude-task.sh.
Do not access credentials or unrelated files outside the repository.
Do not bypass authentication, CAPTCHA, anti-bot, access controls, or source terms.
Prefer small, testable changes and add/update fixture-based tests when appropriate.
The project environment is managed exclusively by uv. For checks use commands such as `uv run --no-sync ruff check ...` and `uv run --no-sync pytest ...`; do not use pip or create another virtual environment.
If information is missing, make the safest reasonable implementation and document the assumption.

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
  "Bash(uv run --no-sync pytest:*)"
  "Bash(uv run --no-sync ruff:*)"
  "Bash(ls:*)"
)

run_claude() {
  local prompt="$1"
  claude -p "$prompt" \
    --max-turns "$max_turns" \
    --output-format text \
    --setting-sources user \
    --mcp-config '{"mcpServers":{}}' \
    --strict-mcp-config \
    --tools "Read,Glob,Grep,Edit,Write,Bash" \
    --allowedTools "${allowed_tools[@]}" \
    --disallowedTools "WebFetch" "WebSearch" \
    | tee -a "$claude_log"
}

run_checks() {
  local status=0
  : >"$check_log"

  echo '== uv run --no-sync ruff check .' | tee -a "$check_log"
  uv run --no-sync ruff check . 2>&1 | tee -a "$check_log" || status=1

  echo '== uv run --no-sync pytest -q' | tee -a "$check_log"
  uv run --no-sync pytest -q 2>&1 | tee -a "$check_log" || status=1

  return "$status"
}

run_claude "$(cat "$prompt_file")"

checks_passed=true
if ! run_checks; then
  checks_passed=false
  repair_prompt="$(cat <<EOF
The implementation for GitHub Issue #${issue_number} has failing checks. Inspect the current working tree, fix the failures without changing protected automation/policy files, and rerun the relevant uv-based checks.

Check output:
$(tail -n 250 "$check_log")
EOF
)"
  run_claude "$repair_prompt"
  if run_checks; then
    checks_passed=true
  fi
fi

# Automation, agent configuration, editor auto-run files, and policy files are
# maintained manually, never by task execution.
protected_changed=false
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  case "$path" in
    AGENTS.md|CLAUDE.md|CLAUDE.local.md|.gitmodules|.mcp.json|.claude/*|.vscode/tasks.json|scripts/run-claude-task.sh|.github/workflows/*|.github/actions/*)
      protected_changed=true
      if git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
        git restore --source="origin/$base_branch" --staged --worktree -- "$path" || true
      else
        rm -rf -- "$path"
      fi
      ;;
  esac
done < <(git status --porcelain=v1 | sed -E 's/^.. //' | sed -E 's/^.* -> //')

# A pre-lockfile task must not accidentally introduce a lockfile generated only
# as a side effect of environment setup. Dependency changes should deliberately
# include/update uv.lock in a dedicated reviewed change.
if [[ "$lock_tracked" != "true" && -f uv.lock ]]; then
  rm -f uv.lock
fi

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
