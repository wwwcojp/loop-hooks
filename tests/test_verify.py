"""scripts/verify.py: quick ステージは CI と同じコマンド・同じ順序。失敗で非ゼロ。"""

import json
import re
from pathlib import Path

import verify

REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract_ci_run_steps(ci_yaml: str, job: str = "test") -> list[str]:
    """ci.yml の指定ジョブの `run:` 本文を出現順に取り出す(YAML パーサを足さないための最小実装)。

    `jobs:` 配下で `  <job>:` から次の 2 スペースインデントのキーまでを対象にする。
    `run: |` のブロックはインデントが戻るまで、`run: cmd` は 1 行。
    """
    lines = ci_yaml.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.rstrip() == f"  {job}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^  \S", lines[i])), len(lines)
    )
    section = lines[start:end]
    steps: list[str] = []
    i = 0
    while i < len(section):
        m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", section[i])
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
        while i < len(section) and (
            not section[i].strip() or len(section[i]) - len(section[i].lstrip()) > indent
        ):
            body.append(section[i])
            i += 1
        steps.append("\n".join(body))
    return steps


def test_extractはジョブを絞る():
    ci = "jobs:\n  test:\n    steps:\n      - run: a\n  security:\n    steps:\n      - run: b\n"
    assert _extract_ci_run_steps(ci, "test") == ["a"]
    assert _extract_ci_run_steps(ci, "security") == ["b"]


def test_ciのsecurityジョブはzizmorとpip_auditを回す():
    """spec §3.5: quick には入れない検査。CI 側だけに存在する。"""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = _extract_ci_run_steps(ci, "security")
    assert any("zizmor" in s for s in steps) and any("pip-audit" in s for s in steps)


def test_ciのpip_auditステップはpipefailのためshell_bashを指定する():
    """最終レビュー: 既定の `bash -e` には pipefail が無く、uv export の失敗を握りつぶす。"""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    m = re.search(r"- name: 依存の脆弱性\(pip-audit\)\n(.*?)(?:\n      - |\Z)", ci, re.S)
    assert m and "shell: bash" in m.group(1), "pip-audit ステップに shell: bash が無い"


def test_ciのActionsはSHAでピン留めされている():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)", ci)
    assert uses, "uses が無い"
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u), f"SHA でピン留めされていない: {u}"


def test_ciはpermissionsを明示する():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read\n", ci, re.M)


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
    assert verify.shell_line(c) == "PYTHONPATH=. uv run lint-imports --config pyproject.toml"


def test_quickに型検査がある():
    names = [c.name for c in verify.STAGES["quick"]]
    assert names.index("types") == names.index("imports") + 1
    c = next(c for c in verify.STAGES["quick"] if c.name == "types")
    assert c.cmd == ["uv", "run", "pyright"]


def _write_meta(root: Path, rel: str, codes: dict[str, int]) -> None:
    meta = root / "mutants" / (rel + ".meta")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"exit_code_by_key": codes}), encoding="utf-8")


def test_mutation_scoresはmetaからファイル別に集計する(tmp_path):
    """spec §2.3: killed = 1/3/-24。survived(0)・no tests(5/33)・timeout は未検出扱い。"""
    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1, "k2": 3, "k3": -24, "s": 0, "n": 5, "t": 24})
    _write_meta(tmp_path, "hooks/lib/b.py", {})
    scores = verify.mutation_scores(tmp_path)
    assert scores == {"hooks/lib/a.py": {"score": 50.0, "killed": 3, "total": 6}}


def test_baselineが無ければ作られる(tmp_path):
    ok, problems = verify.check_mutation_baseline(
        tmp_path, {"hooks/lib/a.py": {"score": 80.0, "killed": 8, "total": 10}}
    )
    assert ok and problems == []
    data = json.loads((tmp_path / verify.BASELINE_REL).read_text(encoding="utf-8"))
    assert data["files"] == {"hooks/lib/a.py": 80.0}


def test_baselineを下回ればfailで一覧(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / verify.BASELINE_REL).write_text(
        json.dumps({"files": {"hooks/lib/a.py": 90.0}}), encoding="utf-8"
    )
    ok, problems = verify.check_mutation_baseline(
        tmp_path, {"hooks/lib/a.py": {"score": 80.0, "killed": 8, "total": 10}}
    )
    assert not ok and problems == ["hooks/lib/a.py: score 80.0 < baseline 90.0"]
    assert json.loads((tmp_path / verify.BASELINE_REL).read_text())["files"] == {
        "hooks/lib/a.py": 90.0
    }


def test_baselineを上回れば書き換える(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / verify.BASELINE_REL).write_text(
        json.dumps({"files": {"hooks/lib/a.py": 70.0}}), encoding="utf-8"
    )
    ok, _ = verify.check_mutation_baseline(
        tmp_path, {"hooks/lib/a.py": {"score": 80.0, "killed": 8, "total": 10}}
    )
    assert ok
    assert json.loads((tmp_path / verify.BASELINE_REL).read_text())["files"] == {
        "hooks/lib/a.py": 80.0
    }


def test_baselineにあって結果に無いファイルはfail(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / verify.BASELINE_REL).write_text(
        json.dumps({"files": {"hooks/lib/gone.py": 70.0}}), encoding="utf-8"
    )
    ok, problems = verify.check_mutation_baseline(tmp_path, {})
    assert not ok and problems
    assert problems[0].startswith("hooks/lib/gone.py: baseline 70.0 にあるが")


def test_run_mutationはmutmut失敗で偽(tmp_path, capsys):
    assert verify.run_mutation(tmp_path, runner=lambda root: (1, "boom")) is False
    assert "boom" in capsys.readouterr().err


def test_run_mutationは結果が無ければ偽(tmp_path, capsys):
    assert verify.run_mutation(tmp_path, runner=lambda root: (0, "")) is False
    assert "only_mutate" in capsys.readouterr().err


def test_run_mutationは表を出しbaselineを更新して真(tmp_path, capsys):
    def fake(root: Path) -> tuple[int, str]:
        _write_meta(root, "hooks/lib/a.py", {"k": 1, "s": 0})
        return 0, ""

    assert verify.run_mutation(tmp_path, runner=fake) is True
    out = capsys.readouterr().out
    assert "hooks/lib/a.py" in out and "50.0" in out
    assert (tmp_path / verify.BASELINE_REL).exists()


def test_mainはmutationとallを受け付ける(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        verify, "run_stage", lambda stage, checks=None, repo_root=None: calls.append(stage) or True
    )
    monkeypatch.setattr(verify, "run_mutation", lambda: calls.append("mutation") or True)
    assert verify.main(["mutation"]) == 0 and calls == ["mutation"]
    calls.clear()
    assert verify.main(["all"]) == 0 and calls == ["quick", "mutation"]


def test_mainのallはquickが落ちればmutationを回さない(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        verify, "run_stage", lambda stage, checks=None, repo_root=None: calls.append(stage) or False
    )
    monkeypatch.setattr(verify, "run_mutation", lambda: calls.append("mutation") or True)
    assert verify.main(["all"]) == 1 and calls == ["quick"]
