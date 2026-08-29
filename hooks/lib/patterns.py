"""watch / ignore のパターンマッチ。gitignore(gitwildmatch)と同じ規則。

対象は「リポジトリ相対のファイルパス」の列。ディレクトリに一致したら配下すべてに一致させる。
- `*` は `/` を跨がない。`**` は直後が `/` か末尾で、それより前に glob 文字が無いか直前が
  `/` のとき跨ぐ(git と同じ)。
- 末尾以外にスラッシュが無ければ任意の深さの basename に一致。あればルート基準。
- 末尾 `/` はディレクトリ指定(配下が必要)。先頭 `!` は否定(後勝ち)。`\\!` はリテラル。
- 不正なパターンは例外にせずリテラル扱い。
"""

from __future__ import annotations

import re
import warnings
from functools import lru_cache


def _glob_to_regex(body: str) -> str:
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "*":
            if body.startswith("**", i):
                after = body[i + 2] if i + 2 < n else ""
                prev = body[i - 1] if i else ""
                # git は literal 接頭辞を剥がして照合する: `**` より前に glob 文字があると跨がない
                if prev in ("", "/") or not any(g in body[:i] for g in "*?[\\"):
                    if after == "/":
                        if not out or out[-1] != "(?:.*/)?":  # 連続する **/ は 1 つに
                            out.append("(?:.*/)?")
                        i += 3
                        continue
                    if after == "":
                        out.append(".*")
                        i += 2
                        continue
                i += 2  # それ以外の ** は * と同じ
            else:
                i += 1
            if not out or out[-1] != "[^/]*":  # 連続する * は 1 つに(バックトラック爆発を防ぐ)
                out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = body.find("]", i + 3 if body.startswith("[!", i) else i + 2)  # "[]" "[!]" は未閉
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                cls = body[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:] + "/"  # git と同じく否定クラスは / に一致しない
                out.append("[" + cls.replace("\\", "\\\\") + "]")
                i = j + 1
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(body[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _translate(pattern: str) -> tuple[bool, re.Pattern[str] | None]:
    """(否定か, 正規表現)。空パターンは None(何にも一致しない)。"""
    negated = pattern.startswith("!")
    if negated or pattern.startswith("\\!"):
        pattern = pattern[1:]
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern[:-1]
    anchored = "/" in pattern
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if not pattern:
        return negated, None
    prefix = "" if anchored else "(?:.*/)?"
    suffix = "/.*$" if dir_only else "(?:/.*)?$"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # "[[a]" の FutureWarning も literal 扱いに倒す
            return negated, re.compile("^" + prefix + _glob_to_regex(pattern) + suffix)
    except Exception:
        return negated, re.compile("^" + prefix + re.escape(pattern) + suffix)


@lru_cache(maxsize=64)
def _compiled(patterns: tuple[str, ...]) -> tuple[tuple[bool, re.Pattern[str] | None], ...]:
    return tuple(_translate(p) for p in patterns)


def matches(rel: str, patterns: list[str]) -> bool:
    """rel がパターン列に一致するか。gitignore と同じ後勝ちで、`!` は直前までの一致を取り消す。"""
    result = False
    for negated, regex in _compiled(tuple(patterns)):
        if regex is not None and regex.match(rel):
            result = not negated
    return result
