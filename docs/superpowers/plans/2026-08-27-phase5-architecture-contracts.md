# 第 5 段階(アーキテクチャ/契約テスト、0.7.0)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 入口ファイルの依存規則・gate と status の判定式一致・書込先の不変条件・lib の例外なし規則を `tests/test_architecture.py` で固定し、Claude Code とのフック入出力契約を `tests/contracts/*.json` のゴールデンと `tests/test_contracts.py` で固定して 0.7.0 を出す。

**Architecture:** すべて例示テスト(`quick` で走る)。`hooks/` は変更しない(入口も lib も)。ゴールデンは可変部分(`<CWD>` / `<COMMAND>` / `<VERSION>` / `<OUTPUT>`)を正規化したうえで辞書ごと完全一致。既存テストが担う不変条件(HEAD 優先、孫プロセス停止)は移動せず参照する。

**Tech Stack:** Python 3.10+, pytest, `ast`, git, uv。

**Spec:** `docs/superpowers/specs/2026-08-27-phase5-architecture-contracts-design.md`(親: `2026-08-26-verification-roadmap-design.md` §6)

## Global Constraints

- `hooks/gate.py`・`hooks/session_start.py`・`hooks/hooks.json`・`hooks/lib/*` は変更しない(欠陥が見つかった場合だけ lib を直し、CHANGELOG の Fixed に載せる)。
- テスト名は日本語の `test_…`。import は `from hooks.lib import …`(ルート起点)、`sys.path.insert(0, <プラグインルート>)`。
- 実ホームパスをソース・ゴールデン・コミットメッセージに書かない(プレースホルダ `/home/USER`)。
- `uv run python scripts/verify.py quick` がコミット前に exit 0(ruff check / ruff format / import-linter / pyright / pytest)。tests/ は pyright 対象外だが ruff(S101・S603 は tests で許可済み)は効く。
- `quick` の増分 ≤ 3 秒(0.6.0 実測 13.3 秒 → 16.3 秒以内)。
- ゴールデンは `tests/contracts/<event>-<case>.json`、各ファイルに `reference: "https://code.claude.com/docs/en/hooks"` と `checked: "2026-08-27"`。自動書き戻し機構は作らない。
- 各テストは `tests/conftest.py` の autouse fixture(`CLAUDE_PLUGIN_DATA` を tmp に隔離)の上で動く。
- 1 タスク 1 コミット、メッセージは各タスクに書いたものをそのまま使う。

---

## ファイル構成

- Create: `tests/test_architecture.py` — §2.1 (a)〜(e)。Task 1〜4 で順に育てる。
- Create: `tests/contracts/*.json`(9 本) — §2.2 ゴールデン。Task 5。
- Create: `tests/test_contracts.py` — §2.2 の検査(in-process 9 本 + サブプロセス 3 本)。Task 5〜6。
- Modify: `tests/test_packaging.py` — ゴールデンの `reference` / `checked` を固定。Task 6。
- Modify: `README.md` / `README.ja.md` / `CLAUDE.md` / `CHANGELOG.md` / `pyproject.toml` / `.claude-plugin/plugin.json` / `uv.lock` / spec §3。Task 7。

---

### Task 1: `tests/test_architecture.py` — 入口の import 規則(AST)と既存担保の参照

**Files:**
- Create: `tests/test_architecture.py`

**Interfaces:**
- Produces: モジュール定数 `ROOT: Path`(プラグインルート)、ヘルパー `_git(cwd, *args)`、`_repo(tmp_path, command) -> str`(設定コミット済み・watch 対象に未検証の変更がある git リポジトリを作り、ルートの文字列を返す)。Task 2〜4 が使う。

- [ ] **Step 1: ファイルを作り、失敗するテストを書く**

