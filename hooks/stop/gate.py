#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""ターン終了時のゲート。dirtyなら検証コマンドを実行し、失敗ならターンを終わらせない。

再入(stop_hook_active)で再び失敗した場合は、閉じ込めずに警告だけ出して通す。
dirty は残すので、次のターンの終了時に再びゲートが掛かる。"""
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, hook_io, state  # noqa: E402

OUTPUT_TAIL_CHARS = 2000


def run_gate(cmd: str, cwd: str, timeout: int) -> tuple[bool, str]:
    try:
        argv = shlex.split(cmd)
        argv[0] = os.path.expanduser(argv[0])
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (ValueError, IndexError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"$ {cmd}\n実行できませんでした: {exc}"
    if r.returncode == 0:
        return True, ""
    return False, f"$ {cmd}\n{(r.stdout + r.stderr)[-OUTPUT_TAIL_CHARS:]}"


def handle(event: dict) -> dict | None:
    cwd = event.get("cwd") or ""
    cfg = config.load(cwd)
    if cfg is None:
        return None
    if "_error" in cfg:
        return {"systemMessage": f"[loop-hooks] 設定が読めないためゲート無効: {cfg['_error']}"}
    if not state.is_dirty(cwd):
        return None
    gate_cfg = cfg["gate"]
    ok, detail = run_gate(gate_cfg["command"], cwd, gate_cfg["timeout_sec"])
    if ok:
        state.set_dirty(cwd, False)
        return None
    if event.get("stop_hook_active"):
        return {"systemMessage": "[loop-hooks] ゲートが連続で失敗。今回は通すが未検証のまま:\n" + detail}
    return {"decision": "block",
            "reason": "[loop-hooks] 検証ゲートが失敗した。修復してから終了すること:\n" + detail}


if __name__ == "__main__":
    out = handle(hook_io.read_event())
    if out:
        hook_io.emit(out)
