"""Hookの標準入出力。stdinイベント読取とJSON出力を担う。"""
import json
import sys


def read_event() -> dict:
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def emit(obj: dict) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
