"""per-repo設定 .loop-hooks.json の読取と検証。"""

import json
from pathlib import Path

from . import fingerprint

# 入口(gate.py / session_start.py)が共有する利用者向け文言。ずれると人間向けと
# エージェント向けの表示が食い違うので一箇所に置く。
DISABLED_PREFIX = "[loop-hooks] gate disabled: "
NOT_GIT_MESSAGE = "not a git repository ({cwd}). loop-hooks uses git to detect changes."

CONFIG_NAME = ".loop-hooks.json"
# プラグイン自身のバージョン。Claude Code は環境変数で渡さないので plugin.json を読む
PLUGIN_JSON = Path(__file__).resolve().parent.parent.parent / ".claude-plugin" / "plugin.json"
EVENTS = ("stop", "subagent_stop", "teammate_idle")
# hooks.json の timeout(3600)より確実に短くする。Claude Code 側が先にフックを殺すと
# プロセスグループの後始末(killpg)が走らず、テストランナーが孤児として残る。
TIMEOUT_MAX_SEC = 3000
GATE_DEFAULTS = {
    "on": list(EVENTS),
    "timeout_sec": 600,
    # 既定は「全部見張り、明らかな雑音だけ除く」。狭い既定だと、言語が違うリポジトリで
    # watch を書き忘れたときにゲートが無言で掛からなくなる。
    "watch": ["*"],
    "ignore": [
        "node_modules/*",
        "*/node_modules/*",
        ".venv/*",
        "*/.venv/*",
        "dist/*",
        "build/*",
        "target/*",
        ".claude/*",
        ".loop/*",
        "*.md",
    ],
}


def load(root: str | None) -> dict | None:
    """設定を返す。ファイルが無い repo は None(=このrepoではゲート無効)。
    ファイルはあるが読めない・不正なら {"_error": 理由}(Stop側が警告を出す)。

    git リポジトリでは HEAD にコミットされた設定を優先する。作業ツリーの設定は
    エージェントが書き換えられる(command を true にする、ファイルを壊す・消す)ので、
    それでゲートを無効化できないようにするため。HEAD に無ければ作業ツリー版を使い、
    コミットを促す通知("_notice")を付ける。
    """
    if not root:
        return None
    path = Path(root) / CONFIG_NAME
    committed = fingerprint.head_file(root, CONFIG_NAME) if fingerprint.repo_root(root) else None
    try:
        working = path.read_bytes() if path.is_file() else None
    except OSError as exc:
        if committed is None:
            return {"_error": f"cannot read {CONFIG_NAME}: {exc}"}
        working = None
    notice = None
    source_name = None
    if committed is not None:
        source = committed
        source_name = "HEAD"
        if working is None:
            notice = f"{CONFIG_NAME} is missing from the working tree; using the committed version"
        elif working != committed:
            notice = (
                f"{CONFIG_NAME} differs from HEAD; using the committed version. "
                "Commit the change if it is intended"
            )
    elif working is not None:
        source = working
        source_name = "working-tree"
        if fingerprint.repo_root(root):
            notice = (
                f"{CONFIG_NAME} is not committed. Commit it so the gate "
                "cannot be altered by editing the working tree"
            )
    else:
        return None
    try:
        raw = json.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"_error": f"cannot read {CONFIG_NAME}: {exc}"}
    result = _validate(raw)
    if "_error" not in result:
        result["_source"] = source_name
        if notice:
            result["_notice"] = notice
    return result


def _validate(raw) -> dict:
    gate = raw.get("gate") if isinstance(raw, dict) else None
    if not isinstance(gate, dict) or not isinstance(gate.get("command"), str):
        return {"_error": f"{CONFIG_NAME} has no gate.command (string)"}
    if not gate["command"].strip():
        return {"_error": f"{CONFIG_NAME}: gate.command must not be empty"}
    merged = dict(GATE_DEFAULTS)
    merged.update(gate)

    timeout_sec = merged.get("timeout_sec")
    if (
        isinstance(timeout_sec, bool)
        or not isinstance(timeout_sec, int)
        or not 1 <= timeout_sec <= TIMEOUT_MAX_SEC
    ):
        return {
            "_error": f"{CONFIG_NAME}: gate.timeout_sec must be an integer "
            f"between 1 and {TIMEOUT_MAX_SEC}"
        }

    for key in ("watch", "ignore"):
        value = merged.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return {"_error": f"{CONFIG_NAME}: gate.{key} must be a list of strings"}

    on = merged.get("on")
    if not isinstance(on, list) or not on or not all(v in EVENTS for v in on):
        return {"_error": f"{CONFIG_NAME}: gate.on must be a non-empty list of {', '.join(EVENTS)}"}

    return {"gate": merged}


def plugin_version() -> str | None:
    """plugin.json の version。読めなければ None(告知や status を止めない)。"""
    try:
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = data.get("version") if isinstance(data, dict) else None
    return v if isinstance(v, str) and v else None
