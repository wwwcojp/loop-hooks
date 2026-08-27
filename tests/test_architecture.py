"""アーキテクチャ規則(第 5 段階 spec §2.1)。

構造と境界を固定する例示テスト。関数の正しさは各 test_*.py、任意入力に対する性質は
tests/test_properties.py(P1: config._validate、P4: log.tail、P6b: state の壊れたファイル)が担う。

既存テストが担う不変条件(移動しない):
- 作業ツリーの .loop-hooks.json は HEAD より優先されない
  → tests/test_gate.py::test_作業ツリーでcommandを緩めてもHEADの設定でブロックされる
- timeout 時にプロセスグループが残らない
  → tests/test_gate.py::test_タイムアウトで孫プロセスも止まる
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hooks import gate  # noqa: E402
from hooks.lib import fingerprint, log, status  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENTRIES = ("gate.py", "session_start.py")
GATE = {
    "command": "true",
    "on": ["stop", "subagent_stop", "teammate_idle"],
    "watch": ["*.ts"],
    "ignore": ["*.md"],
    "timeout_sec": 10,
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=cwd, capture_output=True, check=True)  # noqa: S607


def _repo(tmp_path: Path, command: str = "true") -> str:
    """設定をコミット済みで、watch 対象に未検証の変更がある git リポジトリ。ルートを返す。"""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    cfg = {"gate": {**GATE, "command": command}}
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(cfg), encoding="utf-8")
    _git(tmp_path, "add", ".loop-hooks.json")
    _git(tmp_path, "commit", "-qm", "config")
    (tmp_path / "main.ts").write_text("source\n", encoding="utf-8")
    return str(tmp_path)


# ---- (a) 入口ファイルの import 規則 ----


def _own_imports(path: Path) -> list[tuple[str, list[str], bool]]:
    """自リポジトリ由来の import を (モジュール名, 取り込む名前, モジュール直下か) で返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = set(map(id, tree.body))
    found: list[tuple[str, list[str], bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_own(alias.name):
                    found.append((alias.name, [], id(node) in top_level))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0 or _is_own(module):
                found.append((module, [a.name for a in node.names], id(node) in top_level))
    return found


def _is_own(module: str) -> bool:
    return module in ("hooks", "lib") or module.startswith(("hooks.", "lib."))


def test_入口は自リポジトリからhooks_lib配下しかimportしない():
    """入口は薄い層。lib 以外の自モジュール(入口同士、旧 `lib` 起点)に依存しない。"""
    for name in ENTRIES:
        for module, names, _ in _own_imports(ROOT / "hooks" / name):
            assert module == "hooks.lib" or module.startswith("hooks.lib."), (name, module, names)


def test_入口はモジュール直下でstatusをimportしない():
    """表示専用モジュールはゲート経路で読まない(0.3.0 spec §2)。gate.py は status_main 内だけ。"""
    for name in ENTRIES:
        for module, names, top in _own_imports(ROOT / "hooks" / name):
            if top:
                assert "status" not in names and module != "hooks.lib.status", (name, module)


def test_入口はプラグインルートをsys_pathに入れてからimportする():
    """`from hooks.lib import …` の前提。無いと mutmut の変異キーや CLAUDE.md 6 項と食い違う。"""
    for name in ENTRIES:
        src = (ROOT / "hooks" / name).read_text(encoding="utf-8")
        assert "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))" in src, name


# ---- (e) 既存テストが担う不変条件の参照が実在する ----


def test_既存テストが担う不変条件の参照先が実在する():
    src = (ROOT / "tests" / "test_gate.py").read_text(encoding="utf-8")
    assert "def test_作業ツリーでcommandを緩めてもHEADの設定でブロックされる(" in src
    assert "def test_タイムアウトで孫プロセスも止まる(" in src


# ---- (b) gate.handle と status.collect の判定式は同じ(0.3.0 の裁定) ----


def _stop(root: str) -> dict:
    return {"hook_event_name": "Stop", "cwd": root, "stop_hook_active": False}


def _last(root: str) -> dict:
    return log.tail(root, 1)[0]


def test_判定式_未検証ならwill_runでgateはran(tmp_path):
    root = _repo(tmp_path)
    info = status.collect(root)
    assert info["will_run"] is True and info["blocked"] is False
    gate.handle(_stop(root))
    assert _last(root)["decision"] == "ran"


def test_判定式_検証済みならwill_runでなくgateはskipped(tmp_path):
    root = _repo(tmp_path)
    gate.handle(_stop(root))  # 通す
    info = status.collect(root)
    assert info["will_run"] is False
    gate.handle(_stop(root))
    assert _last(root)["decision"] == "skipped"


def test_判定式_指紋が取れなければ両方とも走る側に倒す(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    gate.handle(_stop(root))  # verified を残す
    monkeypatch.setattr(fingerprint, "compute", lambda root, gate_cfg: None)
    info = status.collect(root)
    assert info["fingerprint"] is None and info["will_run"] is True
    gate.handle(_stop(root))
    rec = _last(root)
    assert rec["decision"] == "ran" and rec["note"] == "fingerprint unavailable"


def test_判定式_blockedは現在の指紋と一致するときだけで再ブロックしない(tmp_path):
    root = _repo(tmp_path, command="false")
    assert status.collect(root)["blocked"] is False
    out = gate.handle(_stop(root))
    assert out and "hookSpecificOutput" in out and _last(root)["result"] == "fail"
    assert status.collect(root)["blocked"] is True
    out = gate.handle(_stop(root))
    assert out and "systemMessage" in out and _last(root)["result"] == "warn"
    (tmp_path / "main.ts").write_text("changed\n", encoding="utf-8")
    assert status.collect(root)["blocked"] is False  # 状態が変わればまたブロックする
