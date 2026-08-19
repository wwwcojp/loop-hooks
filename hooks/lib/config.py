"""per-repo設定 .loop-hooks.json の読取と検証。"""
import json
from pathlib import Path

CONFIG_NAME = ".loop-hooks.json"
GATE_DEFAULTS = {
    "timeout_sec": 600,
    "watch": ["*.ts", "*.tsx", "package.json", "tsconfig*.json"],
    "ignore": [".loop/*", "node_modules/*", "*.md"],
}


def load(cwd: str | None) -> dict | None:
    """設定を返す。ファイルが無い repo は None(=このrepoではゲート無効)。
    ファイルはあるが読めない・不正なら {"_error": 理由}(Stop側が警告を出す)。"""
    if not cwd:
        return None
    path = Path(cwd) / CONFIG_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_error": f"{CONFIG_NAME} が読めない: {exc}"}
    gate = raw.get("gate") if isinstance(raw, dict) else None
    if not isinstance(gate, dict) or not isinstance(gate.get("command"), str):
        return {"_error": f"{CONFIG_NAME} に gate.command (文字列) が無い"}
    merged = dict(GATE_DEFAULTS)
    merged.update(gate)
    return {"gate": merged}
