#!/usr/bin/env python3
"""Verify runner template for loop-hooks.

Copy this file to `scripts/verify.py` in your repository, edit the STAGES table
below, and point `.loop-hooks.json` at it:

    {"gate": {"command": "uv run python scripts/verify.py quick"}}

Usage:
    uv run python scripts/verify.py quick        # run one stage
    uv run python scripts/verify.py all          # run every stage in definition order
    uv run python scripts/verify.py --print-ci   # print the `run:` line for each check

Output is one line per check: `[verify] <name>: ok` or `[verify] <name>: FAIL (...)`
followed by the tail of the command output. The runner stops at the first failure
and exits 1; exits 0 when every check passes; exits 2 for an unknown stage.
loop-hooks records the first FAIL line as the failure reason in `--status`.

In a project without uv, drop the `uv run` prefix and make sure the tools are on PATH.

Standard library only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # this file lives in <repo>/scripts/
CHECK_TIMEOUT_SEC = 600
FAIL_OUTPUT_TAIL = 4000


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]  # argv, run from REPO_ROOT without a shell
    ok_codes: frozenset[int] = frozenset({0})  # exit codes that count as success


# --- STAGES BEGIN ---
# Edit this table. Keep `quick` under ~30 seconds: it runs at every turn end.
# Move anything slower (mutation testing, end-to-end suites) to `slow`.
#
# Other stacks:
#   node:  Check("lint", ["bun", "run", "lint"]), Check("tests", ["bun", "test"])
#   rust:  Check("fmt", ["cargo", "fmt", "--check"]), Check("tests", ["cargo", "test", "-q"])
#   go:    Check("vet", ["go", "vet", "./..."]), Check("tests", ["go", "test", "./..."])
STAGES: dict[str, list[Check]] = {
    "quick": [
        Check("lint", ["ruff", "check", "."]),
        Check("format", ["ruff", "format", "--check", "."]),
        Check("tests", ["pytest", "-q"]),
    ],
    # e.g. Check("mutation", ["mutmut", "run"]) — see scripts/verify.py in the loop-hooks
    # repository for a per-file score ratchet.
    "slow": [],
}
# --- STAGES END ---


def shell_line(check: Check) -> str:
    """The line to put under `run:` in CI so CI and the gate stay identical."""
    return shlex.join(check.cmd)


def run_check(check: Check) -> tuple[bool, str]:
    if not check.cmd:
        return False, "FAIL (empty command)"
    try:
        r = subprocess.run(  # noqa: S603 -- argv is fixed in STAGES
            check.cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False, f"FAIL (timeout after {CHECK_TIMEOUT_SEC}s)"
    except FileNotFoundError:
        return False, f"FAIL (command not found: {check.cmd[0]})"
    except OSError as exc:
        return False, f"FAIL (could not run: {exc})"
    if r.returncode in check.ok_codes:
        return True, "ok"
    output = (r.stdout or "") + (r.stderr or "")
    return False, f"FAIL (exit {r.returncode})\n{output[-FAIL_OUTPUT_TAIL:]}"


def run_stage(checks: list[Check]) -> bool:
    for check in checks:
        ok, detail = run_check(check)
        print(f"[verify] {check.name}: {detail}", flush=True)
        if not ok:
            return False
    return True


def stages_for(name: str) -> list[tuple[str, list[Check]]] | None:
    if name == "all":
        return list(STAGES.items())
    if name in STAGES:
        return [(name, STAGES[name])]
    return None


def main(argv: list[str]) -> int:
    known = ", ".join([*STAGES, "all"])
    parser = argparse.ArgumentParser(description=f"Run a verification stage ({known}).")
    parser.add_argument("stage", nargs="?", default=None, help=f"one of: {known} (default: quick)")
    parser.add_argument(
        "--print-ci", action="store_true", help="print the CI `run:` line for each check and exit"
    )
    args = parser.parse_args(argv)
    here = Path(__file__).resolve().parent
    if here.name != "scripts":  # REPO_ROOT is derived from this location; refuse to guess
        print(f"verify.py must live in <repo>/scripts/ (found: {here})", file=sys.stderr)
        return 2
    stage = args.stage if args.stage is not None else ("all" if args.print_ci else "quick")
    selected = stages_for(stage)
    if selected is None:
        print(f"unknown stage: {stage} (known: {known})", file=sys.stderr)
        return 2
    if args.print_ci:
        for _, checks in selected:
            for check in checks:
                print(shell_line(check))
        return 0
    for _, checks in selected:
        if not run_stage(checks):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