```python
"""アーキテクチャ規則(第 5 段階 spec §2.1)。

構造と境界を固定する例示テスト。関数の正しさは各 test_*.py、任意入力に対する性質は
tests/test_properties.py(P1: config._validate、P4: log.tail、P6b: state の壊れたファイル)が担う。

既存テストが担う不変条件(移動しない):
- 作業ツリーの .loop-hooks.json は HEAD より優先されない
  → tests/test_gate.py::test_作業ツリーでcommandを緩めてもHEADの設定でブロックされる
- timeout 時にプロセスグループが残らない
  → tests/test_gate.py::test_タイムアウトで孫プロセスも止まる
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
ENTRIES = ("gate.py", "session_start.py")
GATE = {
    "command": "true",
    "on": ["stop", "subagent_stop", "teammate_idle"],
    "watch": ["*.ts"],
    "ignore": ["*.md"],
    "timeout_sec": 10,
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=cwd, capture_output=True, check=True)


def _repo(tmp_path: Path, command: str = "true") -> str:
    """設定をコミット済みで、watch 対象に未検証の変更がある git リポジトリ。ルートを返す。"""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    cfg = {"gate": {**GATE, "command": command}}
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(cfg), encoding="utf-8")
    _git(tmp_path, "add", ".loop-hooks.json")
    _git(tmp_path, "commit", "-qm", "config")
    (tmp_path / "main.ts").write_text("source\n", encoding="utf-8")
    return str(tmp_path)


# ---- (a) 入口ファイルの import 規則 ----


def _own_imports(path: Path) -> list[tuple[str, list[str], bool]]:
    """自リポジトリ由来の import を (モジュール名, 取り込む名前, モジュール直下か) で返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = set(map(id, tree.body))
    found: list[tuple[str, list[str], bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_own(alias.name):
                    found.append((alias.name, [], id(node) in top_level))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0 or _is_own(module):
                found.append((module, [a.name for a in node.names], id(node) in top_level))
    return found


def _is_own(module: str) -> bool:
    return module in ("hooks", "lib") or module.startswith(("hooks.", "lib."))


def test_入口は自リポジトリからhooks_lib配下しかimportしない():
    """入口は薄い層。lib 以外の自モジュール(入口同士、旧 `lib` 起点)に依存しない。"""
    for name in ENTRIES:
        for module, names, _ in _own_imports(ROOT / "hooks" / name):
            assert module == "hooks.lib" or module.startswith("hooks.lib."), (name, module, names)


def test_入口はモジュール直下でstatusをimportしない():
    """表示専用モジュールはゲート経路で読まない(0.3.0 spec §2)。gate.py は status_main 内だけ。"""
    for name in ENTRIES:
        for module, names, top in _own_imports(ROOT / "hooks" / name):
            if top:
                assert "status" not in names and module != "hooks.lib.status", (name, module)


def test_入口はプラグインルートをsys_pathに入れてからimportする():
    """`from hooks.lib import …` の前提。無いと mutmut の変異キーや CLAUDE.md 6 項と食い違う。"""
    for name in ENTRIES:
        src = (ROOT / "hooks" / name).read_text(encoding="utf-8")
        assert "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))" in src, name


# ---- (e) 既存テストが担う不変条件の参照が実在する ----


def test_既存テストが担う不変条件の参照先が実在する():
    src = (ROOT / "tests" / "test_gate.py").read_text(encoding="utf-8")
    assert "def test_作業ツリーでcommandを緩めてもHEADの設定でブロックされる(" in src
    assert "def test_タイムアウトで孫プロセスも止まる(" in src
```

- [ ] **Step 2: 走らせる**

Run: `uv run pytest tests/test_architecture.py -q`
Expected: 4 passed(規則は現状満たされている。RED を確かめるには一時的に `ENTRIES` に `"lib/status.py"` を足すと `_own_imports` が `from . import …`(相対)を拾って 1 本目が落ちる — 確認したら戻す)。

- [ ] **Step 3: 検証・コミット**

Run: `uv run python scripts/verify.py quick`
Expected: 6 checks ok。

```bash
git add tests/test_architecture.py
git commit -m "test(arch): 入口ファイルの import 規則を AST で固定し、既存テストが担う不変条件を参照する"
```

---

### Task 2: gate と status の判定式の一致

**Files:**
- Modify: `tests/test_architecture.py`(末尾に追記)

**Interfaces:**
- Consumes: `_repo(tmp_path, command)`、`GATE`(Task 1)。`hooks.gate.handle(event) -> dict | None`、`hooks.lib.status.collect(cwd) -> dict`、`hooks.lib.log.tail(root, n)`、`hooks.lib.fingerprint.compute`。

- [ ] **Step 1: import を足し、表駆動テストを追記する**

ファイル先頭の import ブロック(`sys.path.insert` の後)に:

```python
from hooks import gate  # noqa: E402
from hooks.lib import fingerprint, log, state, status  # noqa: E402
```

末尾に:

