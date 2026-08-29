# Per-agent blocked record (0.9.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `blocked` の記録をエージェント単位のスコープ(session / agent / teammate)で分け、同一 worktree の並行セッション・並行 subagent が互いの「2 度ブロックしない」記録を共有しないようにする。

**Architecture:** `hooks/lib/state.py` に純関数 `scope(event)` と dict 形式の `blocked` API(read/write/clear/count、64 件上限、原子書込)を置く。`hooks/gate.py` の `_refuse` は scope 付きで照合・記録し、pass 経路は全消去する。`hooks/lib/status.py` は現在の fp でブロック済みの scope 数を表示する。contract golden は入力に `session_id` 等を足すが出力は不変。

**Tech Stack:** Python 3.10+、pytest、hypothesis、mutmut(`scripts/verify.py`)。

**Spec:** `docs/superpowers/specs/2026-08-29-per-agent-blocked-design.md`

## Global Constraints

- import は `from hooks.lib import …`(ルート起点)。`from lib import …` は禁止(CLAUDE.md 6)。
- 入口ファイル(`hooks/gate.py` / `hooks/session_start.py` / `hooks/hooks.json`)は動かさない。`gate.py` の変更は spec §2.3 の範囲(`_refuse` 3 行 + pass 経路 1 行 + docstring)に留める。
- lib は例外を外に出さない(書込失敗は握る)。
- スコープ文字列は状態ファイル内にだけ現れる。判定ログ・`additionalContext`・`systemMessage` に出さない。
- `additionalContext` / `systemMessage` の文言、`.loop-hooks.json` schema、`hooks.json`、`session_start.py`、判定ログの形式は変えない。contract golden の `output` は無変更(変わったら理由を記録して止まる)。
- `BLOCKED_MAX_SCOPES = 64`、`"manual"` フォールバック、表示文言 `yes (N agents already blocked at this state)` は spec の値をそのまま使う。
- ゲート(`uv run python scripts/verify.py quick`)は各コミット前に緑。`quick` の増分 ≤ 1 秒。
- 実ホームパスをソース・コミットメッセージに書かない(プレースホルダーは `/home/USER`)。
- 各タスクは foreground で実行し、subagent を使わない。コミットメッセージは日本語の既存流儀(`feat(state): …` など)。

---

### Task 1: `state` — scope 関数と dict 形式の blocked

**Files:**
- Modify: `hooks/lib/state.py`
- Test: `tests/test_state.py`(既存の blocked 3 テストを置換)、`tests/test_properties.py`(P6a 更新 + P7 追加)

**Interfaces:**
- Produces:
  - `state.BLOCKED_MAX_SCOPES: int = 64`
  - `state.scope(event: dict[str, Any]) -> str`
  - `state.read_blocked(root: str, scope: str) -> str | None`
  - `state.write_blocked(root: str, scope: str, fingerprint: str) -> None`
  - `state.clear_blocked(root: str) -> None`
  - `state.read_blocked_scopes(root: str, fingerprint: str) -> int`
- 旧 API `read_blocked(root)` / `write_blocked(root, fp)` は削除(呼び出し側は Task 2・3 で追従。Task 1 完了時点では gate/status が壊れるので、**Task 1 は gate/status の呼び出し側も最小限で同時に直す**: 下記 Step 5 参照)。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_state.py`)**

既存の `test_初期状態ではブロック記録が無い` / `test_ブロック記録は書いて読める` / `test_検証済みとブロックは共存する` を以下に置き換える(`REPO` 定数はそのまま使う)。

```python
# --- ブロック記録(0.9.0: エージェント単位のスコープ) ---

import pytest


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"hook_event_name": "Stop", "cwd": "/x"}, "manual"),
        ({"hook_event_name": "Stop", "cwd": "/x", "session_id": 123}, "manual"),
        ({"hook_event_name": "Stop", "cwd": "/x", "session_id": "s1"}, "s1"),
        ({"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1"}, "s1/a1"),
        ({"hook_event_name": "SubagentStop", "session_id": "s1"}, "s1"),
        ({"hook_event_name": "TeammateIdle", "session_id": "s1", "teammate_name": "w"}, "s1/w"),
        ({"hook_event_name": "TeammateIdle", "session_id": "s1"}, "s1"),
        ({"hook_event_name": "Stop", "session_id": "s1", "agent_id": "a1"}, "s1"),
    ],
)
def test_scopeはイベントの識別子から決まる(event, expected):
    assert state.scope(event) == expected


