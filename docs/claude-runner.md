# Local Claude Code Runner

This repository can use GitHub Issues as a task queue for a **local self-hosted Claude Code worker**.

The intended flow is:

```text
ChatGPT / repository owner
        │
        ▼
owner creates [claude] GitHub Issue
        │
        ▼
.github/workflows/claude-task.yml
        │
        ▼
self-hosted runner on local Linux / WSL
        │
        ▼
scripts/run-claude-task.sh
        │
        ▼
local `claude -p` process
        │
        ├── edits checkout
        ├── runs allowed tests/lint
        └── receives one repair attempt on failed checks
        │
        ▼
runner commits + pushes branch
        │
        ▼
PR (draft when checks still fail)
```

The workflow deliberately invokes the locally installed `claude` executable instead of installing Claude Code inside the Action. This lets the runner reuse the development machine's Claude Code installation/authentication and local toolchain.

## Security model

This repository is public, so the local runner must **not** execute arbitrary public issues or pull requests.

The workflow has two independent gates:

1. the Issue author must equal `github.repository_owner`;
2. the Issue title must start with `[claude]`.

The runner re-fetches the Issue through the GitHub API and checks both conditions again before invoking Claude.

The automation also prevents task code from committing changes to:

- `AGENTS.md`
- `.gitmodules`
- `.github/workflows/*`
- `.github/actions/*`
- `scripts/run-claude-task.sh`

These files are treated as the automation/policy trust boundary and should be changed manually.

Claude is started with an explicit allow-list of editing/read tools and selected local commands. Web search/fetch tools are disabled for automated tasks. The outer shell, not Claude, performs commit, push and PR creation.

Even with these gates, a self-hosted runner is powerful. Prefer a dedicated OS user with access only to development resources that the agent actually needs. Do not put unrelated SSH keys, company credentials, wallets, browser profiles, or personal secrets in that user's home directory.

## 1. Prerequisites on the local machine

The runner host needs:

- Linux or WSL
- `git`
- GitHub CLI `gh`
- `jq`
- Claude Code CLI (`claude`)
- Python tooling needed by this repository

Check the basic tools from a repository checkout:

```bash
bash scripts/check-claude-runner.sh
```

To additionally verify that Claude can run non-interactively under the current OS user:

```bash
CLAUDE_RUNNER_SMOKE_TEST=1 bash scripts/check-claude-runner.sh
```

The smoke test should print `RUNNER_OK`.

## 2. Prepare a Python environment

The task script looks for a persistent environment at:

```text
~/.venvs/cn-property-agent
```

Create it once from a normal clone of this repository:

```bash
python3 -m venv ~/.venvs/cn-property-agent
~/.venvs/cn-property-agent/bin/python -m pip install -U pip
~/.venvs/cn-property-agent/bin/python -m pip install '.[dev]'
```

The workflow checkout itself remains isolated under the GitHub runner work directory; the persistent environment only supplies Python and installed dependencies.

If you want another path, set `CLAUDE_RUNNER_VENV` in the runner process environment.

Reinstall/update this environment whenever `pyproject.toml` dependencies change materially.

## 3. Authenticate Claude Code for the runner user

The workflow does not require an Anthropic API secret in GitHub when the local Claude Code installation is already authenticated.

The important point is that the **same OS user that runs the GitHub runner service** must be able to execute:

```bash
claude -p 'Reply exactly with RUNNER_OK' --max-turns 1
```

If the runner uses a dedicated account, authenticate Claude Code while logged in as that account rather than relying on another user's home directory.

## 4. Install the GitHub self-hosted runner

In GitHub open:

```text
Repository → Settings → Actions → Runners → New self-hosted runner
```

Choose Linux and follow the generated download/configuration commands. GitHub supplies a short-lived registration token in those instructions.

When configuring the runner, add the custom label:

```text
claude-code
```

For example, the configuration command will conceptually look like:

```bash
./config.sh \
  --url https://github.com/luoyh15/cn-property-agent \
  --token '<registration-token-from-github>' \
  --labels claude-code
```

Do not commit or share the registration token.

After a successful configuration, install/start it as a service if appropriate for the machine:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
```

Verify in GitHub that the runner is **Online** and has at least:

```text
self-hosted
claude-code
```

The workflow uses exactly these labels:

```yaml
runs-on: [self-hosted, claude-code]
```

## 5. GitHub CLI authentication

Inside GitHub Actions, `GH_TOKEN` is supplied from the workflow's scoped `github.token`, so the runner does not need to store a long-lived GitHub PAT for normal issue/branch/PR operations.

The workflow requests only:

```text
contents: write
issues: write
pull-requests: write
```

Repository/org policy can still restrict those permissions. If push or PR creation fails, check the repository Actions permissions first rather than adding a personal token.

## 6. Dispatching a task

Create an Issue using the **Claude code task** template, or create an Issue manually with a title beginning:

```text
[claude] ...
```

Example:

```text
[claude] Implement transaction ingestion service
```

The body should contain:

- objective
- architectural constraints
- acceptance criteria
- relevant context

The Issue must be authored by the repository owner. An Issue from any other account is intentionally ignored even if it copies the title prefix.

This makes ChatGPT → GitHub dispatch simple: ChatGPT only needs to create a well-specified owner Issue through the connected GitHub account.

## 7. What happens during execution

For each trusted Issue the worker:

1. revalidates Issue author/title;
2. discovers the repository default branch;
3. creates a branch named roughly `claude/issue-<n>-<run-id>` from the latest default branch;
4. gives Claude the Issue plus `AGENTS.md` instructions;
5. lets Claude edit using the allowed local tool set;
6. runs `ruff` and `pytest` when they are available;
7. if checks fail, gives Claude the failure output and one repair pass;
8. discards any attempted changes to protected automation/policy files;
9. commits and pushes remaining changes;
10. opens a normal PR if checks pass, otherwise a Draft PR;
11. comments the resulting PR URL on the Issue.

## 8. Manual re-dispatch

The workflow also supports `workflow_dispatch` with an Issue number. The shell-level trust checks still apply, so a manually supplied Issue must still be owner-authored and have the `[claude]` prefix.

Use this if a GitHub event was missed or after repairing the local runner.

## 9. Current limitations

The first version intentionally does not:

- execute public PR code automatically;
- react to arbitrary Issue comments;
- automatically continue an existing Claude session;
- merge PRs;
- modify its own workflow/security policy;
- expose unrestricted web/network research tools to Claude.

A later iteration can add an owner-only `/claude` PR follow-up path after the initial Issue → PR flow is stable.

## 10. Troubleshooting

### Job stays queued

The self-hosted runner is offline or does not have the `claude-code` label.

### `claude: command not found`

The runner service has a different `PATH` from the interactive VS Code shell. Install Claude Code for the runner user or expose the installation path to the service environment.

### Claude works interactively but fails in Actions

Confirm the runner service uses the same OS account/home directory as the authenticated Claude installation. Run the smoke test as that exact user.

### Tests are skipped

Create/update `~/.venvs/cn-property-agent` so that `pytest`, `ruff`, DuckDB, Pydantic and the project's other dependencies are available.

### Push/PR fails with permission errors

Check GitHub repository Actions settings and workflow token permissions. Avoid solving this by placing a broad personal access token on a public self-hosted runner unless there is a specific, reviewed need.
