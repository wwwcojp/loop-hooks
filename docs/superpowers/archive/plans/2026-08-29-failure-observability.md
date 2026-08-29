# 0.8.0 失敗の可観測性と技術負債 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 判定ログの fail / warn 記録に `reason` を残し、`--status` に集計行(`summary`)と予算警告を出し、第 5 段階で deferred にした技術負債 3 件(`FP_UNAVAILABLE_KEY` の置き場、AST 検査の top-level 判定、`stop-pass` の `decision` 検査)を解消して 0.8.0 を出す。

**Architecture:** `reason` の抽出は `hooks/lib/log.py` の純関数 `failure_reason(output) -> str`。`gate.handle` は fail 経路でそれを記録に足すだけ。集計は `hooks/lib/status.py` の `summarize(records) -> dict | None` と `_format_summary`。`FP_UNAVAILABLE_KEY` は `hooks/lib/state.py` に移し、gate と status がそれを参照する。`hooks/gate.py` を変更するので再起動が必要なリリース。

**Tech Stack:** Python 3.10+, pytest, uv, mutmut(baseline は runner が更新)。

**Spec:** `docs/superpowers/specs/2026-08-29-failure-observability-design.md`

## Global Constraints

- `hooks/gate.py` の変更は §2.1(`reason` 記録)と §2.4(`state.FP_UNAVAILABLE_KEY` 参照)の 2 点だけ。`hooks/session_start.py`・`hooks/hooks.json` は変更しない。入口ファイルを移動しない。
- エージェントへ返す `additionalContext` / `systemMessage` の文言は変えない(`tests/contracts/*.json` は無変更で緑のはず。変わったら理由を記録して golden を直す)。
- `reason` は最大 120 字。旧形式のログ行(`reason` なし)はそのまま読める。
- `status.SLOW_BUDGET_SEC = 30`。設定項目にしない。
- テスト名は日本語の `test_…`。import は `from hooks.lib import …`(ルート起点)。
- 実ホームパスを書かない(placeholder `/home/USER`)。
- `uv run python scripts/verify.py quick` がコミット前に exit 0。`quick` 増分 ≤ 1 秒。
- `tests/mutation-baseline.json` は runner だけが書く(`log` / `status` / `state` の total 変化で再基準化される)。
- 1 タスク 1 コミット、メッセージは各タスクに書いたものをそのまま使う。

---

## ファイル構成

- Modify: `hooks/lib/log.py` — `FAILURE_RE`, `REASON_MAX_CHARS`, `failure_reason()`(Task 1)
- Modify: `hooks/gate.py` — fail 経路で `rec["reason"]`(Task 2)、`state.FP_UNAVAILABLE_KEY` 参照(Task 3)
- Modify: `hooks/lib/state.py` — `FP_UNAVAILABLE_KEY`(Task 3)
- Modify: `hooks/lib/status.py` — `FP_UNAVAILABLE_KEY` 削除(Task 3)、`SLOW_BUDGET_SEC`, `summarize`, `_format_summary`, `collect["summary"]`, `render` の `summary` 行、`_format_recent` の `reason`(Task 4)
- Modify: `tests/test_log.py`, `tests/test_gate.py`, `tests/test_status.py`, `tests/test_architecture.py`, `tests/test_contracts.py`
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `README.md`, `README.ja.md`, `pyproject.toml`, `.claude-plugin/plugin.json`, `uv.lock`, spec §3(Task 6)

---

### Task 1: `log.failure_reason` — 出力から失敗理由を 1 行抽出する

**Files:**
- Modify: `hooks/lib/log.py`
- Test: `tests/test_log.py`

**Interfaces:**
- Produces: `log.FAILURE_RE: re.Pattern[str]`、`log.REASON_MAX_CHARS = 120`、`log.failure_reason(output: str) -> str`(Task 2 が使う)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_log.py` 末尾に追記:

```python
# ---- failure_reason: 検証コマンドの出力から失敗理由を 1 行抽出する ----

