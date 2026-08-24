"""per-repo設定 .loop-hooks.json の読取と検証。"""
import json
from pathlib import Path

CONFIG_NAME = ".loop-hooks.json"
GATE_DEFAULTS = {
    "timeout_sec": 600,
    "watch": ["*.ts", "*.tsx", "package.json", "*tsconfig*.json"],
    "ignore": [".loop/*", "node_modules/*", "*.md"],
}


def load(root: str | None) -> dict | None:
    """設定を返す。ファイルが無い repo は None(=このrepoではゲート無効)。
    ファイルはあるが読めない・不正なら {"_error": 理由}(Stop側が警告を出す)。"""
    if not root:
        return None
    path = Path(root) / CONFIG_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_error": f"cannot read {CONFIG_NAME}: {exc}"}
    gate = raw.get("gate") if isinstance(raw, dict) else None
    if not isinstance(gate, dict) or not isinstance(gate.get("command"), str):
        return {"_error": f"{CONFIG_NAME} has no gate.command (string)"}
    if not gate["command"].strip():
        return {"_error": f"{CONFIG_NAME}: gate.command must not be empty"}
    merged = dict(GATE_DEFAULTS)
    merged.update(gate)

    timeout_sec = merged.get("timeout_sec")
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, int) or timeout_sec < 1:
        return {"_error": f"{CONFIG_NAME}: gate.timeout_sec must be an integer >= 1"}

    for key in ("watch", "ignore"):
        value = merged.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return {"_error": f"{CONFIG_NAME}: gate.{key} must be a list of strings"}

    return {"gate": merged}