def test_初期状態ではブロック記録が無い():
    assert state.read_blocked(REPO, "s1") is None
    assert state.read_blocked_scopes(REPO, "fp-bad") == 0


def test_ブロック記録はスコープごとに独立():
    state.write_blocked(REPO, "s1/a", "fp-bad")
    assert state.read_blocked(REPO, "s1/a") == "fp-bad"
    assert state.read_blocked(REPO, "s1/b") is None
    state.write_blocked(REPO, "s1/b", "fp-bad")
    assert state.read_blocked_scopes(REPO, "fp-bad") == 2
    assert state.read_blocked_scopes(REPO, "other") == 0


def test_clear_blockedで全スコープが消える():
    state.write_blocked(REPO, "s1", "fp-bad")
    state.write_blocked(REPO, "s2", "fp-bad")
    state.clear_blocked(REPO)
    assert state.read_blocked(REPO, "s1") is None
    assert state.read_blocked(REPO, "s2") is None
    assert state.read_blocked_scopes(REPO, "fp-bad") == 0


def test_検証済みとブロックは共存する():
    state.write_verified(REPO, "fp-good")
    state.write_blocked(REPO, "s1", "fp-bad")
    assert state.read_verified(REPO) == "fp-good"
    assert state.read_blocked(REPO, "s1") == "fp-bad"


def test_上限を超えると最古のスコープが落ちる():
    for i in range(state.BLOCKED_MAX_SCOPES + 1):
        state.write_blocked(REPO, f"s{i}", "fp")
    assert state.read_blocked(REPO, "s0") is None
    assert state.read_blocked(REPO, "s1") == "fp"
    assert state.read_blocked(REPO, f"s{state.BLOCKED_MAX_SCOPES}") == "fp"
    assert state.read_blocked_scopes(REPO, "fp") == state.BLOCKED_MAX_SCOPES


def test_同じスコープの再書込は最新扱いになる():
    for i in range(state.BLOCKED_MAX_SCOPES):
        state.write_blocked(REPO, f"s{i}", "fp")
    state.write_blocked(REPO, "s0", "fp2")  # s0 を末尾へ
    state.write_blocked(REPO, "new", "fp")  # 65 件目: 落ちるのは s1
    assert state.read_blocked(REPO, "s0") == "fp2"
    assert state.read_blocked(REPO, "s1") is None


def test_旧形式の文字列blockedは未ブロック扱い():
    p = state._path(REPO)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"root": REPO, "verified": "v", "blocked": "fp-bad"}), encoding="utf-8")
    assert state.read_blocked(REPO, "s1") is None
    assert state.read_blocked_scopes(REPO, "fp-bad") == 0
    state.write_blocked(REPO, "s1", "fp-bad")  # 次の書込で dict に置き換わる
    assert json.loads(p.read_text(encoding="utf-8"))["blocked"] == {"s1": "fp-bad"}
    assert state.read_verified(REPO) == "v"


def test_書込は原子的で一時ファイルを残さない():
    state.write_blocked(REPO, "s1", "fp")
    state.write_verified(REPO, "v")
    files = sorted(p.name for p in state._path(REPO).parent.iterdir())
    assert files == [state._path(REPO).name]
```

`tests/test_state.py` の先頭に `import json` が無ければ足す(`import pytest` も同様。既にあれば重複させない)。

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_state.py -q`
Expected: FAIL(`state.scope` が無い / `read_blocked` の引数数が違う)

- [ ] **Step 3: 実装(`hooks/lib/state.py`)**

モジュール docstring の `blocked` 行を次に差し替える:

```
- blocked : スコープ(セッション / subagent / teammate)ごとに、最後にブロックした時点。
            同じエージェントに同じ状態を繰り返しブロックしないためのガード。
            0.9.0 から dict(scope → fingerprint)。pass で全消去、上限 64 件。
```

`FP_UNAVAILABLE_KEY` の下に追加:

```python
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
```

`_write` を原子書込にし、汎用の `_update` に分ける:

```python
def _update(root: str, mutate: Any) -> None:
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
```

blocked API を置き換える:

```python
def _blocked_map(root: str) -> dict[str, str]:
    """blocked の dict。旧形式(str)や壊れた値は空扱い。"""
    value = _read(root).get("blocked")
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def read_blocked(root: str, scope: str) -> str | None:
    return _blocked_map(root).get(scope)


def read_blocked_scopes(root: str, fingerprint: str) -> int:
    """この指紋でブロック済みのスコープ数(--status の表示用)。"""
    return sum(1 for v in _blocked_map(root).values() if v == fingerprint)


def write_blocked(root: str, scope: str, fingerprint: str) -> None:
    def mutate(data: dict[str, Any]) -> None:
        current = data.get("blocked")
        blocked = dict(current) if isinstance(current, dict) else {}
        blocked.pop(scope, None)  # 再書込は末尾へ(挿入順が古さの順)
        blocked[scope] = fingerprint
        while len(blocked) > BLOCKED_MAX_SCOPES:
            del blocked[next(iter(blocked))]
        data["blocked"] = blocked

    _update(root, mutate)


def clear_blocked(root: str) -> None:
    """pass 後に呼ぶ。指紋が verified に変わるので、全スコープの記録が無意味になる。"""
    _update(root, lambda data: data.__setitem__("blocked", {}))
```

- [ ] **Step 4: state テストが通ることを確認**

Run: `uv run pytest tests/test_state.py -q`
Expected: PASS

- [ ] **Step 5: 呼び出し側の最小追従(ゲートを緑にするため)**

`hooks/gate.py` `_refuse`(spec §2.3 そのまま):

```python
    key = current if current is not None else state.FP_UNAVAILABLE_KEY
    scope = state.scope(event)
    if key == state.read_blocked(root, scope):
        return {"systemMessage": WARN + detail}
    state.write_blocked(root, scope, key)
```

`handle` の pass 経路: `state.write_blocked(root, "")  # 直ったのでブロック記録を無効化` → `state.clear_blocked(root)  # 直ったので全スコープのブロック記録を無効化`。

`_refuse` docstring の 1 文目「同じフィンガープリントは2度ブロックしない。」→「同じエージェントに同じフィンガープリントは2度ブロックしない(0.9.0: 記録は session / agent / teammate のスコープ別)。」

`hooks/lib/status.py` `collect`: `blocked=key == state.read_blocked(root),` → `blocked=state.read_blocked_scopes(root, key),`。`render`: 

```python
    if info["blocked"] is not None:
        n = info["blocked"]
        blocked_text = f"yes ({n} agents already blocked at this state)" if n else "no"
        lines.append(_row("blocked", blocked_text))
```

`tests/test_status.py` の 2 テストを追従:

```python
def test_blockedは現在の指紋でブロック済みのスコープ数(tmp_path):
    r = repo(tmp_path)
    fp = fingerprint.compute(str(r), GATE)
    state.write_blocked(str(r), "s1", fp)
    state.write_blocked(str(r), "s1/a", fp)
    assert status.collect(str(r))["blocked"] == 2
    (r / "b.ts").write_text("y\n", encoding="utf-8")
    assert status.collect(str(r))["blocked"] == 0


def test_blockedがyesのときの文言が固定(tmp_path):
    r = repo(tmp_path)
    fp = fingerprint.compute(str(r), GATE)
    state.write_blocked(str(r), "s1", fp)
    state.write_blocked(str(r), "s2", fp)
    out = status.render(status.collect(str(r)))
    assert "  blocked   yes (2 agents already blocked at this state)" in out.splitlines()
```

`tests/test_architecture.py` の判定パリティ 2 本を追従(`_stop` は変えない。session_id 無し = `"manual"` スコープ):

```python
def test_判定式_blockedは現在の指紋と一致するときだけで再ブロックしない(tmp_path):
    root = _repo(tmp_path, command="false")
    assert status.collect(root)["blocked"] == 0
    out = gate.handle(_stop(root))
    assert out and "hookSpecificOutput" in out and _last(root)["result"] == "fail"
    assert status.collect(root)["blocked"] == 1
    out = gate.handle(_stop(root))
    assert out and "systemMessage" in out and _last(root)["result"] == "warn"
    (tmp_path / "main.ts").write_text("changed\n", encoding="utf-8")
    assert status.collect(root)["blocked"] == 0  # 状態が変わればまたブロックする


def test_判定式_指紋が取れないときのblockedはgateの固定キーと同じ(tmp_path, monkeypatch):
    root = _repo(tmp_path, command="false")
    monkeypatch.setattr(fingerprint, "compute", lambda root, gate_cfg: None)
    gate.handle(_stop(root))  # 失敗、"fp-unavailable" でブロック記録
    assert state.read_blocked(root, state.scope(_stop(root))) == state.FP_UNAVAILABLE_KEY
    assert status.collect(root)["blocked"] == 1
    out = gate.handle(_stop(root))
    assert out and "systemMessage" in out  # gate も再ブロックしない
```

