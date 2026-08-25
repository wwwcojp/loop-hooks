---
name: status
description: Show whether the loop-hooks verification gate is active in this repository, what it runs, and why it did or did not run recently
allowed-tools: Bash(uv run "${CLAUDE_PLUGIN_ROOT}/hooks/gate.py" *)
---

!`uv run "${CLAUDE_PLUGIN_ROOT}/hooks/gate.py" --status "${CLAUDE_PROJECT_DIR}"`

The block above is the current state of the loop-hooks gate for this repository.
Report it to the user as-is. This skill is read-only: it does not change
`.loop-hooks.json` or the gate.