RUNNER_OUT = "\n".join(
    [
        "$ uv run python scripts/verify.py quick",
        "[verify] leak: ok",
        "[verify] lint: FAIL",
        "$ uv run ruff check hooks tests scripts",
        "E501 Line too long (101 > 100)",
        "Found 2 errors.",
    ]
)
PYTEST_OUT = "....F..\nFAILED tests/test_x.py::test_y - assert 1 == 2\n1 failed, 3 passed in 0.5s\n"


def test_failure_reasonは最初のFAIL行を採る():
    assert log.failure_reason(RUNNER_OUT) == "[verify] lint: FAIL"


def test_failure_reasonはpytestのFAILED行を採る():
    assert log.failure_reason(PYTEST_OUT) == "FAILED tests/test_x.py::test_y - assert 1 == 2"


def test_failure_reasonは小文字のerrorコロンも採る():
    assert log.failure_reason("$ make\nbuilding\nerror: link failed\ndone\n") == "error: link failed"


def test_failure_reasonは一致が無ければ最後の非空行():
    assert log.failure_reason("$ x\nFound 2 errors.\n\n\n") == "Found 2 errors."


def test_failure_reasonはコマンド行を候補にしない():
    # 1 行目の "$ cmd" は候補から外す(コマンド文字列に error: が含まれても拾わない)
    assert log.failure_reason("$ run --on-error: stop\nall good\n") == "all good"
    assert log.failure_reason("$ false\n") == ""


def test_failure_reasonはタイムアウトと実行不能をそのまま返す():
    assert log.failure_reason("$ cmd\ntimed out after 300s") == "timed out after 300s"
    assert log.failure_reason("$ cmd\ncould not run: [Errno 2] No such file") == (
        "could not run: [Errno 2] No such file"
    )


def test_failure_reasonは前後の空白を除き120字で切る():
    long = "FAIL " + "x" * 200
    out = log.failure_reason("  " + long + "  \n")
    assert out == long[: log.REASON_MAX_CHARS] and len(out) == 120


def test_failure_reasonは空出力で空文字():
    assert log.failure_reason("") == ""
    assert log.failure_reason("\n\n") == ""
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_log.py -q -k failure_reason`
Expected: FAIL(`AttributeError: module 'hooks.lib.log' has no attribute 'failure_reason'`)

- [ ] **Step 3: 実装**

`hooks/lib/log.py` に `import re` を足し(`import os` の後、アルファベット順)、`KEEP_LINES` の下に:

```python
# 失敗理由の抽出(0.8.0 spec §2.1)。ゲートは検証コマンドの中身を知らないので出力から汎用に取る:
# FAIL / FAILED / ERROR / "error:" を含む最初の行、無ければ最後の非空行。設定項目にはしない。
FAILURE_RE = re.compile(r"\b(FAIL|FAILED|ERROR)\b|\berror:")
REASON_MAX_CHARS = 120


def failure_reason(output: str) -> str:
    """検証コマンドの出力から、ログに残す失敗理由を 1 行(最大 REASON_MAX_CHARS 字)返す。"""
    lines = output.splitlines()
    if lines and lines[0].startswith("$ "):
        lines = lines[1:]  # run_gate が先頭に付けるコマンド行は候補にしない
    candidates = [line.strip() for line in lines if line.strip()]
    if not candidates:
        return ""
    hit = next((line for line in candidates if FAILURE_RE.search(line)), candidates[-1])
    return hit[:REASON_MAX_CHARS]
```

- [ ] **Step 4: 通す**

Run: `uv run pytest tests/test_log.py -q` → 全部 passed。

- [ ] **Step 5: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → 6 checks ok。

```bash
git add hooks/lib/log.py tests/test_log.py
git commit -m "feat(log): 検証出力から失敗理由を 1 行抽出する failure_reason を追加"
```

---

### Task 2: gate が fail / warn の記録に `reason` を残す

**Files:**
- Modify: `hooks/gate.py`(`handle` の else 経路、1 行)
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `log.failure_reason(detail)`(Task 1)。
- Produces: 判定記録の `reason: str`(fail / warn のみ)。Task 4 の `recent` 表示が使う。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_gate.py` の「記録」節(`test_失敗はran_failと記録される` の直後)に追記:

