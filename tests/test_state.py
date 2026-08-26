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
