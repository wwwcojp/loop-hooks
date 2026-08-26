# Changelog

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