```python
def test_失敗の記録には理由が入る(tmp_path):
    gate.handle(setup_repo(tmp_path, "echo NOPE >&2; exit 1"))
    rec = log.tail(str(tmp_path), 1)[0]
    assert rec["result"] == "fail" and rec["reason"] == "NOPE"


def test_理由はFAIL行を優先する(tmp_path):
    gate.handle(setup_repo(tmp_path, "echo '[verify] lint: FAIL'; echo 'Found 2 errors.'; exit 1"))
    assert log.tail(str(tmp_path), 1)[0]["reason"] == "[verify] lint: FAIL"


def test_成功とskippedの記録には理由が無い(tmp_path):
    event = setup_repo(tmp_path, "true")
    gate.handle(event)
    assert "reason" not in log.tail(str(tmp_path), 1)[0]
    gate.handle(event)  # skipped
    assert "reason" not in log.tail(str(tmp_path), 1)[0]


def test_再入の警告記録にも理由が入る(tmp_path):
    event = setup_repo(tmp_path, "echo BROKEN; exit 1")
    event["stop_hook_active"] = True
    gate.handle(event)
    rec = log.tail(str(tmp_path), 1)[0]
    assert rec["result"] == "warn" and rec["reason"] == "BROKEN"


def test_タイムアウトの理由(tmp_path):
    gate.handle(setup_repo(tmp_path, "sleep 5", timeout_sec=1))
    assert log.tail(str(tmp_path), 1)[0]["reason"] == "timed out after 1s"
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_gate.py -q -k "理由"` → FAIL(`KeyError: 'reason'`)。

- [ ] **Step 3: 実装**

`hooks/gate.py` の `handle` 内、`out = _refuse(...)` の直後・`rec["result"] = ...` の次の行に:

```python
        rec["reason"] = log.failure_reason(detail)  # 0.8.0: --status が「何が落ちたか」を答えるため
```

- [ ] **Step 4: 通す**

Run: `uv run pytest tests/test_gate.py tests/test_contracts.py -q` → 全部 passed(contract golden は出力 JSON を見るので無変更のはず。落ちたら理由を報告して止める)。

- [ ] **Step 5: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok。

```bash
git add hooks/gate.py tests/test_gate.py
git commit -m "feat(gate): fail / warn の判定記録に失敗理由(reason)を残す"
```

---

### Task 3: `FP_UNAVAILABLE_KEY` を `state` に移す

**Files:**
- Modify: `hooks/lib/state.py`, `hooks/lib/status.py`, `hooks/gate.py`
- Test: `tests/test_architecture.py`

**Interfaces:**
- Produces: `state.FP_UNAVAILABLE_KEY = "fp-unavailable"`。`status.FP_UNAVAILABLE_KEY` は削除。

- [ ] **Step 1: テストを置き換える(失敗する)**

`tests/test_architecture.py` の `test_判定式_指紋が取れないときのblockedはgateの固定キーと同じ` 内の
`status.FP_UNAVAILABLE_KEY` を `state.FP_UNAVAILABLE_KEY` に変更し、
`test_gateの指紋不能キーはstatusの定数と同じ文字列` を次に置き換える:

```python
def test_gateの指紋不能キーはstateの定数を参照しリテラルを持たない():
    """入口は lib を import できるが lib は入口を import できない。定数は state が持ち、gate は参照する。"""
    tree = ast.parse((ROOT / "hooks" / "gate.py").read_text(encoding="utf-8"))
    literals = {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert state.FP_UNAVAILABLE_KEY not in literals
    refs = {
        (n.value.id, n.attr)
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }
    assert ("state", "FP_UNAVAILABLE_KEY") in refs
    assert not hasattr(status, "FP_UNAVAILABLE_KEY")
```

Run: `uv run pytest tests/test_architecture.py -q -k "指紋不能キー"` → FAIL(`state` に属性が無い)。

