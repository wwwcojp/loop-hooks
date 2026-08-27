#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""セッション開始時のゲート告知。

設定を検証し、ゲートが有効ならエージェントに事実として伝え、人間には1行で示す。
無効ならなぜかを人間に示す。検証コマンドは実行しない。

フック定義はセッション開始時のスナップショットなので、プラグインの入口が動くと
ゲートは無言で消えることがある。「gate active」の1行が出ないセッションはその
状態だと分かる — それがこの入口の存在理由。
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import config, fingerprint, hook_io, log  # noqa: E402


def announcement(gate_cfg: dict[str, Any]) -> str:
    """エージェント向けの告知。命令形ではなく事実文で書く(プロンプトインジェクション
    防御に引っかからないための公式の指針)。"""
    return (
        "loop-hooks is active in this repository. When a turn ends and a watched file "
        f"has changed since the gate last passed, `{gate_cfg['command']}` runs from the "
        "repository root; if it fails, its output is returned and the turn stays open "
        f"until it passes. Events: {', '.join(gate_cfg['on'])}. "
        f"Watched: {', '.join(gate_cfg['watch'])}. Ignored: {', '.join(gate_cfg['ignore'])}. "
        f"Configuration is read from the committed {config.CONFIG_NAME}."
    )


def handle(event: dict[str, Any]) -> dict[str, Any] | None:
    cwd = event.get("cwd") or ""
    root = fingerprint.repo_root(cwd)
    cfg = config.load(root or cwd)
    if cfg is None:
        return None  # オプトインしていないリポジトリでは何も言わない
    rec = {"event": "SessionStart", "source": event.get("source") or ""}
    if "_error" in cfg:
        log.append(root or cwd, {**rec, "decision": "disabled", "note": cfg["_error"][:80]})
        return {"systemMessage": config.DISABLED_PREFIX + cfg["_error"]}
    if root is None:
        log.append(cwd, {**rec, "decision": "disabled", "note": "not a git repository"})
        return {"systemMessage": config.DISABLED_PREFIX + config.NOT_GIT_MESSAGE.format(cwd=cwd)}
    gate_cfg = cfg["gate"]
    version = config.plugin_version()
    tag = f"[loop-hooks {version}]" if version else "[loop-hooks]"
    lines = [f"{tag} gate active: {gate_cfg['command']}"]
    if cfg.get("_notice"):
        lines.append(f"[loop-hooks] {cfg['_notice']}")
        rec["note"] = cfg["_notice"][:80]
    log.append(root, {**rec, "decision": "announced"})
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": announcement(gate_cfg),
        },
        "systemMessage": "\n".join(lines),
    }


if __name__ == "__main__":
    try:
        out = handle(hook_io.read_event())
    except Exception:  # 起動をブロックする事故を起こさない。告知は失っても実害が無い
        out = None
    if out:
        hook_io.emit(out)
    sys.exit(0)
