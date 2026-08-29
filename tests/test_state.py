"""state: 「最後にゲートを通った時点のフィンガープリント」をリポジトリの外に持つ。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import state  # noqa: E402

REPO = "/somewhere/my-repo"


def test_初期状態では検証済みの記録が無い():
    assert state.read_verified(REPO) is None


def test_書いた値がそのまま読める():
    state.write_verified(REPO, "abc123")
    assert state.read_verified(REPO) == "abc123"


def test_上書きすると新しい値になる():
    state.write_verified(REPO, "old")
    state.write_verified(REPO, "new")
    assert state.read_verified(REPO) == "new"


def test_リポジトリごとに別の記録になる():
    state.write_verified("/a/one", "fp-one")
    state.write_verified("/b/two", "fp-two")
    assert state.read_verified("/a/one") == "fp-one"
    assert state.read_verified("/b/two") == "fp-two"


def test_リポジトリ内には何も書かない(tmp_path):
    state.write_verified(str(tmp_path), "abc123")
    assert list(tmp_path.iterdir()) == []


def test_CLAUDE_PLUGIN_DATA配下に置かれる(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    state.write_verified(REPO, "abc123")
    assert list(tmp_path.rglob("*.json")), "CLAUDE_PLUGIN_DATA 配下にファイルが無い"


def test_CLAUDE_PLUGIN_DATAが無ければXDGキャッシュを使う(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # 実ホームのプラグイン置き場を見ない
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    state.write_verified(REPO, "abc123")
    assert list((tmp_path / "loop-hooks").rglob("*.json"))
    assert state.read_verified(REPO) == "abc123"


def test_記録には元のリポジトリパスが残る(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    state.write_verified(REPO, "abc123")
    written = json.loads(next(tmp_path.rglob("*.json")).read_text(encoding="utf-8"))
    assert written["root"] == REPO


def test_壊れた記録はNone(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    state.write_verified(REPO, "abc123")
    next(tmp_path.rglob("*.json")).write_text("{broken", encoding="utf-8")
    assert state.read_verified(REPO) is None


def test_verifiedが文字列でなければNone(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    state.write_verified(REPO, "abc123")
    next(tmp_path.rglob("*.json")).write_text('{"verified": 42}', encoding="utf-8")
    assert state.read_verified(REPO) is None


# --- ブロック記録(0.9.0: エージェント単位のスコープ) ---


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"hook_event_name": "Stop", "cwd": "/x"}, "manual"),
        ({"hook_event_name": "Stop", "cwd": "/x", "session_id": 123}, "manual"),
        ({"hook_event_name": "Stop", "cwd": "/x", "session_id": "s1"}, "s1"),
        ({"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1"}, "s1/a1"),
        ({"hook_event_name": "SubagentStop", "session_id": "s1"}, "s1"),
        ({"hook_event_name": "TeammateIdle", "session_id": "s1", "teammate_name": "w"}, "s1/w"),
        ({"hook_event_name": "TeammateIdle", "session_id": "s1"}, "s1"),
        ({"hook_event_name": "Stop", "session_id": "s1", "agent_id": "a1"}, "s1"),
    ],
)
def test_scopeはイベントの識別子から決まる(event, expected):
    assert state.scope(event) == expected


def test_初期状態ではブロック記録が無い():
    assert state.read_blocked(REPO, "s1") is None
    assert state.read_blocked_scopes(REPO, "fp-bad") == 0


def test_ブロック記録はスコープごとに独立():
    state.write_blocked(REPO, "s1/a", "fp-bad")
    assert state.read_blocked(REPO, "s1/a") == "fp-bad"
    assert state.read_blocked(REPO, "s1/b") is None
    state.write_blocked(REPO, "s1/b", "fp-bad")
    assert state.read_blocked_scopes(REPO, "fp-bad") == 2
    assert state.read_blocked_scopes(REPO, "other") == 0


def test_clear_blockedで全スコープが消える():
    state.write_blocked(REPO, "s1", "fp-bad")
    state.write_blocked(REPO, "s2", "fp-bad")
    state.clear_blocked(REPO)
    assert state.read_blocked(REPO, "s1") is None
    assert state.read_blocked(REPO, "s2") is None
    assert state.read_blocked_scopes(REPO, "fp-bad") == 0


def test_検証済みとブロックは共存する():
    state.write_verified(REPO, "fp-good")
    state.write_blocked(REPO, "s1", "fp-bad")
    assert state.read_verified(REPO) == "fp-good"
    assert state.read_blocked(REPO, "s1") == "fp-bad"


def test_上限を超えると最古のスコープが落ちる():
    for i in range(state.BLOCKED_MAX_SCOPES + 1):
        state.write_blocked(REPO, f"s{i}", "fp")
    assert state.read_blocked(REPO, "s0") is None
    assert state.read_blocked(REPO, "s1") == "fp"
    assert state.read_blocked(REPO, f"s{state.BLOCKED_MAX_SCOPES}") == "fp"
    assert state.read_blocked_scopes(REPO, "fp") == state.BLOCKED_MAX_SCOPES


def test_同じスコープの再書込は最新扱いになる():
    for i in range(state.BLOCKED_MAX_SCOPES):
        state.write_blocked(REPO, f"s{i}", "fp")
    state.write_blocked(REPO, "s0", "fp2")  # s0 を末尾へ
    state.write_blocked(REPO, "new", "fp")  # 65 件目: 落ちるのは s1
    assert state.read_blocked(REPO, "s0") == "fp2"
    assert state.read_blocked(REPO, "s1") is None


def test_旧形式の文字列blockedは未ブロック扱い():
    p = state._path(REPO)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"root": REPO, "verified": "v", "blocked": "fp-bad"}), encoding="utf-8")
    assert state.read_blocked(REPO, "s1") is None
    assert state.read_blocked_scopes(REPO, "fp-bad") == 0
    state.write_blocked(REPO, "s1", "fp-bad")  # 次の書込で dict に置き換わる
    assert json.loads(p.read_text(encoding="utf-8"))["blocked"] == {"s1": "fp-bad"}
    assert state.read_verified(REPO) == "v"


def test_書込は原子的で一時ファイルを残さない():
    state.write_blocked(REPO, "s1", "fp")
    state.write_verified(REPO, "v")
    files = sorted(p.name for p in state._path(REPO).parent.iterdir())
    assert files == [state._path(REPO).name]


def test_書込に失敗しても一時ファイルを残さない(monkeypatch):
    state.write_blocked(REPO, "s1", "fp")  # 置き場を作る

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(state.os, "replace", boom)
    state.write_blocked(REPO, "s2", "fp")  # 握られて例外は出ない
    files = sorted(p.name for p in state._path(REPO).parent.iterdir())
    assert files == [state._path(REPO).name]
    assert state.read_blocked(REPO, "s2") is None  # 書けていない


def test_書き込めなくても例外を出さない(tmp_path, monkeypatch):
    """0.3.1: lib は例外を外に出さない。状態が書けない環境でもゲートは動く。"""
    blocked_dir = tmp_path / "file-not-dir"
    blocked_dir.write_text("x", encoding="utf-8")  # ファイルなのでその下にディレクトリを作れない
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(blocked_dir))
    state.write_verified("/home/USER/repo", "abc")  # 例外にならない
    assert state.read_verified("/home/USER/repo") is None


def _plugin_data(home: Path, name: str) -> Path:
    d = home / ".claude" / "plugins" / "data" / name / "state"
    d.mkdir(parents=True)
    return d


def test_CLAUDE_PLUGIN_DATAが無ければプラグインのデータ置き場を探す(monkeypatch, tmp_path):
    """0.3.2: ターミナルからの --status はフックと同じ記録を読む。"""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    found = _plugin_data(tmp_path, "loop-hooks-loop-hooks")
    assert state.state_dir() == found


def test_プラグインのデータ置き場はCLAUDE_CONFIG_DIRを優先する(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = tmp_path / "cfg"
    d = cfg / "plugins" / "data" / "loop-hooks-loop-hooks" / "state"
    d.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    assert state.state_dir() == d


def test_データ置き場が複数あれば新しい方を使う(monkeypatch, tmp_path):
    import os

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    old = _plugin_data(tmp_path, "loop-hooks-other")
    new = _plugin_data(tmp_path, "loop-hooks-loop-hooks")
    os.utime(old, (1, 1))
    assert state.state_dir() == new


def test_データ置き場が無ければXDGキャッシュに戻る(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert state.state_dir() == tmp_path / "xdg" / "loop-hooks" / "state"


def test_ホームが解決できなくても例外を出さない(monkeypatch, tmp_path):
    """lib は例外を外に出さない: HOME 無し・passwd 無しの環境で Path.home() は RuntimeError。"""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def boom():
        raise RuntimeError("no home")

    monkeypatch.setattr(state.Path, "home", staticmethod(boom))
    assert state.state_dir() == tmp_path / "loop-hooks" / "state"