```python
# ---- (b) gate.handle と status.collect の判定式は同じ(0.3.0 の裁定) ----


def _stop(root: str) -> dict:
    return {"hook_event_name": "Stop", "cwd": root, "stop_hook_active": False}


def _last(root: str) -> dict:
    return log.tail(root, 1)[0]


def test_判定式_未検証ならwill_runでgateはran(tmp_path):
    root = _repo(tmp_path)
    info = status.collect(root)
    assert info["will_run"] is True and info["blocked"] is False
    gate.handle(_stop(root))
    assert _last(root)["decision"] == "ran"


def test_判定式_検証済みならwill_runでなくgateはskipped(tmp_path):
    root = _repo(tmp_path)
    gate.handle(_stop(root))  # 通す
    info = status.collect(root)
    assert info["will_run"] is False
    gate.handle(_stop(root))
    assert _last(root)["decision"] == "skipped"


def test_判定式_指紋が取れなければ両方とも走る側に倒す(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    gate.handle(_stop(root))  # verified を残す
    monkeypatch.setattr(fingerprint, "compute", lambda root, gate_cfg: None)
    info = status.collect(root)
    assert info["fingerprint"] is None and info["will_run"] is True
    gate.handle(_stop(root))
    rec = _last(root)
    assert rec["decision"] == "ran" and rec["note"] == "fingerprint unavailable"


def test_判定式_blockedは現在の指紋と一致するときだけで再ブロックしない(tmp_path):
    root = _repo(tmp_path, command="false")
    assert status.collect(root)["blocked"] is False
    out = gate.handle(_stop(root))
    assert out and "hookSpecificOutput" in out and _last(root)["result"] == "fail"
    assert status.collect(root)["blocked"] is True
    out = gate.handle(_stop(root))
    assert out and "systemMessage" in out and _last(root)["result"] == "warn"
    (tmp_path / "main.ts").write_text("changed\n", encoding="utf-8")
    assert status.collect(root)["blocked"] is False  # 状態が変わればまたブロックする
```

- [ ] **Step 2: 走らせる**

Run: `uv run pytest tests/test_architecture.py -q`
Expected: 8 passed。

- [ ] **Step 3: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → 6 checks ok。

```bash
git add tests/test_architecture.py
git commit -m "test(arch): gate.handle と status.collect の判定式が同じ答えを返すことを表で固定"
```

---

### Task 3: 書込先の不変条件

**Files:**
- Modify: `tests/test_architecture.py`(末尾に追記)

**Interfaces:**
- Consumes: `_repo`、`state._path(root)`, `state.key(root)`, `state.state_dir()`, `state.write_verified`, `log._path(root)`, `log.append`。

- [ ] **Step 1: 追記する**

`import os` を import ブロックに足し、末尾に:

```python
# ---- (c) 状態・ログの書込先はリポジトリ外の既定領域から出ない ----


def _files(root: Path) -> set[str]:
    return {os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs}


def _root_variants(tmp_path: Path) -> list[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sub").mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)
    return [str(repo), str(repo) + "/", str(repo / "sub" / ".."), str(link)]


def test_書込先はどんなroot表現でもstate_dir配下(tmp_path):
    base = state.state_dir()
    for root in _root_variants(tmp_path):
        assert state._path(root).is_relative_to(base), root
        assert log._path(root).is_relative_to(base), root


def test_同じリポジトリの別表現は同じキーに解決される(tmp_path):
    variants = _root_variants(tmp_path)
    keys = {state.key(v) for v in variants}
    assert len(keys) == 1, dict(zip(variants, map(state.key, variants)))


def test_書込でリポジトリ内にファイルが増えない(tmp_path):
    root = Path(_repo(tmp_path))
    (root / "sub").mkdir()
    link = tmp_path.parent / f"{tmp_path.name}-link"
    link.symlink_to(root, target_is_directory=True)
    before = _files(root)
    for v in (str(root), str(root) + "/", str(root / "sub" / ".."), str(link)):
        state.write_verified(v, "fp")
        state.write_blocked(v, "fp")
        state.write_noticed(v, "n")
        log.append(v, {"event": "Stop", "decision": "skipped"})
    assert _files(root) == before
    assert state.read_verified(str(root)) == "fp"
    link.unlink()
```

- [ ] **Step 2: 走らせる**

Run: `uv run pytest tests/test_architecture.py -q -k "書込先 or キーに解決 or 増えない"`
Expected: 3 passed。

- [ ] **Step 3: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok。

```bash
git add tests/test_architecture.py
git commit -m "test(arch): 状態とログの書込先が root の表現(末尾スラッシュ・..・シンボリックリンク)によらず state_dir 配下に留まる"
```

---

### Task 4: `hooks/lib` の公開関数は例外を外に出さない(表駆動)

**Files:**
- Modify: `tests/test_architecture.py`(末尾に追記)

**Interfaces:**
- Consumes: `hooks.lib.config.load/plugin_version/PLUGIN_JSON`、`fingerprint.repo_root/compute/head_file`、`state.*`、`log.*`、`status.collect`。

