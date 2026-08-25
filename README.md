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

## Where it fits

*Loop engineering* — the term [Addy Osmani](https://addyosmani.com/blog/loop-engineering/)
made the long-form case for, [Gergely Orosz](https://newsletter.pragmaticengineer.com/p/what-is-loop-engineering)
surveyed, and Boris Cherny summed up as "my job now is to write loops" — names the
shift from prompting an agent to designing the system that prompts it: triggers,
schedules, worktrees, `/goal`, subagents, memory. Most of that is orchestration,
and this plugin does none of it.

It does one narrow thing inside that picture. Every loop has to answer the
question Sonar's [write-up](https://www.sonarsource.com/blog/loop-engineering-without-verification-is-just-automation/)
puts at the centre: *who, or what, is allowed to say the loop is finished?* If the
answer is the model that did the work, or a second model asked to review it, you
have two optimists agreeing, and the failure mode is the premature-completion loop:
"done" declared on work that isn't. What that write-up and Osmani both land on is
a **deterministic tier** — in Osmani's phrase, a check that can fail the work, not
a verifier with an opinion. loop-hooks is that tier for Claude Code, in its
smallest form: a command your repository already has, run at the moment the agent
tries to stop, with the result handed back as the next instruction instead of as
a report.

Three design choices follow from taking that role seriously:

- **Orientation and enforcement are different layers.** `CLAUDE.md` tells the
  agent what good looks like; it can be forgotten, summarised away, or reasoned
  around. A hook can't. Keep policy in `CLAUDE.md`; put what must not be skipped
  in a hook.
- **The plugin doesn't know what "verified" means.** It knows how to run a
  command and refuse to end the turn. What the command checks — tests, types,
  lint, static analysis, a contract suite — is the repository's decision, in
  `.loop-hooks.json`. That is why one plugin serves a TypeScript monorepo and a
  Python CLI alike, and why a gate can get stricter without the plugin changing.
- **Per turn, not per commit.** A pre-commit hook fires only if the agent
  commits. CI fires minutes later, outside the loop, after the context that
  produced the bug is gone. A Stop hook fires while the failure is still the
  agent's problem. Change detection is what makes per-turn affordable.

## What it does

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
6. It never traps an agent. On re-entry (`stop_hook_active`) a second failure
   becomes a `systemMessage` warning and the turn ends. `TeammateIdle` carries no
   re-entry flag, so instead the gate refuses to block the same fingerprint twice
   in a row. Either way the fingerprint stays unrecorded, so the gate fires again
   on the next change.

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
| `gate.on` | no | all three | Which events to gate: `stop`, `subagent_stop`, `teammate_idle`. |
| `gate.timeout_sec` | no | `600` | Integer ≥ 1. On timeout the whole process group is killed, so no orphaned test runners. |
| `gate.watch` | no | see above | Paths that make the gate fire. |
| `gate.ignore` | no | see above | Wins over `watch`. |

Patterns are `fnmatch` against repository-relative paths. Note that **`*` crosses
`/`**: `docs/*` also matches `docs/a/b.md`.

If the file is present but invalid (bad JSON, missing or empty `gate.command`,
wrong types), the gate stays disabled and the Stop hook emits a `systemMessage`
explaining why rather than blocking the turn.

## Pairings

The gate is only as good as the command behind it. These are combinations that
have held up in daily use.

### TDD: enforce green, delegate red

A Stop gate turns "make sure the tests pass before you finish" from an
instruction into a contract — the GREEN step of the cycle, enforced. It says
nothing about RED. For that, pair it with a `PreToolUse` guard such as
[tdd-guard](https://github.com/nizos/tdd-guard), which refuses implementation
edits that have no failing test. One hook keeps the agent from writing code it
can't justify; the other keeps it from stopping with code that doesn't work.

```json
{"gate": {"command": "npm test -- --run", "watch": ["src/**", "test/**"]}}
```

### Mirror CI

Make `gate.command` run exactly what CI runs, in the same order, and add a test
that asserts the two lists are identical. "If it passes locally it passes CI"
stops being a hope and becomes a checked property. Keep CI on the raw commands
rather than calling the runner, so the equality test has two independent
sources to compare.

```json
{"gate": {"command": "uv run ruff check . && uv run pytest -q"}}
```

### Tier the checks

Anything on the Stop path is paid on every turn that changed a watched file, so
keep that tier fast and push the slow tiers behind an explicit stage. A verify
runner with `quick` / `mutation` / `all` stages is the shape that works: `quick`
is lint plus unit tests (about one second for 240 tests in one Python repo,
measured), `all` is what you run before you call a task done.

```json
{"gate": {"command": "uv run python scripts/verify.py quick", "timeout_sec": 120}}
```

### Mutation testing with a ratchet

The gate proves the tests pass. It says nothing about whether the tests mean
anything — an agent can turn a suite green by weakening an assertion. Mutation
testing closes that gap: run [mutmut](https://mutmut.readthedocs.io/) (or
Stryker, or cargo-mutants) as the slow tier, commit a per-file score baseline,
and fail the stage if any file drops below it. Scores only go up. In one
security-guard codebase this took 11 seconds for 803 mutants, and the survivors
pointed at real untested boundaries, not noise. Too slow for every turn; right
for "done".

### Formatters and generators inside the gate

Putting `ruff format`, `prettier --write`, or a code generator in the gate is
safe here, because the fingerprint recorded on success is taken *after* the
command ran. The rewritten files are part of the verified state, so the next
turn doesn't fire the gate again on the formatter's own changes.

```json
{"gate": {"command": "ruff format . && ruff check . && pytest -q"}}
```

### Multi-agent: no unverified hand-offs

With `on` left at its default, a subagent can't return and a teammate can't go
idle while the gate is red, so the main agent never receives work that hasn't
been checked. Duplicate gates are free: if the subagent's run left the tree
verified, the main agent's Stop finds a matching fingerprint and runs nothing.

### Layer with pre-action guards

A `PreToolUse` deny/ask guard and a Stop gate answer different questions. The
guard stops an action before it happens: a destructive command, a write to a
secret, an outbound request carrying a token. The gate checks the outcome
before the turn ends. Neither covers the other's ground, both are deterministic,
and both are hooks — run them together.

## What it is not

- **Not CI.** CI is still the final judge. This shortens the loop so that most
  of what CI would catch is caught while the agent can still fix it.
- **Not a judge of test quality.** A green gate means the command exited 0. See
  the mutation pairing above.
- **Not an LLM reviewer.** It is the deterministic tier. For intent and
  semantics — "is this what was asked?" — pair it with `/goal` or a reviewer
  subagent, and let this gate be the one that can't be talked out of a verdict.
- **Not a cage.** It fails open on purpose: a second failure on re-entry becomes
  a warning, Claude Code caps consecutive blocks at eight, and `TeammateIdle`
  is never blocked twice on the same state. What it never does is forget — the
  fingerprint stays unrecorded, so the gate is back on the next change.

## State

**loop-hooks never writes inside your repository**, so there is nothing to add to
`.gitignore`. State lives in the plugin's persistent data directory, or under the
XDG cache when that isn't set (running the script by hand, for instance):

```
$CLAUDE_PLUGIN_DATA/state/<sha16-of-repo-path>.json
~/.cache/loop-hooks/state/<sha16-of-repo-path>.json
```

```json
{"root": "/home/alice/my-project", "verified": "9f2c…", "blocked": ""}
```

`verified` is the fingerprint recorded the last time the gate passed; `blocked` is
the re-entry guard for `TeammateIdle`. Delete the file to force the gate to run on
the next turn.

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

## Limitations

- Requires a git repository.
- One recorded fingerprint per repository, so concurrent sessions in the same
  worktree share it.
- `hookSpecificOutput.additionalContext` on `Stop` needs a recent Claude Code; on
  older versions the feedback is ignored and the turn ends unverified.

## License

MIT — see [LICENSE](LICENSE).