- [ ] **Step 2: 実装**

`hooks/lib/state.py` の `def state_dir()` の前に:

```python
# gate._refuse と status.collect が「指紋が取れない状態」を blocked として記録・照合するための固定キー。
# state が持つ(両者が import できる最下層に近いモジュール)。
FP_UNAVAILABLE_KEY = "fp-unavailable"
```

`hooks/lib/status.py`: `FP_UNAVAILABLE_KEY = "fp-unavailable"` とその上のコメント 1 行を削除し、
`collect` 内の `FP_UNAVAILABLE_KEY` を `state.FP_UNAVAILABLE_KEY` に。

`hooks/gate.py` の `_refuse`: `key = current if current is not None else "fp-unavailable"` を
`key = current if current is not None else state.FP_UNAVAILABLE_KEY` に。

- [ ] **Step 3: 通す**

Run: `uv run pytest tests/test_architecture.py tests/test_status.py tests/test_gate.py -q` → 全部 passed。
`grep -rn "fp-unavailable" hooks/` の結果が `hooks/lib/state.py` の 1 行だけであることを確認。

- [ ] **Step 4: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok。

```bash
git add hooks/lib/state.py hooks/lib/status.py hooks/gate.py tests/test_architecture.py
git commit -m "refactor(state): 指紋不能キー FP_UNAVAILABLE_KEY を state に移し、gate と status が参照する"
```

---

### Task 4: status の `summary` 行・予算警告・`recent` の `reason`

**Files:**
- Modify: `hooks/lib/status.py`
- Test: `tests/test_status.py`, `tests/test_architecture.py`(`INFO_KEYS`)

**Interfaces:**
- Consumes: `log.tail(root, n)`, `log.MAX_LINES`、記録の `reason`(Task 2)。
- Produces: `status.SLOW_BUDGET_SEC = 30`、`status.summarize(records: list[dict]) -> dict | None`、`collect(...)["summary"]`、render の `summary` 行。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_status.py` 末尾に追記:

```python
# ---- summary: ログ全体の集計行(0.8.0) ----


def _ran(result: str, ms: int) -> dict:
    return {"event": "Stop", "decision": "ran", "result": result, "ms": ms}


def test_summarizeは件数と中央値を集計する():
    records = [  # log.tail の順(新しい順)
        {"event": "Stop", "decision": "skipped", "ts": "2026-08-27T15:28:18Z"},
        {**_ran("fail", 12000), "ts": "2026-08-27T15:00:00Z"},
        {**_ran("warn", 9000), "ts": "2026-08-27T14:00:00Z"},
        {**_ran("pass", 11543), "ts": "2026-08-27T13:00:00Z"},
        {"event": "SessionStart", "decision": "announced", "ts": "2026-08-26T13:10:16Z"},
    ]
    s = status.summarize(records)
    assert s == {
        "records": 5,
        "since": "2026-08-26T13:10:16Z",
        "ran": 3,
        "pass": 1,
        "fail": 1,
        "warn": 1,
        "skipped": 1,
        "median_ms": 11543,
        "slow": False,
    }


def test_summarizeは空ならNone():
    assert status.summarize([]) is None


def test_summarizeの中央値は上側中央値でmsが無ければNone():
    assert status.summarize([_ran("pass", 1), _ran("pass", 2), _ran("pass", 4), _ran("pass", 8)])[
        "median_ms"
    ] == 4
    assert status.summarize([{"event": "Stop", "decision": "ran", "result": "pass"}])["median_ms"] is None


def test_summarizeのslowは中央値または直近5件の最大が予算超過():
    budget = status.SLOW_BUDGET_SEC * 1000
    assert status.summarize([_ran("pass", budget + 1)])["slow"] is True
    fast = [_ran("pass", 1000)] * 10
    assert status.summarize(fast)["slow"] is False
    assert status.summarize([_ran("pass", budget + 1), *fast])["slow"] is True  # 直近 1 件が超過
    assert status.summarize([*fast, _ran("pass", budget + 1)])["slow"] is False  # 古い 1 件は無視


