"""判定ログ。ゲートが「走った / 走らなかった / なぜか」をリポジトリの外に残す。

--status が「なぜ走らなかったか」を答えるための唯一の一次情報。書込の失敗は
ゲートの判定に影響させない(全て握る)。
"""

import datetime
import json
import os
from pathlib import Path

from . import state

MAX_LINES = 1200  # これを超えたら…
KEEP_LINES = 1000  # …直近この行数に切り詰める(償却的に安い)


def _path(root: str) -> Path:
    return state.state_dir() / f"{state.key(root)}.log.jsonl"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append(root: str, record: dict) -> None:
    """1行追記する。ts はここで付ける。例外は投げない。"""
    try:
        p = _path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": _now(), **record}, ensure_ascii=False)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        _trim(p)
    except (OSError, TypeError, ValueError):
        pass


def _trim(p: Path) -> None:
    """上限を超えたら直近 KEEP_LINES 行に切り詰める。一時ファイルに書いて差し替える(原子的)。"""
    lines = p.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_LINES:
        return
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def tail(root: str, n: int = 5) -> list[dict]:
    """最新 n 件を新しい順に返す。壊れた行は飛ばす。例外は投げない。"""
    try:
        lines = _path(root).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
        if len(out) >= n:
            break
    return out
