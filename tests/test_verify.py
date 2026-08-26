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


def test_shell_lineは単純コマンドをそのまま返す():
    assert verify.shell_line(verify.Check("x", ["uv", "run", "pytest", "-q"])) == "uv run pytest -q"


def test_shell_lineはcwdとenvを前置する():
    c = verify.Check("x", ["uv", "run", "lint-imports"], cwd="hooks", env=(("PYTHONPATH", "."),))
    assert verify.shell_line(c) == "cd hooks && PYTHONPATH=. uv run lint-imports"


def test_shell_lineは空白を含む引数をクォートする():
    c = verify.Check("x", ["git", "grep", "-nP", "a b", "--"])
    assert verify.shell_line(c) == "git grep -nP 'a b' --"


def test_shell_lineは空白を含むenv値をクォートする():
    c = verify.Check("x", ["echo", "hi"], env=(("FOO", "a b"),))
    assert verify.shell_line(c) == "FOO='a b' echo hi"


def test_run_stageはcwdとenvを反映する(tmp_path):
    (tmp_path / "sub").mkdir()
    c = verify.Check(
        "x",
        ["sh", "-c", 'test "$(pwd)" = "$EXPECT" && test "$FLAG" = on'],
        cwd="sub",
        env=(("FLAG", "on"), ("EXPECT", str((tmp_path / "sub").resolve()))),
    )
    assert verify.run_stage("quick", [c], repo_root=tmp_path) is True


def test_quick_stage_mirrors_ci():
    """spec §3.5: quick は CI の test ジョブと同じコマンド・同じ順序。

    ランナー側の Check から CI に書くべき 1 行(shell_line)を生成し、ci.yml の test ジョブの
    run ステップと 1 対 1・順序も一致させる。leak だけは CI 側が if ブロックなので部分一致。
    どちらか片方だけ変えると落ちる(両方向)。
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    quick = verify.STAGES["quick"]
    assert quick[0].name == "leak" and quick[0].ok_codes == frozenset({1})
    run_steps = _extract_ci_run_steps(ci)
    assert len(run_steps) == len(quick), f"CI の run ステップ数が quick と違う: {run_steps!r}"
    leak_step = run_steps[0]
    assert verify.shell_line(quick[0]) in leak_step
    assert re.search(r"\bif\b.*\bexit 1\b", leak_step, re.S), leak_step
    for check, step in zip(quick[1:], run_steps[1:]):
        assert step.strip() == verify.shell_line(check), check.name


def test_quickにformatチェックがある():
    names = [c.name for c in verify.STAGES["quick"]]
    assert names.index("format") == names.index("lint") + 1
    fmt = next(c for c in verify.STAGES["quick"] if c.name == "format")
    assert fmt.cmd == ["uv", "run", "ruff", "format", "--check", "hooks", "tests", "scripts"]


def test_全チェックが成功すればTrue(tmp_path):
    checks = [verify.Check("a", ["true"]), verify.Check("b", ["true"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is True


def test_最初の失敗で打ち切りFalse(tmp_path, capsys):
    checks = [
        verify.Check("a", ["true"]),
        verify.Check("b", ["false"]),
        verify.Check("c", ["sh", "-c", "echo SHOULD_NOT_RUN"]),
    ]
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


def test_タイムアウトしたチェックは失敗扱い(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "CHECK_TIMEOUT_SEC", 1)
    checks = [verify.Check("slow", ["sleep", "5"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is False


def test_quickにimport契約チェックがある():
    names = [c.name for c in verify.STAGES["quick"]]
    assert names.index("imports") == names.index("format") + 1
    c = next(c for c in verify.STAGES["quick"] if c.name == "imports")
    assert verify.shell_line(c) == (
        "cd hooks && PYTHONPATH=. uv run lint-imports --config ../pyproject.toml"
    )
