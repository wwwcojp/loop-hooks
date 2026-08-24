# loop-hooks

A [Claude Code](https://docs.claude.com/en/docs/claude-code/hooks) hooks plugin
that enforces your repository's verification command (`verify`, `test`, `lint`,
whatever you use) when a turn ends — **but only when something has actually
changed since the last time the gate was green.**

日本語版: [README.ja.md](README.ja.md)

## Why

"Run a Stop hook that runs your tests" is a well-known Claude Code recipe. The
problem with the usual version is that it runs *every* turn: ask a question, get
an answer, wait 60 seconds for the test suite. People turn it off.

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

## What it does

A single **Stop** hook, `hooks/stop/gate.py`:

1. Resolves the repository root from the session's `cwd` (`git rev-parse --show-toplevel`),
   so it works from any subdirectory.
2. Loads `.loop-hooks.json` from that root. **No config file, no gate** — the
   plugin is inert in repositories that haven't opted in.
3. Computes the current fingerprint: the `HEAD` sha, plus the content hash of
   every path that differs from `HEAD` and matches `watch` without matching
   `ignore`.
4. If it equals the recorded fingerprint, returns immediately.
5. Otherwise runs `gate.command`. On success it records the fingerprint *taken
   after the command ran*, so a command that rewrites files (a formatter, say)
   doesn't trigger an endless re-run. On failure it returns
   `decision: "block"` with the tail of the output, so Claude must fix it.
6. If the gate fails again on re-entry (`stop_hook_active` is true), it does not
   trap the agent: it emits a `systemMessage` warning and lets the turn end. The
   fingerprint is left unrecorded, so the gate fires again next turn.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) on `PATH`. Each hook is a self-contained
  `uv run --script`, so there is no Python environment to set up.
- **git.** Change detection is built on it. In a directory that isn't a git
  repository the gate stays disabled and emits a warning.

## Install

1. Register the marketplace and enable the plugin in `~/.claude/settings.json`
   (a local checkout uses a `directory` source):

   ```json
   "extraKnownMarketplaces": {
     "loop-hooks": {
       "source": {"source": "directory", "path": "/path/to/loop-hooks"}
     }
   },
   "enabledPlugins": {
     "loop-hooks@loop-hooks": true
   }
   ```

2. Drop a `.loop-hooks.json` in the root of each repository you want gated.

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
| `gate.timeout_sec` | no | `600` | Integer ≥ 1. On timeout the whole process group is killed, so no orphaned test runners. |
| `gate.watch` | no | see above | Paths that make the gate fire. |
| `gate.ignore` | no | see above | Wins over `watch`. |

Patterns are `fnmatch` against repository-relative paths. Note that **`*` crosses
`/`**: `docs/*` also matches `docs/a/b.md`.

If the file is present but invalid (bad JSON, missing or empty `gate.command`,
wrong types), the gate stays disabled and the Stop hook emits a `systemMessage`
explaining why rather than blocking the turn.

## State

`.loop/state.json`, inside the gated repository, surviving across sessions:

```json
{"verified": "9f2c…"}
```

The fingerprint recorded the last time the gate passed. Delete the file to force
the gate to run on the next turn. Add `.loop/` to your `.gitignore`.

## Evidence (a convention, not a feature)

`.loop/evidence.jsonl` is where a verify runner is expected to append one line
per run. **This plugin neither writes nor reads it** — it is documented here so
that a runner and a gate agree on a shape:

```json
{"ts":"2026-08-19T12:34:56.789Z","rev":"64db08b+dirty","stage":"quick","pass":false,"checks":[{"name":"typecheck","ok":true,"ms":4120},{"name":"unit","ok":false,"ms":9876}]}
```

## Manual smoke test

```bash
cd /tmp && rm -rf loop-smoke && mkdir loop-smoke && cd loop-smoke
git init -q && git commit -q --allow-empty -m init
echo '{"gate": {"command": "true", "watch": ["*.ts"]}}' > .loop-hooks.json
echo 'export const a = 1' > a.ts

echo '{"cwd":"'$PWD'","stop_hook_active":false}' | uv run ~/loop-hooks/hooks/stop/gate.py
cat .loop/state.json    # {"verified": "…"}  gate ran and passed

echo '{"cwd":"'$PWD'","stop_hook_active":false}' | uv run ~/loop-hooks/hooks/stop/gate.py
                        # no output: nothing changed, gate skipped
```

## Tests

```bash
uv run pytest -v
```

## Limitations

- Requires a git repository.
- Covers the main agent's `Stop` only. `SubagentStop` and `TeammateIdle` are not
  gated yet.
- One recorded fingerprint per repository, so concurrent sessions in the same
  worktree share it.

## License

MIT — see [LICENSE](LICENSE).
