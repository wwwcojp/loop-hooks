"""mark_dirty: ゲート対象の編集だけを dirty として記録する。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks" / "post_tool_use"))
from lib import state  # noqa: E402
import mark_dirty  # noqa: E402

GATE = {"watch": ["*.ts", "package.json"], "ignore": [".loop/*", "*.md", "docs/*"]}


def make_event(tmp_path: Path, rel: str, tool: str = "Edit") -> dict:
    return {"tool_name": tool, "cwd": str(tmp_path),
            "tool_input": {"file_path": str(tmp_path / rel)}}


def write_config(tmp_path: Path) -> None:
    (tmp_path / ".loop-hooks.json").write_text(
        json.dumps({"gate": {"command": "echo ok", "watch": GATE["watch"], "ignore": GATE["ignore"]}}),
        encoding="utf-8")


def test_is_watched_watchに一致すれば対象():
    assert mark_dirty.is_watched("server/main.ts", GATE) is True
    assert mark_dirty.is_watched("package.json", GATE) is True


def test_is_watched_ignoreがwatchより優先():
    assert mark_dirty.is_watched("docs/notes.ts", GATE) is False
    assert mark_dirty.is_watched("README.md", GATE) is False


def test_is_watched_どちらにも無ければ対象外():
    assert mark_dirty.is_watched("scripts/dev.sh", GATE) is False


def test_対象ファイルの編集でdirtyが立つ(tmp_path):
    write_config(tmp_path)
    mark_dirty.handle(make_event(tmp_path, "server/main.ts"))
    assert state.is_dirty(str(tmp_path)) is True


def test_対象外ファイルの編集ではdirtyが立たない(tmp_path):
    write_config(tmp_path)
    mark_dirty.handle(make_event(tmp_path, "docs/notes.md"))
    assert state.is_dirty(str(tmp_path)) is False


def test_設定が無いrepoでは何もしない(tmp_path):
    mark_dirty.handle(make_event(tmp_path, "server/main.ts"))
    assert not (tmp_path / ".loop").exists()


def test_書き込みツール以外は無視する(tmp_path):
    write_config(tmp_path)
    mark_dirty.handle(make_event(tmp_path, "server/main.ts", tool="Read"))
    assert state.is_dirty(str(tmp_path)) is False


def test_repo外への書き込みは無視する(tmp_path):
    write_config(tmp_path)
    mark_dirty.handle({"tool_name": "Edit", "cwd": str(tmp_path),
                       "tool_input": {"file_path": "/etc/hosts.ts"}})
    assert state.is_dirty(str(tmp_path)) is False


def test_設定が壊れているrepoでは何もしない(tmp_path):
    (tmp_path / ".loop-hooks.json").write_text("{broken", encoding="utf-8")
    mark_dirty.handle(make_event(tmp_path, "server/main.ts"))
    assert not (tmp_path / ".loop").exists()
