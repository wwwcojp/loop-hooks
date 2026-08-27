# Changelog

## [0.7.0] - 2026-08-27

### Added
- **Architecture tests** (`tests/test_architecture.py`): entry files import only `hooks.lib`
  (and never `status` at module level); `gate.handle` and `status.collect` make the same
  decision for the same repository state; state and log files stay under the plugin data
  directory whatever form the repository path takes (trailing slash, `..`, symlink); every
  public function in `hooks/lib` degrades instead of raising when the data directory is not
  writable, `git` is missing, or paths do not exist.
- **Hook I/O contract goldens** (`tests/contracts/*.json`, `tests/test_contracts.py`): the
  exact input/output JSON, exit code and stderr for Stop / SubagentStop / TeammateIdle /
  SessionStart, normalised and compared as whole dictionaries. Each golden records the
  reference URL and the date it was checked.

### Fixed
- `--status` / `/loop-hooks:status` now agrees with the gate when the fingerprint cannot be
  computed: `will_run` is true (the gate always runs in that case) and `blocked` compares against
  the same fixed key the gate uses, instead of reporting "will not run" / "blocked: no". The
  `state` line of the status output names that reason ("fingerprint unavailable") instead of
  claiming a change.

### Upgrading
- Nothing to do. No entry-point files or hook definitions changed; no restart needed.

## [0.6.0] - 2026-08-27

### Added
- **Property-based tests** (`tests/test_properties.py`, hypothesis): configuration validation
  never raises on arbitrary JSON; `is_watched` honours `ignore` over `watch`; the fingerprint is
  deterministic, blind to unwatched files and sensitive to watched ones; the decision log tolerates
  arbitrary bytes and keeps its size bounded; state round-trips and degrades to `None` on corruption.
  25 examples in the gate and CI, 300 in `verify.py all` (`properties` stage), 5 while mutmut runs.
- `scripts/verify.py properties`; `all` is now quick → properties → mutation.

### Upgrading
- Nothing to do. No entry-point files or hook definitions changed; no restart needed.

## [0.5.0] - 2026-08-27

### Added
- **Mutation testing with a ratchet for the repository itself.** `uv run python
  scripts/verify.py mutation` runs mutmut over `hooks/lib` (about 920 mutants, about 65 seconds),
  scores each file, and fails when a file drops below `tests/mutation-baseline.json`;
  the runner raises the baseline itself when a score improves. `all` = `quick` + `mutation`.
  Neither is part of the end-of-turn gate or CI. Timing: about a minute in isolation, longer
  under load. The ratchet compares killed-mutant counts and tolerates a one-mutant dip;
  timeouts are not counted as kills; if a file's mutant total changes it is re-baselined
  instead of failing.
- Tests added by the first triage: `hook_io` is now called directly (it was only exercised
  through subprocesses), `fingerprint` pins the git timeout, `log` pins the UTC timestamp
  format and trim boundaries, `status.render` has golden output.
- `fingerprint._changed_paths` was rewritten as an iterator so no mutant can loop forever
  (mutmut previously ran into an 11 GB runaway).

### Changed
- **Imports are rooted at the plugin directory** (`from hooks.lib import …`) so that
  mutmut's mutant keys match runtime module names. The hook entry files did not move and
  `hooks.json` is unchanged. Existing sessions keep working; restart Claude Code to pick up
  0.5.0 as usual.

### Upgrading
- Nothing to do.

## [0.4.0] - 2026-08-27

### Added
- **More checks in the repository's own gate and CI**, all deterministic and fast:
  `ruff format --check`, ruff's `S` (bandit) rules with the three designed `subprocess`
  call sites accepted by line-level `noqa` and a stated reason, import-linter contracts
  for `hooks/lib` (layering, no import of entry points, `subprocess` confined to
  `fingerprint`), and pyright in strict mode. `quick` now runs six checks in about
  11.5 seconds (measured).
- **CI `security` job**: zizmor over the workflows and pip-audit over the exported lock
  file. Workflow actions are pinned to commit SHAs, `permissions: contents: read` is
  explicit, and Dependabot keeps the pins current.
- `scripts/verify.py`: `Check` gained `cwd` / `env`, and `shell_line()` is the single
  source for the CI mirror test.

### Changed
- Source reformatted with `ruff format` (no behaviour change).

### Upgrading
- Nothing to do. No entry-point files or hook definitions changed; no restart needed.

## [0.3.2] - 2026-08-27

### Fixed
- **`/loop-hooks:status` now reads the same records the hooks write.** The skill's
  inline command did not receive `CLAUDE_PLUGIN_DATA`, so it read `~/.cache/loop-hooks`
  and reported `(no runs recorded)`. The skill now passes the variable explicitly, and
  `state_dir()` falls back to `~/.claude/plugins/data/loop-hooks-*/` (honouring
  `CLAUDE_CONFIG_DIR`) before the XDG cache, so a terminal `gate.py --status` finds the
  hook's records too. `--status` prints the directory it read as a `records` line.
