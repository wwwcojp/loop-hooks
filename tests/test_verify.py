"""scripts/verify.py: quick ステージは CI と同じコマンド・同じ順序。失敗で非ゼロ。"""
import re
from pathlib import Path

import verify

REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract_ci_run_steps(ci_yaml: str) -> list[str]:
    """ci.yml の `run:` 本文を出現順に取り出す(YAML パーサを足さないための最小実装)。

    `run: |` のブロックはインデントが戻るまで、`run: cmd` は 1 行。
    """
    lines = ci_yaml.splitlines()
    steps: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, rest = len(m.group(1)), m.group(2)
        if rest.strip() != "|":
            steps.append(rest)
            i += 1
            continue
        body = []
        i += 1
        while i < len(lines) and (
            not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent
        ):
            body.append(lines[i])
            i += 1
        steps.append("\n".join(body))
    return steps


def test_quick_stage_mirrors_ci():
    """spec §2.1: quick は CI と同じコマンド・同じ順序。片方を変えたらもう片方も変える。

    両方向で検査する: ランナー側のコマンドを固定し、CI の run ステップを全数抽出して
    1 対 1・順序も一致させる。CI にステップが増えても、フラグが変わっても落ちる。
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert [c.name for c in verify.STAGES["quick"]] == ["leak", "lint", "tests"]
    leak, lint, tests = verify.STAGES["quick"]
    assert leak.cmd[:3] == ["git", "grep", "-nP"] and leak.ok_codes == frozenset({1})
    assert lint.cmd == ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]
    assert tests.cmd == ["uv", "run", "pytest", "-q"]

    run_steps = _extract_ci_run_steps(ci)
    assert len(run_steps) == 3, f"CI の run ステップ数が想定と違う: {run_steps!r}"
    leak_step, lint_step, tests_step = run_steps
    assert f"git grep -nP '{verify.LEAK_REGEX}' --" in leak_step
    assert re.search(r"\bif\b.*\bexit 1\b", leak_step, re.S), leak_step
    assert lint_step.strip() == "uv run ruff check hooks tests scripts"
    assert tests_step.strip() == "uv run pytest -q"


def test_全チェックが成功すればTrue(tmp_path):
    checks = [verify.Check("a", ["true"]), verify.Check("b", ["true"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is True


def test_最初の失敗で打ち切りFalse(tmp_path, capsys):
    checks = [verify.Check("a", ["true"]), verify.Check("b", ["false"]),
              verify.Check("c", ["sh", "-c", "echo SHOULD_NOT_RUN"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is False
    assert "SHOULD_NOT_RUN" not in capsys.readouterr().out


def test_ok_codesで成功扱いの終了コードを反転できる(tmp_path):
    checks = [verify.Check("grep-no-match", ["false"], ok_codes=frozenset({1}))]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is True


def test_失敗したチェックの出力を表示する(tmp_path, capsys):
    checks = [verify.Check("boom", ["sh", "-c", "echo DETAIL; exit 3"])]
    verify.run_stage("quick", checks, repo_root=tmp_path)
    out = capsys.readouterr().out
    assert "DETAIL" in out and "boom" in out


def test_実行できないコマンドは失敗扱い(tmp_path):
    checks = [verify.Check("missing", ["/nonexistent/command"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is False


def test_mainは未知のステージで2を返す():
    assert verify.main(["nope"]) == 2


def test_mainは成功で0失敗で1(monkeypatch):
    monkeypatch.setitem(verify.STAGES, "ok", [verify.Check("a", ["true"])])
    monkeypatch.setitem(verify.STAGES, "ng", [verify.Check("a", ["false"])])
    assert verify.main(["ok"]) == 0
    assert verify.main(["ng"]) == 1
