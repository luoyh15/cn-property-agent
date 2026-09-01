# Local Claude Code Runner

This repository uses GitHub Issues as a task queue for a **local self-hosted Claude Code worker**.

```text
ChatGPT / repository owner
        ↓
owner creates [claude] GitHub Issue
        ↓
GitHub Actions
        ↓
self-hosted runner on local Linux / WSL
        ↓
uv sync
        ↓
local claude -p
        ↓
uv run ruff / pytest
        ↓
branch + PR
```

The workflow invokes the locally installed `claude` executable so the runner can reuse the development machine's Claude Code authentication and local toolchain.

## Security model

This repository is public. The runner therefore does **not** execute arbitrary public issues or pull requests.

A task is accepted only when both conditions hold:

1. the Issue author equals `github.repository_owner`;
2. the Issue title starts with `[claude]`.

The shell runner re-fetches the Issue and repeats these checks before invoking Claude.

Automated tasks cannot commit changes to the automation/agent trust boundary, including `AGENTS.md`, Claude project configuration, MCP configuration, GitHub workflows/actions, `.gitmodules`, VS Code auto-run tasks, or `scripts/run-claude-task.sh`.

Automated Claude sessions also load only trusted user-level Claude settings, disable project auto-memory, use an empty strict MCP configuration, disable Claude web search/fetch, and allow only selected local commands. Git commit/push/PR creation is handled by the outer shell rather than Claude.

Even with these gates, a self-hosted coding runner is powerful because project tests execute locally. Prefer a dedicated OS user without unrelated credentials or sensitive files.

## Prerequisites

The runner host needs:

- Linux or WSL
- `git`
- GitHub CLI `gh`
- `jq`
- `uv`
- Claude Code CLI (`claude`)

From the repository root run:

```bash
bash scripts/check-claude-runner.sh
```

Then verify non-interactive Claude authentication:

```bash
CLAUDE_RUNNER_SMOKE_TEST=1 bash scripts/check-claude-runner.sh
```

The smoke test should print `RUNNER_OK`.

## Python environment: uv only

The project does not maintain a separate runner virtualenv. `uv` owns the repository-local `.venv`.

Initial setup:

```bash
uv sync
```

Normal development commands:

```bash
uv run pytest -q
uv run ruff check .
```

Add dependencies with:

```bash
uv add <package>
uv add --dev <package>
```

Do not use `pip install -e '.[dev]'` or create a second project virtualenv. Development dependencies live in the standard `[dependency-groups].dev` section of `pyproject.toml`.

Once `uv.lock` is committed, CI and the Claude runner automatically switch to locked/reproducible sync. Before the first lockfile is committed they perform a normal `uv sync`.

## Authenticate Claude Code

The **same OS user that runs the GitHub runner** must be able to execute:

```bash
claude -p 'Reply exactly with RUNNER_OK' --max-turns 1
```

If the runner uses a dedicated account, authenticate Claude Code as that account.

## Install the GitHub self-hosted runner

Open:

```text
Repository → Settings → Actions → Runners → New self-hosted runner
```

Choose Linux and follow GitHub's generated commands. During `config.sh`, add the custom label:

```text
claude-code
```

Conceptually:

```bash
./config.sh \
  --url https://github.com/luoyh15/cn-property-agent \
  --token '<temporary-registration-token>' \
  --labels claude-code
```

Do not commit or share the registration token.

For the first test, running the runner in the foreground is useful:

```bash
./run.sh
```

After confirming it can see `gh`, `uv`, and `claude`, it can be installed as a service:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status
```

The GitHub runner should show labels including:

```text
self-hosted
claude-code
```

## GitHub permissions

Inside Actions, `GH_TOKEN` comes from the workflow's scoped `github.token`. The workflow requests:

```text
contents: write
issues: write
pull-requests: write
```

If push or PR creation fails, inspect repository Actions permissions before adding any personal token.

## Dispatching a task

Create an owner-authored Issue whose title begins with `[claude]`, for example:

```text
[claude] Implement transaction ingestion service
```

The body should contain the objective, architectural constraints, acceptance criteria, and relevant context. ChatGPT can create these Issues through the connected GitHub account.

## Execution sequence

For each trusted Issue the worker:

1. validates Issue author/title;
2. creates a fresh branch from the default branch;
3. runs `uv sync` (`--locked` when `uv.lock` is committed);
4. gives Claude the Issue and repository instructions;
5. lets Claude modify business code with restricted tools;
6. runs `uv run --no-sync ruff check .` and `uv run --no-sync pytest -q`;
7. gives Claude one repair attempt if checks fail;
8. discards protected automation/configuration changes;
9. pushes remaining changes;
10. opens a normal PR if checks pass, otherwise a Draft PR;
11. comments the PR URL on the Issue.

## Troubleshooting

### Job stays queued

The self-hosted runner is offline or lacks the `claude-code` label.

### `uv`, `gh`, or `claude` is not found in Actions

The runner service may have a different `PATH` from the interactive VS Code shell. Verify the service runs as the expected OS user and expose that user's binary paths to the service environment.

### Claude works interactively but fails in Actions

Run the smoke test as the exact OS user running the GitHub runner service.

### `uv sync` fails

Confirm the runner has network/package-index access and that the project metadata is valid. When `uv.lock` exists, also confirm it is consistent with `pyproject.toml`.

### Push/PR permission errors

Check GitHub repository Actions permissions. Avoid placing a broad personal access token on a public self-hosted runner unless there is a specific reviewed need.