- **The session-start line and `--status` show the plugin version**
  (`[loop-hooks 0.3.2] gate active: …`, `loop-hooks status (0.3.2)`), so an outdated
  plugin is visible even when the repository's configuration is current.

### Upgrading
- No configuration changes. Restart Claude Code after updating so the new
  `SessionStart` output and skill take effect.

## [0.3.1] - 2026-08-26

### Added
- **The plugin now gates its own repository** (`.loop-hooks.json` → `uv run python
  scripts/verify.py quick`, the same three commands as CI: home-path leak check, ruff,
  pytest). `tests/test_verify.py` keeps the runner and `ci.yml` in lockstep.
  Dogfooding rules for contributors are in `CLAUDE.md`.

### Fixed
- **A git failure no longer silently disables the gate.** When the fingerprint cannot
  be computed, the gate runs the verification command instead of recording `skipped`
  (`None == None` matched an unverified repository). The decision log notes
  `fingerprint unavailable`, and a pass in that state does not record `verified`.
- **`/loop-hooks:status` always shows the latest `ran` decision**, even when the last
  five decisions are all `skipped`. It is appended as a sixth line when needed.
- **State writes never raise.** A state directory that cannot be created is ignored;
  the gate simply runs again next time.
- **Decision-log trimming is atomic** (write to a temp file, then `os.replace`), so a
  crash or a concurrent session cannot leave a half-written log.
- `--status` is covered by a test that injects an exception and checks it still exits 0.
- Dogfooding acceptance: the gate stopped a turn in this repository on a deliberately
  broken test (`ran fail`, 10.8 s) and let it end after the fix.

### Upgrading
- No configuration changes. No entry-point files moved, so a running session keeps
  working; restart Claude Code to pick up the fixes.

## [0.3.0] - 2026-08-26

### Added
- **`SessionStart` hook.** When a session starts (or resumes, or after `/clear` and
  compaction), loop-hooks validates `.loop-hooks.json` and, if the gate is active,
  tells the agent what runs and when, and prints one line for you:
  `[loop-hooks] gate active: <command>`. If that line is missing in a repository
  that has a configuration, the plugin is not loaded — restart Claude Code. Invalid
  configuration and non-git directories are reported at session start instead of at
  the first Stop. The verification command is never run here.
- **Decision log.** Every gate decision (`skipped`, `ran pass/fail/warn`, `off`,
  `disabled`, `announced`) is appended to `<state dir>/<key>.log.jsonl`, trimmed
  back to the last 1000 lines whenever it exceeds 1200. Nothing is written into
  your repository.
- **`/loop-hooks:status`** and **`uv run …/hooks/gate.py --status [path]`**: where
  the configuration was read from, what the gate runs, whether it will run at the
  next stop, and the last five decisions. Read-only.

### Changed (breaking)
- **`watch` now defaults to `["*"]`** (every file), and the default `ignore` covers
  `node_modules`, `.venv`, `dist`, `build`, `target`, `.claude`, `.loop` and `*.md`.
  Repositories that omitted `watch` were gated on TypeScript files only; a Python
  repository without an explicit `watch` was silently never gated. Repositories that
  set `watch` explicitly are unaffected.

### Upgrading
Restart running Claude Code sessions after updating. The first session after the
restart prints `[loop-hooks] gate active: …` in gated repositories, which confirms
the update took effect.

## [0.2.1] - 2026-08-25

### Fixed
- **The gate never blocks the same fingerprint twice, on any event.** If the working tree
  has not changed since the last block, the agent has not attempted a fix, so a second
  block would only repeat the same failure. The rule no longer depends on
  `stop_hook_active`, which covers the cases where that flag is missing (`TeammateIdle`)
  or fails to propagate (anthropics/claude-code#54360), and bounds the loop when the
  agent is legitimately waiting on a background task (#55754).
- **The committed `.loop-hooks.json` takes precedence.** In a git repository the gate reads
  the configuration from `HEAD`. Editing, breaking, or deleting the file in the working
  tree no longer changes or disables the gate; a `systemMessage` reports the divergence.
  An uncommitted configuration still works, with a one-time notice asking to commit it.
- **Hook timeout no longer cuts off the gate's own timeout.** `hooks.json` now allows
  3600 s and `gate.timeout_sec` is capped at 3000, so the process-group cleanup on
  timeout always runs. Previously a `timeout_sec` above 660 was killed by Claude Code
  first, leaving test runners orphaned.
- **Failure feedback keeps both the head and the tail of the output** (2500 + 5500
  characters) instead of the last 2000. Tracebacks and the first failing assertion
  usually appear before the summary, and were being cut off.

### Added
- `statusMessage` on every hook entry, so the spinner says what is running.

## [0.2.0] - 2026-08-25

First public release. See the
[release notes](https://github.com/wwwcojp/loop-hooks/releases/tag/v0.2.0).