- [ ] **Step 1: 追記する**

`import pytest` と `from hooks.lib import config` を import に足す(既存の `from hooks.lib import …` 行に `config` を加える)。末尾に:

```python
# ---- (d) hooks/lib の公開関数は例外を外に出さない ----
# 壊れた入力(任意 JSON / 任意バイト列)は tests/test_properties.py の P1 / P4 / P6b が担う。

INFO_KEYS = {
    "cwd", "root", "config_source", "config_error", "notice", "command", "on", "watch",
    "ignore", "timeout_sec", "fingerprint", "verified", "will_run", "blocked", "recent",
    "state_dir",
}  # fmt: skip


@pytest.fixture
def unwritable_state(tmp_path, monkeypatch):
    """CLAUDE_PLUGIN_DATA を書込不能にする。root など制限が効かない環境では skip。"""
    data = tmp_path / "ro"
    data.mkdir()
    data.chmod(0o500)
    if os.access(data, os.W_OK):
        data.chmod(0o700)
        pytest.skip("書込制限が効かない環境(root など)")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    yield data
    data.chmod(0o700)


def test_書込不能でもstateとlogは例外を出さない(tmp_path, unwritable_state):
    (tmp_path / "r").mkdir()
    root = _repo(tmp_path / "r")
    state.write_verified(root, "fp")
    state.write_blocked(root, "fp")
    state.write_noticed(root, "n")
    log.append(root, {"event": "Stop", "decision": "ran"})
    assert state.read_verified(root) is None
    assert state.read_blocked(root) is None
    assert state.read_noticed(root) is None
    assert log.tail(root, 5) == []


def test_状態ファイルとログがディレクトリでも例外を出さない(tmp_path):
    root = str(tmp_path)
    state._path(root).mkdir(parents=True)
    log._path(root).mkdir(parents=True)
    state.write_verified(root, "fp")
    log.append(root, {"event": "Stop"})
    assert state.read_verified(root) is None
    assert log.tail(root, 5) == []


@pytest.fixture
def no_git(tmp_path, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def test_gitが無くてもfingerprintとconfigは例外を出さずNone側に倒す(tmp_path, no_git):
    root = str(tmp_path)
    assert fingerprint.repo_root(root) is None
    assert fingerprint.compute(root, GATE) is None
    assert fingerprint.head_file(root, ".loop-hooks.json") is None
    assert config.load(root) is None  # 設定ファイルも無い
    (tmp_path / ".loop-hooks.json").write_text(json.dumps({"gate": GATE}), encoding="utf-8")
    loaded = config.load(root)
    assert loaded is not None and "_error" not in loaded  # 作業ツリー版で読める


def test_存在しないディレクトリでも例外を出さない(tmp_path):
    missing = str(tmp_path / "missing")
    assert fingerprint.repo_root(missing) is None
    assert config.load(missing) is None
    assert set(status.collect(missing)) == INFO_KEYS


def test_plugin_jsonが壊れていてもplugin_versionはNone(tmp_path, monkeypatch):
    broken = tmp_path / "plugin.json"
    for body in ("{", "[]", '{"version": 3}', '{"version": ""}'):
        broken.write_text(body, encoding="utf-8")
        monkeypatch.setattr(config, "PLUGIN_JSON", broken)
        assert config.plugin_version() is None, body
    monkeypatch.setattr(config, "PLUGIN_JSON", tmp_path / "none.json")
    assert config.plugin_version() is None


def test_status_collectはどの状況でも全キーを返す(tmp_path, unwritable_state, no_git):
    for cwd in (str(tmp_path), str(tmp_path / "missing"), ""):
        assert set(status.collect(cwd)) == INFO_KEYS, cwd
```

- [ ] **Step 2: 走らせる**

Run: `uv run pytest tests/test_architecture.py -q`
Expected: 17 passed(root で走らせている場合は 2 skipped)。`no_git` で `_repo` を呼ばないこと(git が無い)。

- [ ] **Step 3: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok。ruff format が `INFO_KEYS` を整形し直すなら `# fmt: skip` を外して整形結果に従う。

```bash
git add tests/test_architecture.py
git commit -m "test(arch): hooks/lib の公開関数が書込不能・git 不在・存在しないパス・壊れた plugin.json でも例外を出さないことを表で固定"
```

---

### Task 5: ゴールデン契約 9 本と in-process 検査

**Files:**
- Create: `tests/contracts/stop-pass.json`, `stop-fail.json`, `stop-reentry.json`, `subagent_stop-fail.json`, `teammate_idle-fail.json`, `teammate_idle-repeat.json`, `session_start-active.json`, `session_start-disabled.json`, `session_start-not-git.json`
- Create: `tests/test_contracts.py`

