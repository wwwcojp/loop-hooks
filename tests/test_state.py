"""state: .loop/state.json の読み書き。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import state  # noqa: E402


def test_初期状態はdirtyでない(tmp_path):
    assert state.is_dirty(str(tmp_path)) is False


def test_set_dirtyで立ててis_dirtyで読める(tmp_path):
    state.set_dirty(str(tmp_path), True)
    assert state.is_dirty(str(tmp_path)) is True
    state.set_dirty(str(tmp_path), False)
    assert state.is_dirty(str(tmp_path)) is False


def test_state_jsonが壊れていてもFalse(tmp_path):
    p = tmp_path / ".loop" / "state.json"
    p.parent.mkdir(parents=True)
    p.write_text("{broken", encoding="utf-8")
    assert state.is_dirty(str(tmp_path)) is False
