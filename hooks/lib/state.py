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


def state_dir() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base) / "state"
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "loop-hooks" / "state"


def key(root: str) -> str:
    """リポジトリを識別するキー。state と log が同じ置き場を共有するために公開する。"""
    return hashlib.sha256(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]


def _path(root: str) -> Path:
    return state_dir() / f"{key(root)}.json"


def _read(root: str) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_str(root: str, key: str) -> str | None:
    value = _read(root).get(key)
    return value if isinstance(value, str) else None


def _write(root: str, key: str, fingerprint: str) -> None:
    data = _read(root)
    data["root"] = root  # どのリポジトリの記録か辿れるように残す
    data[key] = fingerprint
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


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