**Interfaces:**
- Produces: `tests/test_contracts.py` の `CASES: dict[str, dict]`(ケース名 → 準備方法)、`normalize(value, ctx) -> Any`、`run_case(name, tmp_path) -> tuple[dict | None, int, str]`(in-process: 出力・exit code・stderr)。Task 6 が `CASES` とゴールデン形式を使う。

- [ ] **Step 1: テストを書く(ゴールデンはまだ無い → 失敗)**

`tests/test_contracts.py`:

```python
"""Claude Code とのフック入出力契約(第 5 段階 spec §2.2)。

tests/contracts/*.json がゴールデン。可変部分(<CWD> / <COMMAND> / <VERSION> / <OUTPUT>)を
正規化したうえで、入口の出力と辞書ごと完全一致することを検査する。

契約が変わったら(Claude Code 側のキー名・形、または入口の文言)、ゴールデンを手で直し、
`checked` を更新する。自動で書き戻す仕組みは意図的に作らない。
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks import gate, session_start  # noqa: E402
from hooks.lib import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "tests" / "contracts"
PLACEHOLDERS = ("<CWD>", "<COMMAND>", "<VERSION>", "<OUTPUT>")
GATE = {
    "on": ["stop", "subagent_stop", "teammate_idle"],
    "watch": ["*.ts"],
    "ignore": ["*.md"],
    "timeout_sec": 10,
}
# ケース名 → (準備の種類, 検証コマンド, 事前に同じイベントを流す回数)
CASES: dict[str, dict[str, Any]] = {
    "stop-pass": {"setup": "repo", "command": "true", "warmup": 0},
    "stop-fail": {"setup": "repo", "command": "false", "warmup": 0},
    "stop-reentry": {"setup": "repo", "command": "false", "warmup": 0},
    "subagent_stop-fail": {"setup": "repo", "command": "false", "warmup": 0},
    "teammate_idle-fail": {"setup": "repo", "command": "false", "warmup": 0},
    "teammate_idle-repeat": {"setup": "repo", "command": "false", "warmup": 1},
    "session_start-active": {"setup": "repo", "command": "true", "warmup": 0},
    "session_start-disabled": {"setup": "broken", "command": "", "warmup": 0},
    "session_start-not-git": {"setup": "no-git", "command": "true", "warmup": 0},
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=cwd, capture_output=True, check=True)


def prepare(name: str, tmp_path: Path) -> dict[str, str]:
    """ケースの前提(リポジトリ状態)を作り、正規化の文脈(cwd / command / version)を返す。"""
    case = CASES[name]
    ctx = {"cwd": str(tmp_path), "command": case["command"], "version": _version()}
    body = {"gate": {**GATE, "command": case["command"]}}
    if case["setup"] == "broken":
        body = {"gate": {"command": ""}}  # _validate が拒む(空 command)
    if case["setup"] == "no-git":
        (tmp_path / ".loop-hooks.json").write_text(json.dumps(body), encoding="utf-8")
        return ctx
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(body), encoding="utf-8")
    _git(tmp_path, "add", ".loop-hooks.json")
    _git(tmp_path, "commit", "-qm", "config")
    (tmp_path / "main.ts").write_text("source\n", encoding="utf-8")
    return ctx


def _version() -> str:
    v = config.plugin_version()
    assert v, "plugin.json の version が読めない"
    return v


def load_golden(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


def fill(value: Any, ctx: dict[str, str]) -> Any:
    """ゴールデンの input のプレースホルダを実値にする。"""
    if isinstance(value, dict):
        return {k: fill(v, ctx) for k, v in value.items()}
    if isinstance(value, str):
        return value.replace("<CWD>", ctx["cwd"])
    return value


def normalize(value: Any, ctx: dict[str, str]) -> Any:
    """入口の出力の可変部分をプレースホルダに戻す。"""
    if isinstance(value, dict):
        return {k: normalize(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, ctx) for v in value]
    if not isinstance(value, str):
        return value
    s = value.replace(ctx["cwd"], "<CWD>")
    s = s.replace(f"[loop-hooks {ctx['version']}]", "[loop-hooks <VERSION>]")
    if ctx["command"]:
        marker = f"$ {ctx['command']}\n"
        i = s.find(marker)
        if i >= 0:
            s = s[: i + len(marker)] + "<OUTPUT>"
        s = s.replace(f"`{ctx['command']}`", "`<COMMAND>`")
        s = s.replace(f"$ {ctx['command']}\n", "$ <COMMAND>\n")
        s = s.replace(f"gate active: {ctx['command']}", "gate active: <COMMAND>")
    return s


def _handle(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("hook_event_name") == "SessionStart":
        return session_start.handle(event)
    return gate.handle(event)


def run_case(name: str, tmp_path: Path) -> tuple[dict[str, Any] | None, int, str, dict[str, str]]:
    """in-process で入口を呼び、(正規化した出力, exit code, 正規化した stderr, ctx) を返す。"""
    ctx = prepare(name, tmp_path)
    golden = load_golden(name)
    event = fill(golden["input"], ctx)
    for _ in range(CASES[name]["warmup"]):
        _handle(event)
    out = dict(_handle(event) or {})
    exit_code = int(out.pop("_exit_code", 0))
    stderr = str(out.pop("_stderr", ""))
    return normalize(out, ctx) or None, exit_code, normalize(stderr, ctx), ctx


@pytest.mark.parametrize("name", sorted(CASES))
def test_ゴールデンが揃っている(name):
    golden = load_golden(name)
    assert set(golden) == {"reference", "checked", "input", "output", "exit_code", "stderr"}, name
    assert golden["reference"].startswith("https://") and len(golden["checked"]) == 10, name
    assert golden["input"]["cwd"] == "<CWD>"


@pytest.mark.parametrize("name", sorted(CASES))
def test_入口の出力はゴールデンと一致する(name, tmp_path):
    golden = load_golden(name)
    out, exit_code, stderr, _ = run_case(name, tmp_path)
    assert out == golden["output"], name
    assert exit_code == golden["exit_code"], name
    assert stderr == golden["stderr"], name


def test_ゴールデンのケースは9本で名前が揃っている():
    files = {p.stem for p in CONTRACTS.glob("*.json")}
    assert files == set(CASES), files ^ set(CASES)
```

