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
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hooks import gate  # noqa: E402
from hooks.lib import config, fingerprint, log, state, status  # noqa: E402

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
    """自リポジトリ由来の import を (モジュール名, 取り込む名前, モジュール直下か) で返す。

    「モジュール直下」= 関数・クラス定義の外側。try / if / with で包まれていても直下とみなす
    (import 時に実行されるため)。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nested: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested.update(id(child) for child in ast.walk(node) if child is not node)
    found: list[tuple[str, list[str], bool]] = []
    for node in ast.walk(tree):
        top = id(node) not in nested
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_own(alias.name):
                    found.append((alias.name, [], top))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0 or _is_own(module):
                found.append((module, [a.name for a in node.names], top))
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


def test_own_importsはtryやifの中のimportもモジュール直下として扱う(tmp_path):
    """関数・クラスの外側はすべて「モジュール直下」。try / if / with で包んでも規則を逃れない。"""
    src = "\n".join(
        [
            "import sys",
            "try:",
            "    from hooks.lib import status",
            "except ImportError:",
            "    pass",
            "if sys.version_info >= (3, 10):",
            "    import hooks.lib.log",
            "def f():",
            "    from hooks.lib import config",
            "class C:",
            "    from hooks.lib import state",
        ]
    )
    p = tmp_path / "entry.py"
    p.write_text(src, encoding="utf-8")
    found = {(module, tuple(names), top) for module, names, top in _own_imports(p)}
    assert ("hooks.lib", ("status",), True) in found
    assert ("hooks.lib.log", (), True) in found
    assert ("hooks.lib", ("config",), False) in found
    assert ("hooks.lib", ("state",), False) in found


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


def test_判定式_未検証で指紋も取れなければ両方とも走る側に倒す(tmp_path, monkeypatch):
    root = _repo(tmp_path)  # verified は無い
    monkeypatch.setattr(fingerprint, "compute", lambda root, gate_cfg: None)
    info = status.collect(root)
    assert info["fingerprint"] is None and info["verified"] is None
    assert info["will_run"] is True
    gate.handle(_stop(root))
    assert _last(root)["decision"] == "ran"


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


def test_判定式_指紋が取れないときのblockedはgateの固定キーと同じ(tmp_path, monkeypatch):
    root = _repo(tmp_path, command="false")
    monkeypatch.setattr(fingerprint, "compute", lambda root, gate_cfg: None)
    gate.handle(_stop(root))  # 失敗、"fp-unavailable" でブロック記録
    assert state.read_blocked(root) == state.FP_UNAVAILABLE_KEY
    assert status.collect(root)["blocked"] is True
    out = gate.handle(_stop(root))
    assert out and "systemMessage" in out  # gate も再ブロックしない


def test_gateの指紋不能キーはstateの定数を参照しリテラルを持たない():
    """入口は lib を import できるが lib は入口を import できない。
    定数は state が持ち、gate は参照する。"""
    tree = ast.parse((ROOT / "hooks" / "gate.py").read_text(encoding="utf-8"))
    literals = {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert state.FP_UNAVAILABLE_KEY not in literals
    refs = {
        (n.value.id, n.attr)
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }
    assert ("state", "FP_UNAVAILABLE_KEY") in refs
    assert not hasattr(status, "FP_UNAVAILABLE_KEY")


# ---- (c) 状態・ログの書込先はリポジトリ外の既定領域から出ない ----


def _files(root: Path) -> set[str]:
    return {os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs}


def _root_variants(tmp_path: Path) -> list[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sub").mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)
    return [str(repo), str(repo) + "/", str(repo / "sub" / ".."), str(link)]


def test_書込先はどんなroot表現でもstate_dir配下(tmp_path):
    base = state.state_dir()
    for root in _root_variants(tmp_path):
        assert state._path(root).is_relative_to(base), root
        assert log._path(root).is_relative_to(base), root


def test_同じリポジトリの別表現は同じキーに解決される(tmp_path):
    variants = _root_variants(tmp_path)
    keys = {state.key(v) for v in variants}
    assert len(keys) == 1, dict(zip(variants, map(state.key, variants)))


def test_書込でリポジトリ内にファイルが増えない(tmp_path, tmp_path_factory):
    root = Path(_repo(tmp_path))
    (root / "sub").mkdir()
    link = tmp_path_factory.mktemp("link-holder") / "link"
    link.symlink_to(root, target_is_directory=True)
    before = _files(root)
    for v in (str(root), str(root) + "/", str(root / "sub" / ".."), str(link)):
        state.write_verified(v, "fp")
        state.write_blocked(v, "fp")
        state.write_noticed(v, "n")
        log.append(v, {"event": "Stop", "decision": "skipped"})
    assert _files(root) == before
    assert state.read_verified(str(root)) == "fp"


# ---- (d) hooks/lib の公開関数は例外を外に出さない ----
# 壊れた入力(任意 JSON / 任意バイト列)は tests/test_properties.py の P1 / P4 / P6b が担う。

INFO_KEYS = {
    "cwd", "root", "config_source", "config_error", "notice", "command", "on", "watch",
    "ignore", "timeout_sec", "fingerprint", "verified", "will_run", "blocked", "recent",
    "state_dir", "summary",
}  # fmt: skip


@pytest.fixture
def unwritable_state(tmp_path, monkeypatch):
    """CLAUDE_PLUGIN_DATA を書込不能にする。root など制限が効かない環境では skip。"""
    data = tmp_path / "ro"
    data.mkdir()
    data.chmod(0o500)
    if os.access(data, os.W_OK):
        data.chmod(0o700)
        pytest.skip("書込制限が効かない環境(root など)")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    yield data
    data.chmod(0o700)


def test_書込不能でもstateとlogは例外を出さない(tmp_path, unwritable_state):
    (tmp_path / "r").mkdir()
    root = _repo(tmp_path / "r")
    state.write_verified(root, "fp")
    state.write_blocked(root, "fp")
    state.write_noticed(root, "n")
    log.append(root, {"event": "Stop", "decision": "ran"})
    assert state.read_verified(root) is None
    assert state.read_blocked(root) is None
    assert state.read_noticed(root) is None
    assert log.tail(root, 5) == []


def test_状態ファイルとログがディレクトリでも例外を出さない(tmp_path):
    root = str(tmp_path)
    state._path(root).mkdir(parents=True)
    log._path(root).mkdir(parents=True)
    state.write_verified(root, "fp")
    log.append(root, {"event": "Stop"})
    assert state.read_verified(root) is None
    assert log.tail(root, 5) == []


@pytest.fixture
def no_git(tmp_path, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def test_gitが無くてもfingerprintとconfigは例外を出さずNone側に倒す(tmp_path, no_git):
    root = str(tmp_path)
    assert fingerprint.repo_root(root) is None
    assert fingerprint.compute(root, GATE) is None
    assert fingerprint.head_file(root, ".loop-hooks.json") is None
    assert config.load(root) is None  # 設定ファイルも無い
    (tmp_path / ".loop-hooks.json").write_text(json.dumps({"gate": GATE}), encoding="utf-8")
    loaded = config.load(root)
    assert loaded is not None and "_error" not in loaded  # 作業ツリー版で読める


def test_存在しないディレクトリでも例外を出さない(tmp_path):
    missing = str(tmp_path / "missing")
    assert fingerprint.repo_root(missing) is None
    assert config.load(missing) is None
    assert set(status.collect(missing)) == INFO_KEYS


def test_plugin_jsonが壊れていてもplugin_versionはNone(tmp_path, monkeypatch):
    broken = tmp_path / "plugin.json"
    for body in ("{", "[]", '{"version": 3}', '{"version": ""}'):
        broken.write_text(body, encoding="utf-8")
        monkeypatch.setattr(config, "PLUGIN_JSON", broken)
        assert config.plugin_version() is None, body
    monkeypatch.setattr(config, "PLUGIN_JSON", tmp_path / "none.json")
    assert config.plugin_version() is None


def test_status_collectはどの状況でも全キーを返す(tmp_path, unwritable_state, no_git):
    for cwd in (str(tmp_path), str(tmp_path / "missing"), ""):
        assert set(status.collect(cwd)) == INFO_KEYS, cwd
