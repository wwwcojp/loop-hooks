"""検証ランナー。チェックを順に実行し、最初の失敗で止まる。

loop-hooks の Stop ゲートから `uv run python scripts/verify.py quick` として呼ばれる。
`quick` の中身は CI(.github/workflows/ci.yml)と同じコマンド・同じ順序に保つこと
(tests/test_verify.py::test_quick_stage_mirrors_ci が両方向で一致を検査する)。

evidence は書かない。「走ったか・なぜ走らなかったか」はプラグイン側の判定ログ
(`/loop-hooks:status`)が持つ。ここは終了コードと出力だけを返す。
stdlib のみ。hooks/ は import しない(ゲート対象とゲート実行者を混ぜない)。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# CI の「実ホームパスのリークチェック」と同一。変えるときは ci.yml も変える
LEAK_REGEX = r"/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*"

REPO_ROOT = Path(__file__).resolve().parent.parent
FAIL_OUTPUT_TAIL = 4000
CHECK_TIMEOUT_SEC = 600


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    # 終了コードがこの集合に含まれれば成功。git grep は「不一致=1」が成功なので反転に使う
    ok_codes: frozenset[int] = frozenset({0})
    cwd: str = "."  # repo_root からの相対
    env: tuple[tuple[str, str], ...] = ()  # 追加の環境変数(frozen なので tuple)


def shell_line(check: Check) -> str:
    """CI の `run:` に書くべき 1 行。tests/test_verify.py がこれと ci.yml を突き合わせる。"""
    prefix = f"cd {check.cwd} && " if check.cwd != "." else ""
    env = "".join(f"{k}={shlex.quote(v)} " for k, v in check.env)
    return prefix + env + shlex.join(check.cmd)


STAGES: dict[str, list[Check]] = {
    "quick": [
        Check("leak", ["git", "grep", "-nP", LEAK_REGEX, "--"], ok_codes=frozenset({1})),
        Check("lint", ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]),
        Check("format", ["uv", "run", "ruff", "format", "--check", "hooks", "tests", "scripts"]),
        Check(
            "imports",
            ["uv", "run", "lint-imports", "--config", "../pyproject.toml"],
            cwd="hooks",
            env=(("PYTHONPATH", "."),),
        ),
        Check("tests", ["uv", "run", "pytest", "-q"]),
    ],
}


def _run(check: Check, repo_root: Path) -> tuple[bool, str]:
    env = {**os.environ, **dict(check.env)} if check.env else None
    try:
        r = subprocess.run(  # noqa: S603 -- argv は STAGES に固定。ユーザー入力なし
            check.cmd,
            cwd=repo_root / check.cwd,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SEC,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {CHECK_TIMEOUT_SEC}s"
    except OSError as exc:
        return False, f"could not run: {exc}"
    return r.returncode in check.ok_codes, (r.stdout or "") + (r.stderr or "")


def run_stage(
    stage: str, checks: Sequence[Check] | None = None, repo_root: Path = REPO_ROOT
) -> bool:
    """チェックを順に実行し、最初の失敗で打ち切る。成否を返す。"""
    checks = STAGES[stage] if checks is None else checks
    for check in checks:
        ok, out = _run(check, repo_root)
        if ok:
            print(f"[verify] {check.name}: ok")
            continue
        print(f"[verify] {check.name}: FAIL")
        print(f"$ {' '.join(check.cmd)}")
        print(out[-FAIL_OUTPUT_TAIL:])
        return False
    return True


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1 or argv[0] not in STAGES:
        print(f"usage: verify.py {{{'|'.join(STAGES)}}}", file=sys.stderr)
        return 2
    return 0 if run_stage(argv[0]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
