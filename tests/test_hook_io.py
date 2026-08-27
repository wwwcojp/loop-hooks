"""hook_io: stdin の JSON を読み、stdout に JSON を書く。subprocess を通さず直接呼ぶ(mutmut 用)。"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import hook_io  # noqa: E402


def test_read_eventはdictをそのまま返す(monkeypatch):
    event = '{"hook_event_name": "Stop", "cwd": "/home/USER/r"}'
    monkeypatch.setattr(sys, "stdin", io.StringIO(event))
    assert hook_io.read_event() == {"hook_event_name": "Stop", "cwd": "/home/USER/r"}


def test_read_eventはdict以外なら空dict(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2]"))
    assert hook_io.read_event() == {}


def test_read_eventは壊れたJSONなら空dict(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert hook_io.read_event() == {}


def test_read_eventは空入力なら空dict(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert hook_io.read_event() == {}


def test_emitは1行のJSONを改行つきで書く(capsys):
    hook_io.emit({"systemMessage": "ゲート"})
    out = capsys.readouterr().out
    assert out == '{"systemMessage": "ゲート"}\n'
    assert json.loads(out) == {"systemMessage": "ゲート"}
