"""Hookの標準入出力。stdinイベント読取とJSON出力を担う。"""

import json
import sys
from typing import Any, cast


def read_event() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
        return cast(dict[str, Any], data) if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def emit(obj: dict[str, Any]) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
