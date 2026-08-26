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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import config, fingerprint, hook_io, log, state  # noqa: E402

# Claude Code はフック出力を 10,000 字で切る。その内側で、失敗の原因(トレースバック等は
# 末尾より前に出る)と結果の要約(末尾)の両方が残るように、先頭と末尾を残して中を落とす。
OUTPUT_HEAD_CHARS = 2500
OUTPUT_TAIL_CHARS = 5500
KILL_GRACE_SEC = 5

# hook_event_name → .loop-hooks.json の gate.on で使うキー
EVENT_KEYS = {"Stop": "stop", "SubagentStop": "subagent_stop", "TeammateIdle": "teammate_idle"}
FEEDBACK = "[loop-hooks] verification gate failed. Fix it before finishing:\n"
WARN = "[loop-hooks] gate failed again; letting this turn end unverified:\n"


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
    return False, f"$ {cmd}\n{_excerpt(out)}"


def _excerpt(out: str) -> str:
    limit = OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS
    if len(out) <= limit:
        return out
    dropped = len(out) - limit
    return (out[:OUTPUT_HEAD_CHARS] + f"\n... [{dropped} characters truncated] ...\n"
            + out[-OUTPUT_TAIL_CHARS:])


def _refuse(hook_event: str, root: str, current: str | None,
            detail: str, event: dict) -> dict:
    """検証に失敗したときの応答。イベントごとに形式が違う。

    同じフィンガープリントは2度ブロックしない。フィンガープリントが同じなら
    エージェントは何も直していないので、再ブロックしても同じ失敗を繰り返すだけ
    になる。この規則は stop_hook_active に依存しないため、そのフラグが伝播しない
    状況や、TeammateIdle のようにフラグ自体が無いイベントでも閉じ込めない。
    """
    if event.get("stop_hook_active"):
        return {"systemMessage": WARN + detail}
    if current is not None and current == state.read_blocked(root):
        return {"systemMessage": WARN + detail}
    if current is not None:
        state.write_blocked(root, current)
    if hook_event == "TeammateIdle":
        # teammate は JSON では止められない(continue:false は teammate 自体を終わらせる)
        return {"_exit_code": 2, "_stderr": FEEDBACK + detail}
    return {"hookSpecificOutput": {"hookEventName": hook_event,
                                   "additionalContext": FEEDBACK + detail}}


def handle(event: dict) -> dict | None:
    hook_event = event.get("hook_event_name") or "Stop"
    key = EVENT_KEYS.get(hook_event)
    if key is None:
        return None

    cwd = event.get("cwd") or ""
    root = fingerprint.repo_root(cwd)
    cfg = config.load(root or cwd)
    if cfg is None:
        return None  # 設定なし: このリポジトリでは何もしない(記録もしない)
    rec = {"event": hook_event}
    if "_error" in cfg:
        log.append(root or cwd, {**rec, "decision": "disabled", "note": cfg["_error"][:80]})
        return {"systemMessage": config.DISABLED_PREFIX + cfg["_error"]}
    if root is None:
        log.append(cwd, {**rec, "decision": "disabled", "note": "not a git repository"})
        return {"systemMessage": config.DISABLED_PREFIX
                                 + config.NOT_GIT_MESSAGE.format(cwd=cwd)}

    gate_cfg = cfg["gate"]
    if key not in gate_cfg["on"]:
        log.append(root, {**rec, "decision": "off"})
        return None
    current = fingerprint.compute(root, gate_cfg)
    rec["fp"] = (current or "")[:12]
    if current is None:
        # git が観測できない。skipped に倒すと無言でゲートが消えるので、走らせる側に倒す
        rec["note"] = "fingerprint unavailable"
    elif current == state.read_verified(root):
        log.append(root, {**rec, "decision": "skipped"})
        return None  # 前回グリーンから何も変わっていない

    started = time.monotonic()
    ok, detail = run_gate(gate_cfg["command"], root, gate_cfg["timeout_sec"])
    rec.update(decision="ran", ms=int((time.monotonic() - started) * 1000))
    if ok:
        # 検証コマンド自身が書き換えた分も含めて記録する(フォーマッタ等での再実行を防ぐ)
        verified = fingerprint.compute(root, gate_cfg)
        if verified is not None:
            state.write_verified(root, verified)
        state.write_blocked(root, "")  # 直ったのでブロック記録を無効化
        out = {}
        rec["result"] = "pass"
    else:
        out = _refuse(hook_event, root, current, detail, event)
        rec["result"] = "warn" if "systemMessage" in out else "fail"
    if cfg.get("_notice"):
        rec["note"] = "; ".join(filter(None, [rec.get("note"), cfg["_notice"][:80]]))
    log.append(root, rec)
    return _with_notice(out, root, cfg.get("_notice"))


def _with_notice(out: dict, root: str, notice: str | None) -> dict | None:
    """設定に関する通知を、ゲートが実際に走った回に一度だけ添える。"""
    if notice and notice != state.read_noticed(root):
        state.write_noticed(root, notice)
        prefix = f"[loop-hooks] {notice}"
        existing = out.get("systemMessage")
        out["systemMessage"] = prefix + ("\n" + existing if existing else "")
    return out or None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        from lib import status  # 表示専用。ゲート経路では読み込まない(item 3)
        # 表示ツールであって判定ツールではない。stdin は読まず、常に exit 0。
        target = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
        try:
            print(status.render(status.collect(target)))
        except Exception as exc:  # 表示ツールであって判定ツールではない
            print(f"loop-hooks status unavailable: {exc}")
        sys.exit(0)
    out = handle(hook_io.read_event()) or {}
    exit_code = out.pop("_exit_code", 0)
    stderr = out.pop("_stderr", "")
    if stderr:
        sys.stderr.write(stderr + "\n")
    if out:
        hook_io.emit(out)
    sys.exit(exit_code)