Run: `uv run pytest tests/test_contracts.py -q` → FAIL(`FileNotFoundError`、ゴールデンが無い)。

- [ ] **Step 2: ゴールデンを書く**

`tests/contracts/` を作り、次の 9 ファイルを置く。共通: `"reference": "https://code.claude.com/docs/en/hooks"`, `"checked": "2026-08-27"`。以下、`FEEDBACK` = `"[loop-hooks] verification gate failed. Fix it before finishing:\n$ <COMMAND>\n<OUTPUT>"`、`WARN` = `"[loop-hooks] gate failed again; letting this turn end unverified:\n$ <COMMAND>\n<OUTPUT>"`。

`stop-pass.json`:
```json
{
  "reference": "https://code.claude.com/docs/en/hooks",
  "checked": "2026-08-27",
  "input": {"hook_event_name": "Stop", "cwd": "<CWD>", "stop_hook_active": false},
  "output": null,
  "exit_code": 0,
  "stderr": ""
}
```

`stop-fail.json`(`output`):
```json
{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "[loop-hooks] verification gate failed. Fix it before finishing:\n$ <COMMAND>\n<OUTPUT>"}}
```
`input` は `stop-pass` と同じ、`exit_code` 0、`stderr` ""。

`stop-reentry.json`: `input` の `stop_hook_active` を `true`、`output` = `{"systemMessage": "<WARN>"}`(上の WARN 文字列を展開して書く)、exit 0、stderr ""。

`subagent_stop-fail.json`: `input.hook_event_name` = `"SubagentStop"`、`output.hookSpecificOutput.hookEventName` = `"SubagentStop"`、他は `stop-fail` と同じ。

`teammate_idle-fail.json`: `input` = `{"hook_event_name": "TeammateIdle", "cwd": "<CWD>"}`、`output` = `null`、`exit_code` = `2`、`stderr` = FEEDBACK 文字列。

`teammate_idle-repeat.json`: `input` は同じ、`output` = `{"systemMessage": "<WARN>"}`、exit 0、stderr ""。

`session_start-active.json`:
```json
{
  "reference": "https://code.claude.com/docs/en/hooks",
  "checked": "2026-08-27",
  "input": {"hook_event_name": "SessionStart", "cwd": "<CWD>", "source": "startup"},
  "output": {
    "hookSpecificOutput": {
      "hookEventName": "SessionStart",
      "additionalContext": "loop-hooks is active in this repository. When a turn ends and a watched file has changed since the gate last passed, `<COMMAND>` runs from the repository root; if it fails, its output is returned and the turn stays open until it passes. Events: stop, subagent_stop, teammate_idle. Watched: *.ts. Ignored: *.md. Configuration is read from the committed .loop-hooks.json."
    },
    "systemMessage": "[loop-hooks <VERSION>] gate active: <COMMAND>"
  },
  "exit_code": 0,
  "stderr": ""
}
```

