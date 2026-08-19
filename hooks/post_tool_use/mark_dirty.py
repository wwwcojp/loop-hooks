#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""ゲート対象ファイルの編集を dirty として記録する。検証はここでは走らせない(Stopで走る)。"""
import fnmatch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, state  # noqa: E402

WRITE_TOOLS = ("Edit", "Write")


def is_watched(rel: str, gate_cfg: dict) -> bool:
    if any(fnmatch.fnmatch(rel, p) for p in gate_cfg["ignore"]):
        return False
    return any(fnmatch.fnmatch(rel, p) for p in gate_cfg["watch"])


def handle(event: dict) -> None:
    if event.get("tool_name") not in WRITE_TOOLS:
        return
    cwd = event.get("cwd") or ""
    cfg = config.load(cwd)
    if cfg is None or "_error" in cfg:
        return
    file_path = (event.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        return
    try:
        rel = str(Path(file_path).resolve().relative_to(Path(cwd).resolve()))
    except ValueError:
        return  # repo外への書き込みはゲート対象外
    if is_watched(rel, cfg["gate"]):
        state.set_dirty(cwd, True)


if __name__ == "__main__":
    handle(hook_io.read_event())
