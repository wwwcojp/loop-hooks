"""検証ランナー。チェックを順に実行し、最初の失敗で止まる。

loop-hooks の Stop ゲートから `uv run python scripts/verify.py quick` として呼ばれる。
`quick` の中身は CI(.github/workflows/ci.yml)と同じコマンド・同じ順序に保つこと
(tests/test_verify.py::test_quick_stage_mirrors_ci が両方向で一致を検査する)。

`mutation` は mutmut を毎回フル実行し、ファイル別 score を `tests/mutation-baseline.json`
とラチェット比較する。`all` は quick 成功後に mutation。どちらも Stop ゲート・CI には
載せない(約 3 分)。

evidence は書かない。「走ったか・なぜ走らなかったか」はプラグイン側の判定ログ
(`/loop-hooks:status`)が持つ。ここは終了コードと出力だけを返す。
stdlib のみ。hooks/ は import しない(ゲート対象とゲート実行者を混ぜない)。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# CI の「実ホームパスのリークチェック」と同一。変えるときは ci.yml も変える
LEAK_REGEX = r"/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*"

REPO_ROOT = Path(__file__).resolve().parent.parent
FAIL_OUTPUT_TAIL = 4000
CHECK_TIMEOUT_SEC = 600

# mutmut の終了コード→状態(mutmut/__main__.py status_by_exit_code)のうち "killed" のもの。
# survived(0)・no tests(5/33)・timeout・suspicious はすべて「検出できていない」として数える
MUTATION_KILLED_CODES = frozenset({1, 3, -24})
MUTMUT_CMD = ["uv", "run", "mutmut", "run"]
BASELINE_REL = Path("tests") / "mutation-baseline.json"


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
            ["uv", "run", "lint-imports", "--config", "pyproject.toml"],
            env=(("PYTHONPATH", "."),),
        ),
        Check("types", ["uv", "run", "pyright"]),
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


def mutation_scores(repo_root: Path) -> dict[str, dict[str, Any]]:
    """mutants/ 配下の *.py.meta からファイル別 {score, killed, total} を集計する。

    キーはリポジトリ相対パス(例: hooks/lib/config.py)。変異が 0 のファイルは載せない。
    """
    mutants = repo_root / "mutants"
    scores: dict[str, dict[str, Any]] = {}
    for meta in sorted(mutants.rglob("*.py.meta")):
        codes = json.loads(meta.read_text(encoding="utf-8")).get("exit_code_by_key", {})
        if not codes:
            continue
        total = len(codes)
        killed = sum(1 for c in codes.values() if c in MUTATION_KILLED_CODES)
        rel = meta.relative_to(mutants).as_posix()[: -len(".meta")]
        scores[rel] = {"score": round(killed / total * 100, 1), "killed": killed, "total": total}
    return scores


def check_mutation_baseline(
    repo_root: Path, scores: dict[str, dict[str, Any]]
) -> tuple[bool, list[str]]:
    """ファイル別ラチェット。(ok, 問題の一覧) を返す。ok で変化があれば baseline を書き換える。

    - 下回ったファイル / baseline にあって結果に無いファイル → fail(全件列挙)
    - 新規ファイルは登録、上回った分だけ更新。変化が無ければファイルに触らない
    """
    path = repo_root / BASELINE_REL
    baseline: dict[str, float] = {}
    if path.exists():
        baseline = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    problems: list[str] = []
    for f, b in sorted(baseline.items()):
        if f not in scores:
            problems.append(
                f"{f}: baseline {b} にあるが今回の結果に無い(only_mutate から外れている?"
                " 対象の縮小は baseline を手で外す必要がある)"
            )
        elif scores[f]["score"] < b:
            problems.append(f"{f}: score {scores[f]['score']} < baseline {b}")
    if problems:
        return False, problems
    new = {f: max(s["score"], baseline.get(f, 0.0)) for f, s in scores.items()}
    if new != baseline:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"files": dict(sorted(new.items()))}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, []


def _run_mutmut(repo_root: Path) -> tuple[int, str]:
    # mutmut はソース関数のハッシュが変わらない限り *.py.meta の判定をキャッシュから再利用する。
    # テストだけを変えた場合に古い判定が残りラチェットを誤判定しうるので、毎回 mutants/ を消す。
    shutil.rmtree(repo_root / "mutants", ignore_errors=True)
    try:
        proc = subprocess.run(  # noqa: S603 -- argv は MUTMUT_CMD に固定
            MUTMUT_CMD,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=repo_root,
            check=False,
        )
    except OSError as exc:
        return 1, f"{MUTMUT_CMD[0]}: {exc}"
    return proc.returncode, proc.stdout + proc.stderr


def run_mutation(
    repo_root: Path = REPO_ROOT, runner: Callable[[Path], tuple[int, str]] | None = None
) -> bool:
    """mutmut を実行し、ファイル別 score を baseline とラチェット比較する。"""
    run = runner or _run_mutmut
    started = time.monotonic()
    code, output = run(repo_root)
    elapsed = time.monotonic() - started
    if code != 0:
        print(output[-FAIL_OUTPUT_TAIL:], file=sys.stderr)
        print(f"[verify] mutation: FAIL (mutmut exit {code})")
        return False
    scores = mutation_scores(repo_root)
    if not scores:
        print(
            "mutants/ に変異結果(*.py.meta)が無い。[tool.mutmut] の only_mutate を確認する",
            file=sys.stderr,
        )
        print("[verify] mutation: FAIL (no results)")
        return False
    for rel, s in sorted(scores.items()):
        print(f"  {rel:<28} {s['score']:>5}  ({s['killed']}/{s['total']} killed)")
    ok, problems = check_mutation_baseline(repo_root, scores)
    for p in problems:
        print(f"  ! {p}")
    print(f"[verify] mutation: {'ok' if ok else 'FAIL'} ({elapsed:.0f}s)")
    return ok


def main(argv: Sequence[str]) -> int:
    stages = [*STAGES, "mutation", "all"]
    if len(argv) != 1 or argv[0] not in stages:
        print(f"usage: verify.py {{{'|'.join(stages)}}}", file=sys.stderr)
        return 2
    if argv[0] == "mutation":
        return 0 if run_mutation() else 1
    if argv[0] == "all":
        return 0 if run_stage("quick") and run_mutation() else 1
    return 0 if run_stage(argv[0]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