`session_start-disabled.json`(準備 `broken` = `{"gate": {"command": ""}}` をコミット): `output` = `{"systemMessage": "[loop-hooks] gate disabled: <ERROR>"}` — `<ERROR>` の正確な文言は `config._validate` が空 command に返す `_error`(`.loop-hooks.json: gate.command …` で始まる)。**一度テストを走らせて assertion の差分から観測した文字列をそのまま書く**(契約は「今こう返す」の記録。文言が妥当か — `.loop-hooks.json` で始まり、`command` に触れている — を確認する)。exit 0、stderr ""。

`session_start-not-git.json`: `output` = `{"systemMessage": "[loop-hooks] gate disabled: not a git repository (<CWD>). loop-hooks uses git to detect changes."}`、exit 0、stderr ""(`prepare` の `no-git` 分岐は `.loop-hooks.json` を置いてから返す — 無いと `config.load` が `None` で出力なしになる)。

- [ ] **Step 3: 走らせて全部通す**

Run: `uv run pytest tests/test_contracts.py -q`
Expected: 19 passed(揃っている 9 + 一致 9 + 名前 1)。落ちた場合、差分がプレースホルダの正規化漏れ(実パス・コマンド)なら `normalize` を直し、入口の文言との差なら **ゴールデンを入口の実際の出力に合わせる**(入口は変えない)。

- [ ] **Step 4: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok(`tests/contracts/*.json` は `.loop-hooks.json` の watch `*.json` に入るので、ゲートも走る — 想定内)。

```bash
git add tests/contracts tests/test_contracts.py
git commit -m "test(contracts): Claude Code とのフック入出力契約をゴールデン 9 本で固定(正規化のうえ完全一致)"
```

---

### Task 6: サブプロセス経路 3 本、ゴールデンの体裁、README

**Files:**
- Modify: `tests/test_contracts.py`(末尾)
- Modify: `tests/test_packaging.py`(末尾)
- Modify: `README.md`(Tests 節)、`README.ja.md`(テスト節)

**Interfaces:**
- Consumes: `CASES`, `prepare`, `load_golden`, `fill`, `normalize`(Task 5)。

- [ ] **Step 1: サブプロセス経路のテストを追記する**

`tests/test_contracts.py` 末尾:

```python
# __main__ 経路(exit code と stderr は入口スクリプトを実際に起動しないと観測できない)。
# 9 本すべてだと 3 秒近く増えるので、形の異なる 3 本に絞る(spec §2.2)。
SUBPROCESS_CASES = ("stop-fail", "teammate_idle-fail", "session_start-active")


@pytest.mark.parametrize("name", SUBPROCESS_CASES)
def test_入口スクリプトの標準出力と終了コードはゴールデンと一致する(name, tmp_path):
    golden = load_golden(name)
    ctx = prepare(name, tmp_path)
    event = fill(golden["input"], ctx)
    entry = "session_start.py" if name.startswith("session_start") else "gate.py"
    r = subprocess.run(
        [sys.executable, str(ROOT / "hooks" / entry)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
    )
    out = normalize(json.loads(r.stdout), ctx) if r.stdout.strip() else None
    assert out == golden["output"], name
    assert r.returncode == golden["exit_code"], name
    assert normalize(r.stderr.rstrip("\n"), ctx) == golden["stderr"], name
```

Run: `uv run pytest tests/test_contracts.py -q` → 22 passed。`time uv run pytest tests/test_contracts.py -q` の実時間を報告に記録(目安 2 秒以内)。

- [ ] **Step 2: ゴールデンの体裁を packaging テストで固定する**

`tests/test_packaging.py` 末尾:

```python
def test_契約ゴールデンは参照URLと確認日を持つ():
    """公式リファレンスの変更検知は手動(第 5 段階 spec §2.2)。辿れる印だけは必ず残す。"""
    import re

    files = sorted((ROOT / "tests" / "contracts").glob("*.json"))
    assert len(files) == 9, [f.name for f in files]
    for f in files:
        golden = json.loads(f.read_text(encoding="utf-8"))
        assert golden["reference"] == "https://code.claude.com/docs/en/hooks", f.name
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", golden["checked"]), f.name
        assert re.fullmatch(r"(stop|subagent_stop|teammate_idle|session_start)-[a-z-]+", f.stem)
```

Run: `uv run pytest tests/test_packaging.py -q` → 全部 passed。

