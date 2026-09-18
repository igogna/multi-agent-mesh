# agentdev — How to Use

`agentdev` automates the requirement → code → tests → PR → automated review → human-merge-gate
lifecycle. Install it once on your machine; configure it per project you use it on.

## 1. Install (once, on your machine)

Requires Python 3.10+.

```bash
uv tool install agentdev
```

Not published to a package index yet, so for now use one of these instead:

```bash
# from a git URL
uv tool install git+<repo-url>

# from a local checkout
git clone <repo-url> && cd <repo> && uv tool install .

# from a built wheel
uv build && uv tool install dist/agentdev-*.whl
```

`agentdev --version` should print a version and a git SHA once it's installed. Docker Desktop is
only needed later, and only if you want the test/lint retry loop.

## 2. Set your Anthropic API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # bash/zsh — put this in your shell profile
$env:ANTHROPIC_API_KEY = "sk-ant-..."      # PowerShell
```

Get a key at console.anthropic.com. This is read directly from the environment — there's nothing
to configure for it in any `agentdev` config file.

## 3. Set up a project

`cd` into the git repo you want to use `agentdev` on, then:

```bash
agentdev init
```

This will, interactively:

- detect your GitHub remote and offer it as the default repo
- detect the actual default branch from that remote (it does not assume `main`)
- check whether you're logged into the `gh` CLI — if so, it uses that session and never asks for a token
- otherwise, prompt for a GitHub token (hidden input) with push access to the repo
- validate everything live: repo reachable, branch exists, token has push rights, Anthropic key works
- write `.agentdev.toml` in the project root — **commit this to git**, it has no secrets in it
- write `~/.config/agentdev/config.toml` only if a token fallback was needed — **never commit this**,
  it's per-user and chmod'd 600

Run `agentdev init --force` to overwrite an existing `.agentdev.toml`.

## 4. Index the codebase (Project Intelligence)

```bash
agentdev bootstrap
```

First-time indexing so the agent has structural + doc-derived context on this repo before it plans
any work. Runs Graphify's structural pass (installing `graphify` if needed) and ingests root-level
docs (`README.md`, `CONTRIBUTING.md`, `docs/**/*.md`), saving evidence-tagged facts under
`.project-intelligence/`. Pass `--interactive` to also answer a short Q&A pass (auth, data model,
deployment, gotchas) that seeds the highest-impact unknowns.

`.project-intelligence/` and `graphify-out/` are local, gitignored, and derived entirely from your
current checkout — **not committed**. Every teammate runs `agentdev bootstrap` themselves after
cloning/pulling so their index matches the code they actually have; committing it would just produce
timestamp-churn diffs on every re-run. Re-run `agentdev bootstrap` any time the codebase has moved on
enough that the index feels stale.

## 5. Verify your setup

```bash
agentdev doctor
```

Runs pass/fail checks — git identity, `ANTHROPIC_API_KEY`, GitHub auth + push rights, base branch
exists on the remote, Docker daemon reachable, `.agentdev.toml` found and parseable — each with a
one-line fix if it fails. `agentdev run` automatically runs the fast subset of these (everything
but Docker) before doing any work, and prints warnings if something's off.

## 6. Run it

```bash
agentdev run \
  --ticket-id AD-101 \
  --requirement "Add input validation to the signup form: reject empty emails and passwords under 8 characters." \
  --repo-path /path/to/the/local/checkout/agentdev/should/edit
```

**Important:** `--requirement` and `--repo-path` are both required — there is no bundled demo
default. Omitting `--requirement` fails fast with a "No requirement detected" error instead of
running against some placeholder project; omitting `--repo-path` falls back to this project's own
root (the nearest `.git` ancestor of your current directory), not a fixture.

What happens: analyzes the requirement against the repo, generates code + tests, runs them (skip by
default — pass `--run-tests` to enable; needs Docker), scans the diff for secrets, opens a PR, and
requests an automated review. Approval lands at a **human merge gate** — the agent never merges its
own PR. `--ticket-id` names the branch/PR (`feature/<your-github-login>/AD-101`) and is required to
resume this later with `agentdev check`.

Flags:

| Flag | Meaning |
|---|---|
| `--requirement TEXT` | The natural-language requirement to implement (required — no default) |
| `--repo-path PATH` | Local path to the repo the agent reads/edits (default: this project's root) |
| `--base-branch BRANCH` | Overrides the branch resolved from `.agentdev.toml`/env/default |
| `--ticket-id ID` | e.g. `AD-101` — branch/commit/PR naming, and the key for `agentdev check` |
| `--skip-tests` | Skip the Docker/pytest test-and-lint step (default: on) |
| `--run-tests` | Re-enable the Docker/pytest retry loop |

## 7. Follow up after human review

```bash
agentdev check --ticket-id AD-101
```

Makes one cheap GitHub read of the PR's real review state. If a human requested changes, pushes a
revision addressing that feedback — it never re-derives the plan or opens a new PR. Run this again
any time; there's no polling loop, and it's a no-op if there's nothing new to act on.

## Config reference

| Layer | Location | Committed? | Contains |
|---|---|---|---|
| Project | `.agentdev.toml` at the git root | Yes | `repo`, `base_branch`, `ticket_prefix`, `model`, `max_iterations`, `max_human_review_rounds` |
| User | `~/.config/agentdev/config.toml` | Never | `github_token` (fallback only) |
| Environment | `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`, `GITHUB_BASE_BRANCH` | N/A | Highest-priority overrides |

Precedence: **CLI flag > environment variable > project config > user config > built-in default.**

### GitHub auth, specifically

Resolved in this order — first one found wins:

1. `gh auth token` (you're logged into the GitHub CLI)
2. `GITHUB_TOKEN` environment variable
3. `github_token` in `~/.config/agentdev/config.toml`

Run `gh auth login` once and you never have to manage a token yourself.

## Where things are stored

- `.agent_runs/<ticket-id>.json` — at your project's git root; full run state, resumed by `agentdev check`
- `~/.local/share/agentdev/runs.jsonl` — append-only, one line per completed run (timestamp, ticket,
  repo, your GitHub login, iterations used, human review rounds used, terminal outcome, agentdev version)

## Troubleshooting

Always start with `agentdev doctor`. Common fixes:

| Problem | Fix |
|---|---|
| `git user.name/user.email not set` | `git config --global user.name "Your Name"` and `... user.email you@example.com` |
| `ANTHROPIC_API_KEY is not set` | Export it in your shell profile so it persists across sessions |
| `no usable GitHub token` | `gh auth login`, or set `GITHUB_TOKEN`, or re-run `agentdev init` |
| `token does not have push access` | Use a token/account with push rights to the repo |
| `Docker daemon not reachable` | Only needed for `--run-tests` — start Docker Desktop, or drop that flag |
| `.agentdev.toml not found` | Run `agentdev init` in the project root |

## Uninstall / reset

```bash
uv tool uninstall agentdev
rm .agentdev.toml                    # per project, if you want to remove it
rm -rf ~/.config/agentdev            # your personal token fallback
rm -rf ~/.local/share/agentdev       # run history log
```
