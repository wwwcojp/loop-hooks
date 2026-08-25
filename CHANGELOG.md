# Changelog

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