def test_collectにsummaryが入る(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, _ran("pass", 1234))
    s = status.collect(root)["summary"]
    assert s["records"] == 1 and s["pass"] == 1 and s["median_ms"] == 1234


def test_renderのsummary行の書式(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, _ran("fail", 12000))
    log.append(root, _ran("pass", 11000))
    log.append(root, {"event": "Stop", "decision": "skipped"})
    info = status.collect(root)
    since = info["summary"]["since"][:16].replace("T", " ")
    line = f"  summary   3 records since {since}: ran 2 (pass 1 / fail 1 / warn 0), skipped 1, median 12.0s"
    assert line in status.render(info).splitlines()


def test_renderのsummaryが無ければその旨(tmp_path):
    out = status.render(status.collect(str(repo(tmp_path))))
    assert "  summary   (no records)" in out.splitlines()


def test_renderのsummaryにslow警告(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, _ran("pass", status.SLOW_BUDGET_SEC * 1000 + 1))
    out = status.render(status.collect(root))
    assert " (slow: over the 30s budget, split the command)" in out


def test_recentにreasonが載る(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, {**_ran("fail", 1000), "reason": "[verify] lint: FAIL"})
    out = status.render(status.collect(root))
    assert "ran       fail  1.0s [verify] lint: FAIL" in out
```

さらに既存の `test_renderのゴールデン_有効で未検証` の `expected` に、`records` 行と `recent` 行の間に
次の 1 行を足す(`ts` は既存の変数):

```python
            f"  summary   1 records since {ts}: ran 1 (pass 1 / fail 0 / warn 0), skipped 0, median 1.2s",
```

`test_configが無くてもinfoの全キーが揃う` のキー集合と `tests/test_architecture.py` の `INFO_KEYS` に
`"summary"` を足す。

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_status.py tests/test_architecture.py -q` → summary 関連が FAIL。

- [ ] **Step 3: 実装**

`hooks/lib/status.py`:

```python
SLOW_BUDGET_SEC = 30  # Stop ゲートの予算(親 spec §7)。超えたら summary で分離を促す
```

を `RECENT_SEARCH` の下に。`_recent` の下に:

```python
def summarize(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """ログ全体(新しい順)の集計。空なら None。中央値は上側中央値(偶数件なら大きい側)。"""
    if not records:
        return None
    ran = [r for r in records if r.get("decision") == "ran"]
    ms = sorted(r["ms"] for r in ran if isinstance(r.get("ms"), int))
    median = ms[len(ms) // 2] if ms else None
    recent_ms = [r["ms"] for r in ran[:RECENT] if isinstance(r.get("ms"), int)]
    budget = SLOW_BUDGET_SEC * 1000
    slow = (median is not None and median > budget) or any(m > budget for m in recent_ms)
    return {
        "records": len(records),
        "since": str(records[-1].get("ts") or ""),
        "ran": len(ran),
        "pass": sum(1 for r in ran if r.get("result") == "pass"),
        "fail": sum(1 for r in ran if r.get("result") == "fail"),
        "warn": sum(1 for r in ran if r.get("result") == "warn"),
        "skipped": sum(1 for r in records if r.get("decision") == "skipped"),
        "median_ms": median,
        "slow": slow,
    }
```

`collect` の初期辞書に `"summary": summarize(log.tail(root or cwd, log.MAX_LINES)),` を
`"recent"` の次に追加。`render` の `records` 行の直後に:

```python
    lines.append(_row("summary", _format_summary(info["summary"])))
```

`_safe_format_recent` の前に:

```python
def _format_summary(s: dict[str, Any] | None) -> str:
    if not s:
        return "(no records)"
    since = str(s["since"])[:16].replace("T", " ")
    median = f"{s['median_ms'] / 1000:.1f}s" if s["median_ms"] is not None else "n/a"
    text = (
        f"{s['records']} records since {since}: ran {s['ran']} "
        f"(pass {s['pass']} / fail {s['fail']} / warn {s['warn']}), "
        f"skipped {s['skipped']}, median {median}"
    )
    if s["slow"]:
        text += f" (slow: over the {SLOW_BUDGET_SEC}s budget, split the command)"
    return text
```

