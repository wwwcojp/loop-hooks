"""Property-Based Testing(hypothesis)。任意の入力に対して成り立つべき性質を固定する
(第 4 段階 spec §2.1)。

例示テストは「思いついた入力」しか守れない。ここでは生成した入力で lib の不変条件を検査する。
例ごとに一意な root を使い、autouse の CLAUDE_PLUGIN_DATA 隔離の中で状態が衝突しないようにする。
"""

import fnmatch
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import config, fingerprint, log, state  # noqa: E402


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
        assert result["_error"].startswith(config.CONFIG_NAME)
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
    # gate で省略したキーは GATE_DEFAULTS がそのまま埋める(merge の向き・既定値そのものを検査する)
    raw_gate = (
        raw.get("gate") if isinstance(raw, dict) and isinstance(raw.get("gate"), dict) else {}
    )
    for key, default in config.GATE_DEFAULTS.items():
        if key not in raw_gate:
            assert gate[key] == default


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
    result = fingerprint.is_watched(rel, cfg)
    assert isinstance(result, bool) and result is False


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


# ---- P4: log.tail は任意のバイト列で例外を出さず、list[dict] を n 件以下で返す ----


@settings(deadline=None)
@given(data=st.binary(max_size=2000), n=st.integers(min_value=1, max_value=20))
def test_P4_tailは任意のバイト列で落ちずn件以下のdictを返す(data: bytes, n: int):
    root = _root()
    p = log._path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    out = log.tail(root, n)
    assert isinstance(out, list) and len(out) <= n
    assert all(isinstance(r, dict) for r in out)
    # 中身も検査する: 壊れていない行だけを、ファイルの末尾から新しい順に、n 件で打ち切って
    # 返しているはず(型が dict であること以上の、実際の選び方・順序の契約を固定する)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        lines = []
    expected: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            expected.append(rec)
        if len(expected) >= n:
            break
    assert out == expected


# ---- P5: append を k 回しても行数は上限内。超えた直後は KEEP_LINES 以上 ----


@settings(deadline=None, max_examples=15)
@given(k=st.integers(min_value=0, max_value=log.MAX_LINES + 300))
def test_P5_何回appendしても行数は上限内(k: int):
    root = _root()
    for i in range(k):
        log.append(root, {"event": "Stop", "decision": "skipped", "i": i})
    p = log._path(root)
    lines = len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0
    assert lines <= log.MAX_LINES
    if k > log.MAX_LINES:
        assert lines >= log.KEEP_LINES
    else:
        assert lines == k
    if k > 0:
        # 切り詰めは新しい方を残す: 直前の append(i=k-1)は必ず生き残っているはず
        latest = log.tail(root, 1)
        assert latest and latest[0].get("i") == k - 1


# ---- P6: state の round trip と、壊れたファイルは None ----

_fp_text = st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=64)


@settings(deadline=None)
@given(verified=_fp_text, blocked=_fp_text, noticed=_fp_text)
def test_P6a_書いた値がそのまま読め互いに干渉しない(verified: str, blocked: str, noticed: str):
    root = _root()
    state.write_verified(root, verified)
    state.write_blocked(root, blocked)
    state.write_noticed(root, noticed)
    assert state.read_verified(root) == verified
    assert state.read_blocked(root) == blocked
    assert state.read_noticed(root) == noticed
    # 置き場を決めるキーは固定長 16(sha256 の先頭 16 桁): 長さがずれると衝突しやすくなる
    assert len(state.key(root)) == 16


@settings(deadline=None)
@given(data=st.binary(max_size=500))
def test_P6b_壊れた状態ファイルは例外を出さずNone(data: bytes):
    root = _root()
    p = state._path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    assert state.read_verified(root) is None or isinstance(state.read_verified(root), str)
    assert state.read_blocked(root) is None or isinstance(state.read_blocked(root), str)


# ---- P3: compute は決定的で、watch 外の変更に不変、watch 内の変更に敏感 ----

_P3_CFG = {"watch": ["*.py"], "ignore": ["*.md"]}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=cwd, capture_output=True, check=True)  # noqa: S607


@pytest.fixture(scope="module")
def p3_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """P3 用の git リポジトリ.

    モジュールで 1 回だけ作る。例ごとに 2 ファイルの内容だけ書き換える。
    """
    repo = tmp_path_factory.mktemp("p3-repo")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "watched.py").write_bytes(b"initial\n")
    (repo / "unwatched.md").write_bytes(b"initial\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


@settings(deadline=None, max_examples=10)
@given(a=st.binary(max_size=64), b=st.binary(max_size=64))
def test_P3_computeは決定的でwatch外に不変watch内に敏感(p3_repo: Path, a: bytes, b: bytes):
    root = str(p3_repo)
    (p3_repo / "unwatched.md").write_bytes(a)
    (p3_repo / "watched.py").write_bytes(b)
    fp1 = fingerprint.compute(root, _P3_CFG)
    fp2 = fingerprint.compute(root, _P3_CFG)
    assert fp1 is not None and fp1 == fp2  # 決定性
    (p3_repo / "unwatched.md").write_bytes(a + b"x")
    assert fingerprint.compute(root, _P3_CFG) == fp1  # watch 外は不変
    (p3_repo / "watched.py").write_bytes(b + b"x")
    assert fingerprint.compute(root, _P3_CFG) != fp1  # watch 内は変わる
