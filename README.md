# loop-hooks

[![CI](https://github.com/wwwcojp/loop-hooks/actions/workflows/ci.yml/badge.svg)](https://github.com/wwwcojp/loop-hooks/actions/workflows/ci.yml)

A [Claude Code](https://docs.claude.com/en/docs/claude-code/hooks) hooks plugin
that enforces your repository's verification command (`verify`, `test`, `lint`,
whatever you use) when a turn ends — **but only when something has actually
changed since the last time the gate was green.**

日本語版: [README.ja.md](README.ja.md)

## Why

"Run a Stop hook that runs your tests" is a well-known Claude Code recipe. The
usual version runs on *every* turn: a turn that only answers a question still
waits for the full test suite, so the hook tends to get disabled.

loop-hooks makes the gate change-driven. On `Stop` it computes a fingerprint of
the files you told it to watch and compares it with the fingerprint recorded the
last time the gate passed. Identical? The turn ends immediately, nothing runs.
Different? Your command runs, and a failure keeps the turn open until it's fixed.

Because the fingerprint is computed from **git's view of the working tree**, not
from which tools Claude happened to call, it catches every edit path: `Edit` and
`Write`, `sed`/heredoc edits made through `Bash`, files written by subagents,
`git checkout`, formatters, code generators, lockfile churn. Revert an edit and
the fingerprint returns to its previous value, so a turn that breaks and then
fixes a file costs you nothing.

## Where it fits

*Loop engineering* is the term for the shift from prompting an agent directly to
designing the system that drives it — triggers, schedules, worktrees, `/goal`,
subagents, memory. [Addy Osmani](https://addyosmani.com/blog/loop-engineering/)
lays out the case and [Gergely Orosz](https://newsletter.pragmaticengineer.com/p/what-is-loop-engineering)
surveys how teams use it. Most of that scope is orchestration, and this plugin is
not involved in it.

What the plugin covers is one part of the loop's **stop condition**. Sonar's
[write-up](https://www.sonarsource.com/blog/loop-engineering-without-verification-is-just-automation/)
frames the central design question as *what decides that the loop is finished?*
If that decision is left to the model that did the work, or to a second model
asked to review it, it stays probabilistic, and the loop can report "done" on
unfinished work. Both that write-up and Osmani recommend a **deterministic
verification tier** as the countermeasure: in Osmani's terms, a check that can
fail the work, rather than a verifier that returns an opinion.

loop-hooks is a minimal implementation of that tier for Claude Code. It runs the
verification command the repository already has when the agent tries to stop,
and on failure returns the output to the agent as its next instruction.

Three design decisions follow:

- **Orientation and enforcement live in different layers.** `CLAUDE.md` tells
  the agent the policy; it can be dropped by context summarisation or reasoned
  around. A hook is guaranteed to run. Keep policy in `CLAUDE.md` and put the
  checks that must not be skipped in a hook.
- **The plugin carries no verification logic.** It runs a command and controls
  whether the turn ends. What the command checks — tests, type checks, lint,
  static analysis, contract suites — is decided by the repository in
  `.loop-hooks.json`. The same plugin therefore works for any language or stack,
  and tightening the gate is a change to the repository, not the plugin.
- **Verification is per turn, not per commit.** A pre-commit hook only fires if
  the agent commits; CI reports minutes later, outside the loop. A Stop hook
  returns the failure while the agent still has the context that produced it.
  Change detection keeps the per-turn cost acceptable.

A write-up of how this repository applies loop engineering to itself (the gate, dogfooding data,
the five verification stages and what each verifier caught) is published at
<https://wwwcojp.github.io/loop-hooks/> (Japanese).

## What it does

At session start, `hooks/session_start.py` validates the configuration and
announces the gate (never runs the command).

One script, `hooks/gate.py`, runs on the three events where an agent is about
to stop working — **Stop**, **SubagentStop** and **TeammateIdle** (pick which
ones with `gate.on`):

1. Resolves the repository root from the session's `cwd` (`git rev-parse --show-toplevel`),
   so it works from any subdirectory. In a git worktree this resolves to the
   worktree's own root, so each worktree is gated and recorded independently.
2. Loads `.loop-hooks.json` from that root. **No config file, no gate** — the
   plugin is inert in repositories that haven't opted in.
3. Computes the current fingerprint: the `HEAD` sha, plus the content hash of
   every path that differs from `HEAD` and matches `watch` without matching
   `ignore`.
4. If it equals the recorded fingerprint, returns immediately.
5. Otherwise runs `gate.command`. On success it records the fingerprint *taken
   after the command ran*, so a command that rewrites files (a formatter, say)
   doesn't trigger an endless re-run. On failure it returns
   the tail of the output as feedback, so the agent has to fix it before it can
   finish. `Stop` and `SubagentStop` get it as `hookSpecificOutput.additionalContext`;
   `TeammateIdle` uses a different protocol, so it gets exit code 2 with the same
   text on stderr.
6. It never traps an agent. The same fingerprint is never blocked twice for the
   same agent in a row — an unchanged tree means no fix was attempted — and on re-entry
   (`stop_hook_active`) a second failure becomes a `systemMessage` warning. Either
   way the fingerprint stays unrecorded, so the gate fires again on the next
   change.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) on `PATH`. Each hook is a self-contained
  `uv run --script`, so there is no Python environment to set up.
- **git.** Change detection is built on it. In a directory that isn't a git
  repository the gate stays disabled and emits a warning.

## Install

Add the marketplace and install the plugin from inside Claude Code:

```
/plugin marketplace add wwwcojp/loop-hooks
/plugin install loop-hooks@loop-hooks
```

Or declare the same thing in `~/.claude/settings.json`:

```json
"extraKnownMarketplaces": {
  "loop-hooks": {
    "source": {"source": "github", "repo": "wwwcojp/loop-hooks"}
  }
},
"enabledPlugins": {
  "loop-hooks@loop-hooks": true
}
```

Then drop a `.loop-hooks.json` in the root of each repository you want gated
(see [Configuration](#configuration)). Nothing happens in repositories that
don't have one.

To develop the plugin itself, point the marketplace at a local checkout instead:

```json
"extraKnownMarketplaces": {
  "loop-hooks": {
    "source": {"source": "directory", "path": "/path/to/loop-hooks"}
  }
}
```

## Configuration

`.loop-hooks.json`, at the repository root:

```json
{
  "gate": {
    "command": "bun run verify quick",
    "timeout_sec": 600,
    "watch": ["*.ts", "*.tsx", "package.json", "*tsconfig*.json"],
    "ignore": [".loop/*", "node_modules/*", "*.md"]
  }
}
```

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `gate.command` | yes | — | Run through a shell, so `&&`, pipes, `$VARS`, globs and `~` all work. |
| `gate.on` | no | all three | Which events to gate: `stop`, `subagent_stop`, `teammate_idle`. |
| `gate.timeout_sec` | no | `600` | Integer from 1 to 3000. On timeout the whole process group is killed, so no orphaned test runners. |
| `gate.watch` | no | `["*"]` | Paths that make the gate fire. Omitting `watch` means every file is watched. |
| `gate.ignore` | no | `["node_modules/", ".venv/", "dist/", "build/", "target/", ".claude/", ".loop/", "*.md"]` | Wins over `watch`. |

Patterns follow `.gitignore` rules, matched against repository-relative paths:

- `*` and `?` do not cross `/`. `**` crosses when it is followed by `/` or ends the
  pattern and no glob character comes before it (`**/x`, `x/**`, `x**/y`).
- A pattern without a slash (`*.md`, `node_modules`) matches at any depth; one with a
  slash (`docs/*`, `src/**/*.py`) is anchored at the repository root.
- A trailing `/` names a directory and matches everything under it (`node_modules/`). A
  trailing slash alone does not anchor: `node_modules/` still matches at any depth.
- A pattern that matches a directory also matches everything inside it, so `docs/*`
  still covers `docs/a/b.md`.
- `!pattern` cancels an earlier match in the same list; the last match wins.

If the file is present but invalid (bad JSON, missing or empty `gate.command`,
wrong types), the gate stays disabled and the Stop hook emits a `systemMessage`
explaining why rather than blocking the turn.

**Commit the file.** In a git repository the gate reads `.loop-hooks.json` from
`HEAD`, not from the working tree. Editing, breaking or deleting the file in the
working tree — which an agent can do — does not change or disable the gate; the
divergence is reported in a `systemMessage`. An uncommitted file still works,
with a one-time notice asking you to commit it.

## Pairings

What the gate achieves depends on `gate.command`. These combinations have worked
in practice. Ready-to-copy pieces — a standard-library verify runner template and
`.loop-hooks.json` examples for Python, Node, Rust and Go — live in
[`examples/README.md`](examples/README.md).

### TDD: enforce GREEN, leave RED to another hook

A Stop gate enforces the GREEN step of the TDD cycle (tests pass). It does not
cover RED (a failing test written first), so pair it with a `PreToolUse` guard
such as [tdd-guard](https://github.com/nizos/tdd-guard). tdd-guard rejects
implementation edits that have no failing test; loop-hooks rejects ending the
turn while a test fails.

```json
{"gate": {"command": "npm test -- --run", "watch": ["src/**", "test/**"]}}
```

### Use the same commands as CI

Put the CI commands in `gate.command` in the same order, and add a test that
checks the two definitions match. This guarantees that a locally green gate
implies a green CI run. Keep CI on the raw commands rather than calling the
runner, so the equality test compares two independent definitions.

```json
{"gate": {"command": "uv run ruff check . && uv run pytest -q"}}
```

### Split verification into stages

`gate.command` runs on every turn that changed a watched file, so limit the Stop
stage to fast checks and move slow ones to a separate stage. A verify runner with
`quick` / `mutation` / `all` stages has worked well: `quick` (lint plus unit
tests; measured at about one second for 240 tests in a Python repository) on
Stop, `all` at task completion.

```json
{"gate": {"command": "uv run python scripts/verify.py quick", "timeout_sec": 120}}
```

### Mutation testing with a ratchet

The gate guarantees that tests pass, not that tests are adequate — weakening an
assertion also makes tests pass. Mutation testing covers this. Run
[mutmut](https://mutmut.readthedocs.io/) (or Stryker, cargo-mutants, etc.) in
the slow stage, commit a per-file score baseline, and fail the stage if any file
drops below it (the ratchet). Measured on a security-guard codebase: 803 mutants
in about 11 seconds, and the surviving mutants pointed at boundary conditions
that were in fact untested. Too slow for every turn; use it as a task-completion
condition. This repository does exactly this for itself; see `scripts/verify.py mutation`.

### Include formatters and generators in the gate

The fingerprint recorded on success is taken *after* the command runs, so
`ruff format`, `prettier --write`, or a code generator can be part of
`gate.command` without the files they rewrite triggering the gate again on the
next turn.

```json
{"gate": {"command": "ruff format . && ruff check . && pytest -q"}}
```

### Multi-agent

With `gate.on` at its default (all three events), a subagent cannot complete and
a teammate cannot go idle while the gate is failing, so unverified work does not
reach the main agent. Overlapping gates cost nothing extra: if the subagent's
run left the tree verified, the main agent's Stop finds a matching fingerprint
and does not run the command.

### Combine with pre-execution guards

A `PreToolUse` deny/ask guard and a Stop gate have different roles. The guard
stops destructive commands, writes to secrets, or outbound requests carrying
tokens before they execute. The gate verifies the result before the turn ends.
Their scopes do not overlap, so use both.

## Out of scope

- **Not a replacement for CI.** CI remains the final judge. The plugin catches
  most of what CI would catch at a point where the agent can still fix it.
- **Does not judge test quality.** A passing gate means the command exited 0.
  Cover test quality with mutation testing as above.
- **Not an LLM review.** It operates as the deterministic tier. Judgements about
  intent or conformance to the request belong to `/goal` or a review subagent.
- **Does not loop forever.** It is deliberately fail-open: the same fingerprint
  is never blocked twice for the same agent (no change means no fix was attempted), a second failure
  on re-entry becomes a warning, and Claude Code stops after eight consecutive
  blocks. In every case the fingerprint stays unrecorded, so the gate runs again
  on the next change.

## State

**loop-hooks never writes inside your repository**, so there is nothing to add to
`.gitignore`. State lives in the plugin's persistent data directory, or under the
XDG cache when that isn't set (running the script by hand, for instance):

```
$CLAUDE_PLUGIN_DATA/state/<sha16-of-repo-path>.json
~/.cache/loop-hooks/state/<sha16-of-repo-path>.json
```

```json
{"root": "/home/alice/my-project", "verified": "9f2c…", "blocked": {"<session>/<agent>": "9f2c…"}}
```

`verified` is the fingerprint recorded the last time the gate passed and is shared by every
session in the worktree (same files, same verdict). `blocked` maps a scope — the session, the
subagent (`session/agent_id`) or the teammate (`session/teammate_name`) that received the
feedback — to the fingerprint it was blocked at, so the same agent is never blocked twice at
the same state while other agents still get the feedback once. It is cleared on every pass
and capped at 64 scopes. Delete the file to force the gate to run on the next turn.

## Troubleshooting

**Is the gate active here?** Run `/loop-hooks:status` inside Claude Code, or from a
terminal: `uv run /path/to/loop-hooks/hooks/gate.py --status [repo]`. It shows where
the configuration was read from, what the gate runs, whether it will run at the next
stop, and the last five decisions.

**No `[loop-hooks <version>] gate active:` line at session start** in a repository that has
`.loop-hooks.json`: the plugin is not loaded in this session. Hook definitions are
snapshotted when a session starts, so restart Claude Code after updating the plugin.

**Decision log:** `$CLAUDE_PLUGIN_DATA/state/<key>.log.jsonl`, one JSON line per
decision, newest last. `--status` prints the directory it read as `records`; from a
terminal (no `CLAUDE_PLUGIN_DATA`) it looks for `~/.claude/plugins/data/loop-hooks-*/`
and falls back to `~/.cache/loop-hooks/state/`. Failed runs record why (the first
failing line of the output), and the status output includes a summary of how often
the gate ran, passed and failed, and how long it takes.

## Manual smoke test

```bash
cd /tmp && rm -rf loop-smoke && mkdir loop-smoke && cd loop-smoke
git init -q && git commit -q --allow-empty -m init
echo '{"gate": {"command": "true", "watch": ["*.ts"]}}' > .loop-hooks.json
echo 'export const a = 1' > a.ts

echo '{"cwd":"'$PWD'","stop_hook_active":false}' | uv run ~/loop-hooks/hooks/gate.py
                        # no output: the gate ran and passed

echo '{"cwd":"'$PWD'","stop_hook_active":false}' | uv run ~/loop-hooks/hooks/gate.py
                        # no output: nothing changed, gate skipped
```

## Tests

```bash
uv run pytest -v
```

The repository gates itself with this plugin: `uv run python scripts/verify.py quick`
runs the same checks as CI's `test` job (home-path leak check, ruff check/format,
import-linter, pyright, pytest). pyright needs `node` on PATH, or downloads one on
first run.

`tests/test_properties.py` holds hypothesis property tests (25 examples in the gate and CI, 300 in
`verify.py all`).

`tests/contracts/*.json` are golden files for the hook I/O contract with Claude Code (input event →
output JSON, exit code, stderr), checked by `tests/test_contracts.py`. When the contract changes,
edit the golden by hand and update its `checked` date; there is no auto-update switch.
`tests/test_architecture.py` pins structural rules (entry-file imports, gate/status decision
parity, state written outside the repository, `hooks/lib` never raising).

`uv run python scripts/verify.py all` adds the `properties` stage (300 examples) and mutation testing (mutmut over `hooks/lib`; the
whole run takes about 1.5–3 minutes) with a per-file score ratchet in `tests/mutation-baseline.json`; it is not part of the gate.

## Limitations

- Requires a git repository.
- `hookSpecificOutput.additionalContext` on `Stop` needs a recent Claude Code; on
  older versions the feedback is ignored and the turn ends unverified.
- Claude Code ends the turn on its own after 8 consecutive Stop-hook blocks. The gate never
  gets there: a second failure at the same fingerprint is let through with a warning.

## License

MIT — see [LICENSE](LICENSE).