`tests/test_properties.py` P6a を追従し、P7 を足す:

```python
@settings(deadline=None)
@given(verified=_fp_text, blocked=_fp_text, noticed=_fp_text)
def test_P6a_書いた値がそのまま読め互いに干渉しない(verified: str, blocked: str, noticed: str):
    root = _root()
    state.write_verified(root, verified)
    state.write_blocked(root, "s", blocked)
    state.write_noticed(root, noticed)
    assert state.read_verified(root) == verified
    assert state.read_blocked(root, "s") == blocked
    assert state.read_noticed(root) == noticed
    # 置き場を決めるキーは固定長 16(sha256 の先頭 16 桁): 長さがずれると衝突しやすくなる
    assert len(state.key(root)) == 16


# ---- P7: blocked のスコープは互いに干渉しない(上限内) ----

_scope_text = st.text(alphabet=st.characters(blacklist_characters="\x00"), min_size=1, max_size=32)


@settings(deadline=None)
@given(
    writes=st.lists(st.tuples(_scope_text, _fp_text), min_size=1, max_size=state.BLOCKED_MAX_SCOPES)
)
def test_P7_他スコープの書込は自スコープの読取値を変えない(writes: list[tuple[str, str]]):
    root = _root()
    for scope, fp in writes:
        state.write_blocked(root, scope, fp)
    last: dict[str, str] = {}
    for scope, fp in writes:
        last[scope] = fp  # 同じ scope は最後の書込が勝つ
    for scope, fp in last.items():
        assert state.read_blocked(root, scope) == fp
    for fp in {v for v in last.values()}:
        assert state.read_blocked_scopes(root, fp) == sum(1 for v in last.values() if v == fp)
```

- [ ] **Step 6: 全体が緑になることを確認**

Run: `uv run python scripts/verify.py quick`
Expected: exit 0(contract golden も `teammate_idle-repeat` は `session_id` 無しなので `"manual"` スコープで従来どおり warn)。

- [ ] **Step 7: コミット**

```bash
git add hooks/lib/state.py hooks/gate.py hooks/lib/status.py tests/test_state.py tests/test_status.py tests/test_architecture.py tests/test_properties.py
git commit -m "feat(state): ブロック記録をエージェント単位のスコープに分け、原子書込にする"
```

---

### Task 2: `gate` — スコープ分離の振る舞いテスト

**Files:**
- Test: `tests/test_gate.py`(追加のみ。`gate.py` は Task 1 で変更済み)

**Interfaces:**
- Consumes: `state.scope`, `state.read_blocked(root, scope)`, `state.clear_blocked`(Task 1)。`setup_repo` / `subagent` / `teammate` / `blocked` は既存ヘルパー。

- [ ] **Step 1: テストを書く(`test_TeammateIdleは状態が変わればまたブロックする` の直後)**

