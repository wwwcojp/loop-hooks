"""state: 「最後にゲートを通った時点のフィンガープリント」をリポジトリの外に持つ。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import state  # noqa: E402

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


# --- TeammateIdle 用の「一度だけブロックする」ガード ---


def test_初期状態ではブロック記録が無い():
    assert state.read_blocked(REPO) is None


def test_ブロック記録は書いて読める():
    state.write_blocked(REPO, "fp-bad")
    assert state.read_blocked(REPO) == "fp-bad"


def test_検証済みとブロックは共存する():
    state.write_verified(REPO, "fp-good")
    state.write_blocked(REPO, "fp-bad")
    assert state.read_verified(REPO) == "fp-good"
    assert state.read_blocked(REPO) == "fp-bad"


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
