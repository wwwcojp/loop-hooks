"""state: .loop/state.json に「最後にゲートを通った時点のフィンガープリント」を持つ。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import state  # noqa: E402


def test_初期状態では検証済みの記録が無い(tmp_path):
    assert state.read_verified(str(tmp_path)) is None


def test_書いた値がそのまま読める(tmp_path):
    state.write_verified(str(tmp_path), "abc123")
    assert state.read_verified(str(tmp_path)) == "abc123"


def test_上書きすると新しい値になる(tmp_path):
    state.write_verified(str(tmp_path), "old")
    state.write_verified(str(tmp_path), "new")
    assert state.read_verified(str(tmp_path)) == "new"


def test_state_jsonが壊れていてもNone(tmp_path):
    p = tmp_path / ".loop" / "state.json"
    p.parent.mkdir(parents=True)
    p.write_text("{broken", encoding="utf-8")
    assert state.read_verified(str(tmp_path)) is None


def test_verifiedが文字列でなければNone(tmp_path):
    p = tmp_path / ".loop" / "state.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"verified": 42}', encoding="utf-8")
    assert state.read_verified(str(tmp_path)) is None