```python
# --- 0.9.0: ブロック記録はエージェント単位 ---


def test_別のsubagentは同じ状態でもブロックされる(tmp_path):
    """規則の根拠は「フィードバックを受けた本人が何も直していない」こと。
    別の subagent はフィードバックを見ていないので 1 回はブロックする。"""
    base = subagent(setup_repo(tmp_path, "false"))
    a = {**base, "session_id": "s1", "agent_id": "a1"}
    b = {**base, "session_id": "s1", "agent_id": "a2"}
    assert blocked(gate.handle(a))
    assert blocked(gate.handle(b))  # 0.8.0 までは warn で素通りしていた
    out = gate.handle(a)
    assert blocked(out) is None and "systemMessage" in out  # a 本人は warn


def test_別セッションのStopは同じ状態でもブロックされる(tmp_path):
    base = setup_repo(tmp_path, "false")
    assert blocked(gate.handle({**base, "session_id": "s1"}))
    assert blocked(gate.handle({**base, "session_id": "s2"}))
    out = gate.handle({**base, "session_id": "s1"})
    assert blocked(out) is None and "systemMessage" in out


def test_同じセッションのStopは同じスコープ(tmp_path):
    base = setup_repo(tmp_path, "false")
    assert blocked(gate.handle({**base, "session_id": "s1"}))
    out = gate.handle({**base, "session_id": "s1", "agent_id": "ignored-on-stop"})
    assert blocked(out) is None and "systemMessage" in out


def test_session_idが無ければmanualスコープで従来どおり(tmp_path):
    event = setup_repo(tmp_path, "false")
    assert blocked(gate.handle(event))
    assert state.read_blocked(str(tmp_path), state.MANUAL_SCOPE) is not None
    out = gate.handle(event)
    assert blocked(out) is None and "systemMessage" in out


def test_passで全スコープのブロック記録が消える(tmp_path):
    marker = tmp_path / "ok"
    base = setup_repo(tmp_path, f"test -e {marker}")
    a = {**base, "session_id": "s1"}
    b = {**base, "session_id": "s2"}
    assert blocked(gate.handle(a)) and blocked(gate.handle(b))
    marker.write_text("", encoding="utf-8")
    assert gate.handle(a) is None  # pass
    assert state.read_blocked(str(tmp_path), "s1") is None
    assert state.read_blocked(str(tmp_path), "s2") is None


def test_スコープ文字列はログと出力に出ない(tmp_path):
    base = setup_repo(tmp_path, "false")
    event = {**base, "session_id": "SESSION-XYZ", "agent_id": "AGENT-XYZ", "hook_event_name": "SubagentStop"}
    out = gate.handle(event)
    assert "SESSION-XYZ" not in json.dumps(out) and "AGENT-XYZ" not in json.dumps(out)
    rec = log.tail(str(tmp_path), 1)[0]
    assert "SESSION-XYZ" not in json.dumps(rec) and "AGENT-XYZ" not in json.dumps(rec)
```

`tests/test_gate.py` の import に `log` が無ければ `from hooks.lib import log` 相当を既存の import 行に足す(`state` も同様)。

- [ ] **Step 2: 通ることを確認(Task 1 で実装済みなので初回から緑のはず。赤ならそれは Task 1 の欠陥)**

Run: `uv run pytest tests/test_gate.py -q`
Expected: PASS

- [ ] **Step 3: ゲートとコミット**

Run: `uv run python scripts/verify.py quick` → exit 0

```bash
git add tests/test_gate.py
git commit -m "test(gate): ブロック記録のスコープ分離(subagent / session / manual / pass で全消去)を固定"
```

---

### Task 3: contract golden の入力を実際の Claude Code の形にする

**Files:**
- Modify: `tests/contracts/*.json`(9 本)、`tests/test_contracts.py`

**Interfaces:**
- Consumes: 既存の `fill` / `normalize` / `PLACEHOLDERS`。
- Produces: 入力プレースホルダ `<SESSION>`(`fill` で `"session-test"` に置換)。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_contracts.py` の `test_ゴールデンが揃っている` に追加)**

```python
    assert golden["input"]["session_id"] == "<SESSION>", name
    if golden["input"]["hook_event_name"] == "SubagentStop":
        assert "agent_id" in golden["input"], name
    if golden["input"]["hook_event_name"] == "TeammateIdle":
        assert {"teammate_name", "team_name"} <= set(golden["input"]), name
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_contracts.py -q -k ゴールデンが揃っている`
Expected: FAIL(`session_id` が無い)

- [ ] **Step 3: golden と `fill` を更新**

`fill` の文字列分岐を:

```python
    if isinstance(value, str):
        return value.replace("<CWD>", ctx["cwd"]).replace("<SESSION>", "session-test")
