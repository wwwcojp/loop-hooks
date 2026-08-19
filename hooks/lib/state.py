"""repoごとのdirty状態。.loop/state.json に置く(セッションを跨いで残る)。"""
import json
from pathlib import Path


def _path(cwd: str) -> Path:
    return Path(cwd) / ".loop" / "state.json"


def is_dirty(cwd: str) -> bool:
    try:
        return bool(json.loads(_path(cwd).read_text(encoding="utf-8")).get("dirty"))
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return False


def set_dirty(cwd: str, dirty: bool) -> None:
    p = _path(cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"dirty": dirty}), encoding="utf-8")
