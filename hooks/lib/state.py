"""repoごとの検証状態。.loop/state.json に置く(セッションを跨いで残る)。

保持するのは「最後にゲートを通った時点のフィンガープリント」。現在の
フィンガープリントと一致していれば、その状態は検証済みということ。
"""
import json
from pathlib import Path


def _path(root: str) -> Path:
    return Path(root) / ".loop" / "state.json"


def read_verified(root: str) -> str | None:
    try:
        value = json.loads(_path(root).read_text(encoding="utf-8")).get("verified")
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def write_verified(root: str, fingerprint: str) -> None:
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"verified": fingerprint}), encoding="utf-8")