```

`PLACEHOLDERS` に `"<SESSION>"` を足し、モジュール docstring の可変部分の列挙にも `<SESSION>` を足す。

9 本の golden の `input` を更新し、`checked` を `"2026-08-29"` にする:

- `session_start-*.json`: `"session_id": "<SESSION>"` を `cwd` の後に足す。
- `stop-pass.json` / `stop-fail.json` / `stop-reentry.json`: 同上。
- `subagent_stop-fail.json`: `"session_id": "<SESSION>", "agent_id": "agent-test", "agent_type": "general-purpose"` を足す。
- `teammate_idle-fail.json` / `teammate_idle-repeat.json`: `"session_id": "<SESSION>", "teammate_name": "worker", "team_name": "team-test"` を足す(2 本とも同じ `teammate_name`。repeat は同じ teammate の 2 回目なので warn のまま)。

`output` / `exit_code` / `stderr` は触らない。

- [ ] **Step 4: 通ることを確認**

Run: `uv run pytest tests/test_contracts.py tests/test_packaging.py -q`
Expected: PASS(`output` が変わって落ちた場合はここで止まり、理由を controller に報告する)

- [ ] **Step 5: ゲートとコミット**

Run: `uv run python scripts/verify.py quick` → exit 0

```bash
git add tests/contracts tests/test_contracts.py
git commit -m "test(contracts): golden の入力に session_id / agent_id / teammate_name を足す(出力は不変)"
```

---

### Task 4: ドキュメント・版・最終検証

**Files:**
- Modify: `README.md`、`README.ja.md`、`CHANGELOG.md`、`pyproject.toml`、`.claude-plugin/plugin.json`、`uv.lock`

**Interfaces:**
- Consumes: Task 1〜3 の成果。

- [ ] **Step 1: README.md**

状態ファイルの節(`{"root": "/home/alice/my-project", "verified": "9f2c…", "blocked": ""}` 付近)を:

```json
{"root": "/home/alice/my-project", "verified": "9f2c…", "blocked": {"<session>/<agent>": "9f2c…"}}
```

```
`verified` is the fingerprint recorded the last time the gate passed and is shared by every
session in the worktree (same files, same verdict). `blocked` maps a scope — the session, the
subagent (`session/agent_id`) or the teammate (`session/teammate_name`) that received the
feedback — to the fingerprint it was blocked at, so the same agent is never blocked twice at
the same state while other agents still get the feedback once. It is cleared on every pass
and capped at 64 scopes. Delete the file to force the gate to run on the next turn.
```

Limitations の「One recorded fingerprint per repository, so concurrent sessions in the same worktree share it.」を削除し、次を足す:

```
- Claude Code ends the turn on its own after 8 consecutive Stop-hook blocks. The gate never
  gets there: a second failure at the same fingerprint is let through with a warning.
```

「The same fingerprint is never blocked twice」系の文(行 97 付近・268 付近)に「for the same agent」を足す。

- [ ] **Step 2: README.ja.md**

対応する箇所(状態ファイル例、`verified` / `blocked` の説明、制限の「同じ worktree で並行する複数セッションは記録を共有する」の削除、8 連続 block の 1 行)を README.md と同じ内容で日本語にする。

- [ ] **Step 3: CHANGELOG.md**

先頭に追加:

```markdown
## [0.9.0] - 2026-08-29

### Changed
- **`blocked` is recorded per agent.** The "never block the same fingerprint twice" rule now
  keys on the agent that received the feedback — the session for `Stop`, `session/agent_id`
  for `SubagentStop`, `session/teammate_name` for `TeammateIdle` (`manual` when the hook input
  has no `session_id`). Concurrent sessions in the same worktree, and sibling subagents in one
  session, no longer inherit each other's block and pass unverified. `verified` stays shared
  (same files, same verdict). The record is cleared on every pass and capped at 64 scopes.
- The state file is written atomically (temp file + rename), so concurrent hooks cannot leave
  a torn JSON behind.
- `--status` `blocked` row now counts scopes: `yes (2 agents already blocked at this state)`.
- Contract goldens carry `session_id` / `agent_id` / `teammate_name` like the real hook input.

### Upgrading
- **Restart Claude Code after updating.** `hooks/gate.py` changed; hook definitions are a
  session-start snapshot. The first SessionStart after the restart prints `[loop-hooks 0.9.0]`.
- The state file's `blocked` field becomes an object on the first block after the update. No
  manual migration: an old-format string is treated as "not blocked" by 0.9.0, and an object
  is treated the same way by older plugin versions still running in another session.
```

- [ ] **Step 4: 版を上げる**

`pyproject.toml` `version = "0.9.0"`、`.claude-plugin/plugin.json` `"version": "0.9.0"`、`uv lock` で `uv.lock` を更新。

- [ ] **Step 5: 最終検証**

Run: `uv run python scripts/verify.py all`
Expected: exit 0。`quick` の所要時間を 0.8.0 の 14.1 秒と比べ、増分 ≤ 1 秒であることを報告に書く。baseline は `state` / `status` の total 変化でランナーが再基準化する(`tests/mutation-baseline.json` の diff を確認し、killed が下がっていないことを報告に書く)。

- [ ] **Step 6: コミット**

```bash
git add README.md README.ja.md CHANGELOG.md pyproject.toml .claude-plugin/plugin.json uv.lock tests/mutation-baseline.json
git commit -m "chore: 0.9.0 のリリース準備(ブロック記録のエージェント単位化を文書化、再起動要件を明記)"
```