`_format_recent` の `if r.get("note"):` ブロックの後に:

```python
    if r.get("reason"):
        parts.append(str(r["reason"]))
```

- [ ] **Step 4: 通す**

Run: `uv run pytest tests/test_status.py tests/test_architecture.py tests/test_gate.py -q` → 全部 passed。
`uv run hooks/gate.py --status .` を実行し、`summary` 行が実ログで表示されることを目視(出力を報告に貼る。実ホームパスは `/home/USER` に置換)。

- [ ] **Step 5: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok。

```bash
git add hooks/lib/status.py tests/test_status.py tests/test_architecture.py
git commit -m "feat(status): ログ全体の集計行(summary)と 30 秒予算の警告、recent に失敗理由を表示"
```

---

### Task 5: AST 検査の top-level 判定と `stop-pass` の `decision` 検査

**Files:**
- Modify: `tests/test_architecture.py`, `tests/test_contracts.py`

**Interfaces:**
- Consumes: `_own_imports(path)`(既存)、`run_case` / `CASES`(既存)、`log.tail`。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_architecture.py` の `test_入口はモジュール直下でstatusをimportしない` の直後に:

```python
def test_own_importsはtryやifの中のimportもモジュール直下として扱う(tmp_path):
    """関数・クラスの外側はすべて「モジュール直下」。try / if / with で包んでも規則を逃れない。"""
    src = "\n".join(
        [
            "import sys",
            "try:",
            "    from hooks.lib import status",
            "except ImportError:",
            "    pass",
            "if sys.version_info >= (3, 10):",
            "    import hooks.lib.log",
            "def f():",
            "    from hooks.lib import config",
            "class C:",
            "    from hooks.lib import state",
        ]
    )
    p = tmp_path / "entry.py"
    p.write_text(src, encoding="utf-8")
    found = {(module, tuple(names), top) for module, names, top in _own_imports(p)}
    assert ("hooks.lib", ("status",), True) in found
    assert ("hooks.lib.log", (), True) in found
    assert ("hooks.lib", ("config",), False) in found
    assert ("hooks.lib", ("state",), False) in found
```

`tests/test_contracts.py` の import に `from hooks.lib import config, log` を(既存の `config` 行を置き換え)、
`test_入口の出力はゴールデンと一致する` の直後に:

```python
def test_stop_passは実際にゲートを走らせて通している(tmp_path):
    """output: null は skipped / off でも同じ形になる。ran で pass したことを記録で固定する。"""
    _, _, _, ctx = run_case("stop-pass", tmp_path)
    rec = log.tail(ctx["cwd"], 1)[0]
    assert rec["decision"] == "ran" and rec["result"] == "pass"
