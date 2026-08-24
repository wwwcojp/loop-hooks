#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""ターン終了時のゲート。

前回ゲートを通った時点から watch 対象が変化していれば検証コマンドを実行し、
失敗ならターンを終わらせない。変化の判定は git による観測なので、Edit/Write
だけでなく Bash 経由の編集や git 操作、フォーマッタによる書き換えも捕捉する。

再入(stop_hook_active)で再び失敗した場合は、閉じ込めずに警告だけ出して通す。
検証済みの記録は更新しないので、次のターンの終了時に再びゲートが掛かる。
"""
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import config, fingerprint, hook_io, state  # noqa: E402

OUTPUT_TAIL_CHARS = 2000
KILL_GRACE_SEC = 5


def _kill_group(proc: subprocess.Popen) -> None:
    """シェルが起こした子孫ごと落とす。シェル自身だけ殺しても孫が残る。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.communicate(timeout=KILL_GRACE_SEC)
    except (subprocess.TimeoutExpired, OSError):
        proc.kill()


def run_gate(cmd: str, cwd: str, timeout: int) -> tuple[bool, str]:
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=cwd, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,  # 専用のプロセスグループを作り、まとめて殺せるようにする
        )
    except OSError as exc:
        return False, f"$ {cmd}\ncould not run: {exc}"
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return False, f"$ {cmd}\ntimed out after {timeout}s"
    if proc.returncode == 0:
        return True, ""
    return False, f"$ {cmd}\n{out[-OUTPUT_TAIL_CHARS:]}"


def handle(event: dict) -> dict | None:
    cwd = event.get("cwd") or ""
    root = fingerprint.repo_root(cwd)
    cfg = config.load(root or cwd)
    if cfg is None:
        return None
    if "_error" in cfg:
        return {"systemMessage": f"[loop-hooks] gate disabled: {cfg['_error']}"}
    if root is None:
        return {"systemMessage": "[loop-hooks] gate disabled: not a git repository "
                                 f"({cwd}). loop-hooks uses git to detect changes."}

    gate_cfg = cfg["gate"]
    if fingerprint.compute(root, gate_cfg) == state.read_verified(root):
        return None  # 前回グリーンから何も変わっていない

    ok, detail = run_gate(gate_cfg["command"], root, gate_cfg["timeout_sec"])
    if ok:
        # 検証コマンド自身が書き換えた分も含めて記録する(フォーマッタ等での再実行を防ぐ)
        verified = fingerprint.compute(root, gate_cfg)
        if verified is not None:
            state.write_verified(root, verified)
        return None
    if event.get("stop_hook_active"):
        return {"systemMessage": "[loop-hooks] gate failed again; letting this turn end "
                                 "unverified:\n" + detail}
    return {"decision": "block",
            "reason": "[loop-hooks] verification gate failed. Fix it before finishing:\n" + detail}


if __name__ == "__main__":
    out = handle(hook_io.read_event())
    if out:
        hook_io.emit(out)
