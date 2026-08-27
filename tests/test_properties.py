"""Property-Based Testing(hypothesis)。任意の入力に対して成り立つべき性質を固定する
(第 4 段階 spec §2.1)。

例示テストは「思いついた入力」しか守れない。ここでは生成した入力で lib の不変条件を検査する。
例ごとに一意な root を使い、autouse の CLAUDE_PLUGIN_DATA 隔離の中で状態が衝突しないようにする。
"""

import fnmatch
import sys
import uuid
from pathlib import Path
from typing import Any

from hypothesis import assume, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import config, fingerprint  # noqa: E402


def _root() -> str:
    """例ごとに一意なリポジトリパス(実在しなくてよい。state/log はキー化して外に書く)。"""
    return f"/home/USER/pbt-{uuid.uuid4().hex}"


# ---- P1: config._validate は任意の JSON 値で例外を出さず、_error か gate のどちらかを返す ----

_json_scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=16)
)
_json_values = st.recursive(
    _json_scalars,
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=8), c, max_size=4),
    max_leaves=12,
)
_gate_like = st.fixed_dictionaries(
    {},
    optional={
        "command": _json_values,
        "timeout_sec": _json_values,
        "watch": _json_values,
        "ignore": _json_values,
        "on": _json_values,
    },
)
_raw_configs = _json_values | st.fixed_dictionaries({"gate": _gate_like | _json_values})


@settings(deadline=None)
@given(raw=_raw_configs)
def test_P1_validateは任意のJSONで例外を出さずerrorかgateを返す(raw: Any):
    result = config._validate(raw)
    assert isinstance(result, dict)
    if "_error" in result:
        assert isinstance(result["_error"], str) and result["_error"]
        assert "gate" not in result
        return
    gate = result["gate"]
    assert isinstance(gate["command"], str) and gate["command"].strip()
    assert isinstance(gate["timeout_sec"], int) and not isinstance(gate["timeout_sec"], bool)
    assert 1 <= gate["timeout_sec"] <= config.TIMEOUT_MAX_SEC
    for key in ("watch", "ignore"):
        assert isinstance(gate[key], list) and all(isinstance(v, str) for v in gate[key])
    assert isinstance(gate["on"], list) and gate["on"]
    assert all(v in config.EVENTS for v in gate["on"])


# ---- P2: is_watched — ignore が watch より優先し、watch に無いものは False ----

_segment = st.from_regex(r"[A-Za-z0-9_.-]{1,8}", fullmatch=True)
_rel_paths = st.lists(_segment, min_size=1, max_size=3).map("/".join)
_patterns = st.lists(
    st.one_of(
        _segment, _segment.map(lambda s: f"*.{s}"), _segment.map(lambda s: f"{s}/*"), st.just("*")
    ),
    max_size=4,
)
# P2b は「watch のどれにも一致しない」を assume するため、全一致する "*" が高頻度だと
# filter_too_much になる(pre-flight で確認済み)。"*" を候補から外して健全性を保つ。
_patterns_no_wildcard = st.lists(
    st.one_of(_segment, _segment.map(lambda s: f"*.{s}"), _segment.map(lambda s: f"{s}/*")),
    max_size=4,
)


@settings(deadline=None)
@given(rel=_rel_paths, watch=_patterns)
def test_P2a_ignoreに一致すればwatchに関係なくFalse(rel: str, watch: list[str]):
    cfg = {"watch": watch + [rel], "ignore": [rel]}
    assert fingerprint.is_watched(rel, cfg) is False


@settings(deadline=None)
@given(rel=_rel_paths, watch=_patterns_no_wildcard)
def test_P2b_watchのどれにも一致しなければFalse(rel: str, watch: list[str]):
    assume(not any(fnmatch.fnmatch(rel, p) for p in watch))
    assert fingerprint.is_watched(rel, {"watch": watch, "ignore": []}) is False


@settings(deadline=None)
@given(rel=_rel_paths, watch=_patterns, ignore=_patterns)
def test_P2c_watchにrel自身がありignoreに一致しなければTrue(
    rel: str, watch: list[str], ignore: list[str]
):
    assume(not any(fnmatch.fnmatch(rel, p) for p in ignore))
    assert fingerprint.is_watched(rel, {"watch": watch + [rel], "ignore": ignore}) is True
