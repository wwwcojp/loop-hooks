"""repoごとの検証状態。リポジトリの外に置く(セッションを跨いで残る)。

保持するのは2つのフィンガープリント:
- verified: 最後にゲートを通った時点。現在の値と一致すれば検証済み。
- blocked : 最後にブロックした時点。stop_hook_active を持たない
            TeammateIdle で、同じ状態を繰り返しブロックしないためのガード。

置き場はプラグインの永続データ領域(CLAUDE_PLUGIN_DATA)。それが無い環境
(手動実行など)では XDG のキャッシュ配下。いずれにせよ利用者のリポジトリには
書かないので、.gitignore への追記を強いることがない。
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast


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


def _write(root: str, key: str, fingerprint: str) -> None:
    """書込失敗は握る。状態が残せなくてもゲートの判定は続行する(次回また走るだけ)。"""
    try:
        data = _read(root)
        data["root"] = root  # どのリポジトリの記録か辿れるように残す
        data[key] = fingerprint
        p = _path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def read_verified(root: str) -> str | None:
    return _read_str(root, "verified")


def write_verified(root: str, fingerprint: str) -> None:
    _write(root, "verified", fingerprint)


def read_noticed(root: str) -> str | None:
    """最後に利用者へ出した設定通知(同じ通知を繰り返さないため)。"""
    return _read_str(root, "noticed")


def write_noticed(root: str, notice: str) -> None:
    _write(root, "noticed", notice)


def read_blocked(root: str) -> str | None:
    return _read_str(root, "blocked")


def write_blocked(root: str, fingerprint: str) -> None:
    _write(root, "blocked", fingerprint)