- [ ] **Step 3: README**

`README.md` の Tests 節(`tests/test_properties.py holds hypothesis property tests …` の段落の直後)に:

```
`tests/contracts/*.json` are golden files for the hook I/O contract with Claude Code (input event →
output JSON, exit code, stderr), checked by `tests/test_contracts.py`. When the contract changes,
edit the golden by hand and update its `checked` date; there is no auto-update switch.
`tests/test_architecture.py` pins structural rules (entry-file imports, gate/status decision
parity, state written outside the repository, `hooks/lib` never raising).
```

`README.ja.md` の対応箇所に:

```
`tests/contracts/*.json` は Claude Code とのフック入出力契約(入力イベント → 出力 JSON・終了コード・
stderr)のゴールデンで、`tests/test_contracts.py` が検査する。契約が変わったらゴールデンを手で直し、
`checked` の日付を更新する(自動更新の仕組みは無い)。`tests/test_architecture.py` は構造の規則
(入口の import、gate と status の判定一致、状態がリポジトリ外に書かれること、`hooks/lib` が例外を
出さないこと)を固定する。
```

- [ ] **Step 4: 検証・コミット**

Run: `uv run python scripts/verify.py quick` → ok。

```bash
git add tests/test_contracts.py tests/test_packaging.py README.md README.ja.md
git commit -m "test(contracts): 入口スクリプトの exit code と stderr を 3 本で検査し、ゴールデンの体裁と運用を固定"
```

---

### Task 7: 所要時間の確認、文書、0.7.0

**Files:**
- Modify: `CLAUDE.md`, `CHANGELOG.md`, `pyproject.toml`, `.claude-plugin/plugin.json`, `uv.lock`, spec §3

- [ ] **Step 1: 所要時間**

Run: `time uv run python scripts/verify.py quick` を 2 回、`time uv run python scripts/verify.py all; echo exit=$?` を 1 回。
Expected: quick ≤ 16.3 秒、all exit 0、`tests/mutation-baseline.json` に差分なし(`git diff --exit-code tests/mutation-baseline.json`。差分が出たら `git checkout` で戻す — `hooks/lib` を変えていないので上がらないはず。上がったなら報告に書く)。

- [ ] **Step 2: 文書とバージョン**

- `CLAUDE.md` 開発節の `quick` の説明行(「`quick` は CI の `test` ジョブと同じ 6 チェック…」)の次に 1 行:
  `- 構造の規則は \`tests/test_architecture.py\`、Claude Code との入出力契約は \`tests/contracts/\`(ゴールデン、手で更新)。入口の文言を変えたらゴールデンも直す`
- `CHANGELOG.md` 先頭:

```markdown
## [0.7.0] - 2026-08-27

### Added
- **Architecture tests** (`tests/test_architecture.py`): entry files import only `hooks.lib`
  (and never `status` at module level); `gate.handle` and `status.collect` make the same
  decision for the same repository state; state and log files stay under the plugin data
  directory whatever form the repository path takes (trailing slash, `..`, symlink); every
  public function in `hooks/lib` degrades instead of raising when the data directory is not
  writable, `git` is missing, or paths do not exist.
- **Hook I/O contract goldens** (`tests/contracts/*.json`, `tests/test_contracts.py`): the
  exact input/output JSON, exit code and stderr for Stop / SubagentStop / TeammateIdle /
  SessionStart, normalised and compared as whole dictionaries. Each golden records the
  reference URL and the date it was checked.

### Upgrading
- Nothing to do. No entry-point files or hook definitions changed; no restart needed.
```

- バージョン `0.7.0`: `pyproject.toml` の `version`、`.claude-plugin/plugin.json` の `version`、`uv lock`。
- spec `docs/superpowers/specs/2026-08-27-phase5-architecture-contracts-design.md` §3 の末尾に「確認済み(2026-08-27): quick N 秒(0.6.0 比 +M 秒)/ all K 秒 / 発見した欠陥(あれば)」。

- [ ] **Step 3: 全体検証・コミット**

Run: `uv run python scripts/verify.py all; echo exit=$?` → exit=0。

```bash
git add CLAUDE.md CHANGELOG.md pyproject.toml .claude-plugin/plugin.json uv.lock docs
git commit -m "chore: 0.7.0 のリリース準備(アーキテクチャ/契約テストを文書化)"
```

---

### Task 8: 受け入れ(コントローラが行う)

- 最終レビュー → マージ → 公開前チェック → push → CI 緑 → 親 spec の第 5 段階行を「完了(0.7.0)」に → 計画を `docs/superpowers/archive/plans/` へ。
