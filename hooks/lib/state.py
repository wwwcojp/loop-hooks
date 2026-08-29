"""repoごとの検証状態。リポジトリの外に置く(セッションを跨いで残る)。

保持するのは検証状態の記録:
- verified: 最後にゲートを通った時点。現在の値と一致すれば検証済み。
- blocked : スコープ(セッション / subagent / teammate)ごとに、最後にブロックした時点。
            同じエージェントに同じ状態を繰り返しブロックしないためのガード。
            0.9.0 から dict(scope → fingerprint)。pass で全消去、上限 64 件。

置き場はプラグインの永続データ領域(CLAUDE_PLUGIN_DATA)。それが無い環境
(手動実行など)では XDG のキャッシュ配下。いずれにせよ利用者のリポジトリには
書かないので、.gitignore への追記を強いることがない。
"""

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

# gate._refuse と status.collect が「指紋が取れない状態」を blocked として記録・照合する
# ための固定キー。state が持つ(両者が import できる最下層に近いモジュール)。
FP_UNAVAILABLE_KEY = "fp-unavailable"

# blocked が保持するスコープ数の上限。pass で全消去されるので、通常ここには届かない。
BLOCKED_MAX_SCOPES = 64
MANUAL_SCOPE = "manual"


def scope(event: dict[str, Any]) -> str:
    """ブロック記録のスコープ。フィードバックを受けた本人にだけ再ブロックしないための識別子。

    session_id が無い(手動実行・古い Claude Code)なら "manual"。SubagentStop は agent_id、
    TeammateIdle は teammate_name で session 内を分ける。Stop は session 単位。
    スコープ文字列は状態ファイルにだけ書き、ログや出力には出さない。
    """
    session = event.get("session_id")
    if not isinstance(session, str) or not session:
        return MANUAL_SCOPE
    name = event.get("hook_event_name")
    sub = None
    if name == "SubagentStop":
        sub = event.get("agent_id")
    elif name == "TeammateIdle":
        sub = event.get("teammate_name")
    if isinstance(sub, str) and sub:
        return f"{session}/{sub}"
    return session


def state_dir() -> Path:
    """記録の置き場。フックには CLAUDE_PLUGIN_DATA が渡るのでそれを使う。

    渡らない経路(ターミナルからの --status)では、Claude Code がプラグインに割り当てる
    データ置き場(`<config dir>/plugins/data/loop-hooks-<marketplace>/`)を探し、
    フックと同じ記録を読めるようにする。無ければ XDG キャッシュ。
    """
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base) / "state"
    found = _plugin_data_dir()
    if found is not None:
        return found
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "loop-hooks" / "state"


def _plugin_data_dir() -> Path | None:
    """`<config dir>/plugins/data/loop-hooks-*/state` のうち最も新しいもの。無ければ None。"""
    try:
        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        candidates = [
            p / "state"
            for p in (config_dir / "plugins" / "data").glob("loop-hooks-*")
            if (p / "state").is_dir()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)
    except (OSError, RuntimeError):  # RuntimeError: HOME 無し・passwd 無しで Path.home() が失敗
        return None


def key(root: str) -> str:
    """リポジトリを識別するキー。state と log が同じ置き場を共有するために公開する。"""
    return hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]


def _path(root: str) -> Path:
    return state_dir() / f"{key(root)}.json"


def _read(root: str) -> dict[str, Any]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _read_str(root: str, key: str) -> str | None:
    value = _read(root).get(key)
    return value if isinstance(value, str) else None


def _update(root: str, mutate: Callable[[dict[str, Any]], None]) -> None:
    """read-modify-write。書込失敗は握る(状態が残せなくてもゲートの判定は続行する)。

    同一ディレクトリの一時ファイルに書いて os.replace するので、並行フックの同時書込でも
    途中まで書けた JSON が読まれることはない。
    """
    tmp: Path | None = None
    try:
        data = _read(root)
        data["root"] = root  # どのリポジトリの記録か辿れるように残す
        mutate(data)
        p = _path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
        tmp = None
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def _write(root: str, key: str, fingerprint: str) -> None:
    _update(root, lambda data: data.__setitem__(key, fingerprint))


def read_verified(root: str) -> str | None:
    return _read_str(root, "verified")


def write_verified(root: str, fingerprint: str) -> None:
    _write(root, "verified", fingerprint)


def read_noticed(root: str) -> str | None:
    """最後に利用者へ出した設定通知(同じ通知を繰り返さないため)。"""
    return _read_str(root, "noticed")


def write_noticed(root: str, notice: str) -> None:
    _write(root, "noticed", notice)


def _blocked_map(root: str) -> dict[str, str]:
    """blocked の dict。旧形式(str)や壊れた値は空扱い。"""
    value = _read(root).get("blocked")
    if not isinstance(value, dict):
        return {}
    items = cast("dict[Any, Any]", value)
    return {k: v for k, v in items.items() if isinstance(k, str) and isinstance(v, str)}


def read_blocked(root: str, scope: str) -> str | None:
    return _blocked_map(root).get(scope)


def read_blocked_scopes(root: str, fingerprint: str) -> int:
    """この指紋でブロック済みのスコープ数(--status の表示用)。"""
    return sum(1 for v in _blocked_map(root).values() if v == fingerprint)


def write_blocked(root: str, scope: str, fingerprint: str) -> None:
    def mutate(data: dict[str, Any]) -> None:
        current = data.get("blocked")
        blocked: dict[str, str] = (
            dict(cast("dict[str, str]", current)) if isinstance(current, dict) else {}
        )
        blocked.pop(scope, None)  # 再書込は末尾へ(挿入順が古さの順)
        blocked[scope] = fingerprint
        while len(blocked) > BLOCKED_MAX_SCOPES:
            del blocked[next(iter(blocked))]
        data["blocked"] = blocked

    _update(root, mutate)


def clear_blocked(root: str) -> None:
    """pass 後に呼ぶ。指紋が verified に変わるので、全スコープの記録が無意味になる。"""
    _update(root, lambda data: data.__setitem__("blocked", {}))