```

Run: `uv run pytest tests/test_architecture.py tests/test_contracts.py -q -k "tryやif or stop_pass"`
Expected: 前者 FAIL(`try` 内が top=False で拾われる)、後者 PASS(現状の挙動を固定するテスト。RED を見るには
`CASES["stop-pass"]["warmup"]` を一時的に 1 にすると skipped になり落ちる — 確認したら戻す)。

- [ ] **Step 2: 実装**

`tests/test_architecture.py` の `_own_imports` を置き換える:

```python
def _own_imports(path: Path) -> list[tuple[str, list[str], bool]]:
    """自リポジトリ由来の import を (モジュール名, 取り込む名前, モジュール直下か) で返す。

    「モジュール直下」= 関数・クラス定義の外側。try / if / with で包まれていても直下とみなす
    (import 時に実行されるため)。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nested: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested.update(id(child) for child in ast.walk(node) if child is not node)
    found: list[tuple[str, list[str], bool]] = []
    for node in ast.walk(tree):
        top = id(node) not in nested
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_own(alias.name):
                    found.append((alias.name, [], top))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0 or _is_own(module):
                found.append((module, [a.name for a in node.names], top))
    return found
```

- [ ] **Step 3: 通す**

Run: `uv run pytest tests/test_architecture.py tests/test_contracts.py -q` → 全部 passed
(`gate.py` の `status_main` 内の import は関数内なので引き続き非 top-level)。

- [ ] **Step 4: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok。

```bash
git add tests/test_architecture.py tests/test_contracts.py
git commit -m "test(arch): モジュール直下の判定を関数・クラスの外側に広げ、stop-pass が実際に走ることを固定"
```

---

### Task 6: 所要時間、文書、0.8.0

**Files:**
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `README.md`, `README.ja.md`, `pyproject.toml`, `.claude-plugin/plugin.json`, `uv.lock`, `tests/mutation-baseline.json`(runner)、spec §3

- [ ] **Step 1: 所要時間と all**

Run: `time uv run python scripts/verify.py quick`(2 回)、`uv run python scripts/verify.py all; echo exit=$?`。
Expected: quick ≤ 15 秒(0.7.0 実測 14.0 秒 + 1 秒)、all exit 0。`git diff --stat tests/mutation-baseline.json` を記録
(`log` / `status` / `state` の total が変わり再基準化される。runner が書いたものをそのまま commit)。

- [ ] **Step 2: 文書とバージョン**

`CHANGELOG.md` 先頭:

```markdown
## [0.8.0] - 2026-08-29

### Added
- **Failure reason in the decision log.** `fail` and `warn` records now carry `reason`: the first
  output line matching `FAIL` / `FAILED` / `ERROR` / `error:`, otherwise the last non-empty line,
  truncated to 120 characters. `--status` shows it on the `recent` rows.
- **`summary` row in `--status` / `/loop-hooks:status`**: record count and first timestamp, how
  many runs passed / failed / were let through with a warning, how many turns were skipped by
  change detection, and the median run time. When the median or any of the last five runs exceeds
  the 30-second budget the row says so and suggests splitting the command.

### Changed
- `hooks/lib/state.py` now owns `FP_UNAVAILABLE_KEY` (the key the gate and `--status` use when the
  fingerprint cannot be computed); `status.FP_UNAVAILABLE_KEY` is gone.

### Upgrading
- **Restart Claude Code after updating.** `hooks/gate.py` changed; hook definitions are a
  session-start snapshot. The first SessionStart after the restart prints `[loop-hooks 0.8.0]`.
```

`CLAUDE.md` 開発節の「状態の確認」の行に「`summary` 行にログ全体の集計(pass / fail / warn / skipped、中央値)が出る」を追記。

`README.md`: `## What it does` の「決定ログ」に触れている箇所(`grep -n "recent\|--status" README.md` で探す)に
1 文追加: `Failed runs record why (the first failing line of the output), and the status output starts with a summary of how often the gate ran, passed and failed, and how long it takes.` `README.ja.md` に対応文。

バージョン `0.8.0`: `pyproject.toml`、`.claude-plugin/plugin.json`、`uv lock`。

spec `docs/superpowers/specs/2026-08-29-failure-observability-design.md` §3 末尾に
「確認済み(2026-08-29): quick N 秒 / all K 秒 / baseline の再基準化(ファイルと件数)/ contract golden の変更有無」。

- [ ] **Step 3: 全体検証・コミット**

Run: `uv run python scripts/verify.py all; echo exit=$?` → exit=0。

```bash
git add CHANGELOG.md CLAUDE.md README.md README.ja.md pyproject.toml .claude-plugin/plugin.json uv.lock tests/mutation-baseline.json docs
git commit -m "chore: 0.8.0 のリリース準備(失敗理由と summary を文書化、再起動要件を明記)"
```

---

### Task 7: 受け入れ(コントローラ)

- 最終レビュー → マージ → 公開前チェック → push → CI 緑 → tag `v0.8.0` と Release → 計画をアーカイブ。
- 利用者: `/plugin marketplace update loop-hooks` → `/plugin update loop-hooks@loop-hooks` → **再起動** → `[loop-hooks 0.8.0]` と `/loop-hooks:status` の `summary` 行を確認。
