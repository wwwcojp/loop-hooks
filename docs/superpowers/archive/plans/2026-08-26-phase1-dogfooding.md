# 第 1 段階 — 決定論的ゲート(ドッグフーディング)+ 0.3.1 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** loop-hooks 自身に loop-hooks のゲートを掛け(`scripts/verify.py quick` + `.loop-hooks.json` + CLAUDE.md の規約)、0.3.0 spec に列挙された既知の小欠陥 5 件を直して 0.3.1 としてリリースする。

**Architecture:** verify ランナーは `scripts/verify.py`(stdlib のみ、`hooks/` を import しない)で、`quick` ステージ = CI と同じ 3 コマンド(leak → ruff → pytest)。`tests/test_verify.py` が `ci.yml` を読んでランナーと 1 対 1 で一致することを検査する。欠陥修正はすべて `hooks/lib` と `hooks/gate.py` の関数内で完結し、入口ファイルの場所・import は動かさない。

**Tech Stack:** Python 3.10+、uv、pytest、ruff。追加依存なし。

**Spec:** `docs/superpowers/specs/2026-08-26-verification-roadmap-design.md` §2(第 1 段階)。欠陥の出所は `docs/superpowers/specs/2026-08-26-0.3.0-observability-design.md` の「0.3.1 候補」。

## Global Constraints

- `requires-python = ">=3.10"`。CI は 3.10 と 3.14 の両方で回る(3.10 で動かない構文を使わない)。
- `hooks/gate.py` / `hooks/session_start.py` / `hooks/hooks.json` の**場所と import を動かさない**(稼働中セッションのフック登録を壊す)。関数の追加・分割は可。
- `hooks/lib/*` の公開関数は**例外を外に出さない**。ログ・状態の書込失敗はゲートの判定に影響させない。
- テストは `tests/conftest.py` の autouse fixture で `CLAUDE_PLUGIN_DATA` が tmp に隔離される。実利用者の状態ディレクトリに触るテストを書かない。
- テスト名は既存の流儀(日本語の `test_…` 関数名)に合わせる。
- ソース・コミットメッセージに実ホームパスを書かない(CI の leak チェックが落ちる)。プレースホルダーは `/home/USER`。
- 実行コマンドは常にリポジトリルートから `uv run …`。
- コミットは日本語の Conventional Commits(既存ログ参照: `feat(log): …` / `fix: …` / `docs: …` / `test: …`)。

---

## ファイル構成

| ファイル | 責務 | 変更 |
|---|---|---|
| `scripts/verify.py` | 検証ランナー。`quick` ステージ。stdlib のみ | 新規 |
| `tests/test_verify.py` | ランナーの動作と CI ミラー検査 | 新規 |
| `.github/workflows/ci.yml` | `ruff check` の対象に `scripts` を追加 | 変更 |
| `.loop-hooks.json` | 自リポジトリのゲート設定 | 新規 |
| `CLAUDE.md` | ドッグフーディング規約 | **新規**(現状存在しない) |
| `hooks/gate.py` | fp が None のとき安全側に倒す / `--status` の本体を関数化 | 変更 |
| `hooks/lib/state.py` | `_write` の例外処理 | 変更 |
| `hooks/lib/log.py` | `_trim` の原子性 | 変更 |
| `hooks/lib/status.py` | `recent` に直近の `ran` を必ず含める | 変更 |
| `tests/test_gate.py` / `test_state.py` / `test_log.py` / `test_status.py` / `test_packaging.py` | 各修正の固定 | 変更 |
| `pyproject.toml` / `.claude-plugin/plugin.json` / `uv.lock` / `CHANGELOG.md` | 0.3.1 | 変更 |

---

### Task 1: verify ランナー `scripts/verify.py` と CI ミラーテスト

**Files:**
- Create: `scripts/verify.py`
- Create: `tests/test_verify.py`
- Modify: `.github/workflows/ci.yml`(Lint ステップの引数に `scripts` を追加)
- Modify: `pyproject.toml`(`[tool.pytest.ini_options]` に `pythonpath = ["scripts"]`)

**Interfaces:**
- Produces: `verify.STAGES: dict[str, list[Check]]`、`verify.Check(name, cmd, ok_codes)`、`verify.LEAK_REGEX: str`、`verify.run_stage(stage, checks=None, repo_root=REPO_ROOT) -> bool`、`verify.main(argv) -> int`。Task 2 の `.loop-hooks.json` と Task 9 が `uv run python scripts/verify.py quick` として呼ぶ。

- [ ] **Step 1: CI の Lint ステップに `scripts` を足す**

`.github/workflows/ci.yml` の

```yaml
      - name: Lint
        run: uv run ruff check hooks tests
```

を

```yaml
      - name: Lint
        run: uv run ruff check hooks tests scripts
```

に変える。

- [ ] **Step 2: pytest から `scripts/` を import できるようにする**

`pyproject.toml` の `[tool.pytest.ini_options]` を

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["scripts"]
```

にする。

- [ ] **Step 3: 失敗するテストを書く**

`tests/test_verify.py`:

```python
"""scripts/verify.py: quick ステージは CI と同じコマンド・同じ順序。失敗で非ゼロ。"""
import re
from pathlib import Path

import verify

REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract_ci_run_steps(ci_yaml: str) -> list[str]:
    """ci.yml の `run:` 本文を出現順に取り出す(YAML パーサを足さないための最小実装)。

    `run: |` のブロックはインデントが戻るまで、`run: cmd` は 1 行。
    """
    lines = ci_yaml.splitlines()
    steps: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, rest = len(m.group(1)), m.group(2)
        if rest.strip() != "|":
            steps.append(rest)
            i += 1
            continue
        body = []
        i += 1
        while i < len(lines) and (
            not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent
        ):
            body.append(lines[i])
            i += 1
        steps.append("\n".join(body))
    return steps


def test_quick_stage_mirrors_ci():
    """spec §2.1: quick は CI と同じコマンド・同じ順序。片方を変えたらもう片方も変える。

    両方向で検査する: ランナー側のコマンドを固定し、CI の run ステップを全数抽出して
    1 対 1・順序も一致させる。CI にステップが増えても、フラグが変わっても落ちる。
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert [c.name for c in verify.STAGES["quick"]] == ["leak", "lint", "tests"]
    leak, lint, tests = verify.STAGES["quick"]
    assert leak.cmd[:3] == ["git", "grep", "-nP"] and leak.ok_codes == frozenset({1})
    assert lint.cmd == ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]
    assert tests.cmd == ["uv", "run", "pytest", "-q"]

    run_steps = _extract_ci_run_steps(ci)
    assert len(run_steps) == 3, f"CI の run ステップ数が想定と違う: {run_steps!r}"
    leak_step, lint_step, tests_step = run_steps
    assert f"git grep -nP '{verify.LEAK_REGEX}' --" in leak_step
    assert re.search(r"\bif\b.*\bexit 1\b", leak_step, re.S), leak_step
    assert lint_step.strip() == "uv run ruff check hooks tests scripts"
    assert tests_step.strip() == "uv run pytest -q"


def test_全チェックが成功すればTrue(tmp_path):
    checks = [verify.Check("a", ["true"]), verify.Check("b", ["true"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is True


def test_最初の失敗で打ち切りFalse(tmp_path, capsys):
    checks = [verify.Check("a", ["true"]), verify.Check("b", ["false"]),
              verify.Check("c", ["sh", "-c", "echo SHOULD_NOT_RUN"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is False
    assert "SHOULD_NOT_RUN" not in capsys.readouterr().out


def test_ok_codesで成功扱いの終了コードを反転できる(tmp_path):
    checks = [verify.Check("grep-no-match", ["false"], ok_codes=frozenset({1}))]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is True


def test_失敗したチェックの出力を表示する(tmp_path, capsys):
    checks = [verify.Check("boom", ["sh", "-c", "echo DETAIL; exit 3"])]
    verify.run_stage("quick", checks, repo_root=tmp_path)
    out = capsys.readouterr().out
    assert "DETAIL" in out and "boom" in out


def test_実行できないコマンドは失敗扱い(tmp_path):
    checks = [verify.Check("missing", ["/nonexistent/command"])]
    assert verify.run_stage("quick", checks, repo_root=tmp_path) is False


def test_mainは未知のステージで2を返す():
    assert verify.main(["nope"]) == 2


def test_mainは成功で0失敗で1(monkeypatch):
    monkeypatch.setitem(verify.STAGES, "ok", [verify.Check("a", ["true"])])
    monkeypatch.setitem(verify.STAGES, "ng", [verify.Check("a", ["false"])])
    assert verify.main(["ok"]) == 0
    assert verify.main(["ng"]) == 1
```

- [ ] **Step 4: 落ちることを確認する**

Run: `uv run pytest tests/test_verify.py -q`
Expected: 収集時に `ModuleNotFoundError: No module named 'verify'` で ERROR。

- [ ] **Step 5: ランナーを書く**

`scripts/verify.py`:

```python
"""検証ランナー。チェックを順に実行し、最初の失敗で止まる。

loop-hooks の Stop ゲートから `uv run python scripts/verify.py quick` として呼ばれる。
`quick` の中身は CI(.github/workflows/ci.yml)と同じコマンド・同じ順序に保つこと
(tests/test_verify.py::test_quick_stage_mirrors_ci が両方向で一致を検査する)。

evidence は書かない。「走ったか・なぜ走らなかったか」はプラグイン側の判定ログ
(`/loop-hooks:status`)が持つ。ここは終了コードと出力だけを返す。
stdlib のみ。hooks/ は import しない(ゲート対象とゲート実行者を混ぜない)。
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# CI の「実ホームパスのリークチェック」と同一。変えるときは ci.yml も変える
LEAK_REGEX = r"/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*"

REPO_ROOT = Path(__file__).resolve().parent.parent
FAIL_OUTPUT_TAIL = 4000


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    # 終了コードがこの集合に含まれれば成功。git grep は「不一致=1」が成功なので反転に使う
    ok_codes: frozenset[int] = frozenset({0})


STAGES: dict[str, list[Check]] = {
    "quick": [
        Check("leak", ["git", "grep", "-nP", LEAK_REGEX, "--"], ok_codes=frozenset({1})),
        Check("lint", ["uv", "run", "ruff", "check", "hooks", "tests", "scripts"]),
        Check("tests", ["uv", "run", "pytest", "-q"]),
    ],
}


def _run(check: Check, repo_root: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(check.cmd, cwd=repo_root, capture_output=True, text=True)
    except OSError as exc:
        return False, f"could not run: {exc}"
    return r.returncode in check.ok_codes, (r.stdout or "") + (r.stderr or "")


def run_stage(
    stage: str, checks: Sequence[Check] | None = None, repo_root: Path = REPO_ROOT
) -> bool:
    """チェックを順に実行し、最初の失敗で打ち切る。成否を返す。"""
    checks = STAGES[stage] if checks is None else checks
    for check in checks:
        ok, out = _run(check, repo_root)
        if ok:
            print(f"[verify] {check.name}: ok")
            continue
        print(f"[verify] {check.name}: FAIL")
        print(f"$ {' '.join(check.cmd)}")
        print(out[-FAIL_OUTPUT_TAIL:])
        return False
    return True


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1 or argv[0] not in STAGES:
        print(f"usage: verify.py {{{'|'.join(STAGES)}}}", file=sys.stderr)
        return 2
    return 0 if run_stage(argv[0]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 6: テストが通ることを確認する**

Run: `uv run pytest tests/test_verify.py -q`
Expected: 8 passed。

- [ ] **Step 7: 実際に quick を回す**

Run: `time uv run python scripts/verify.py quick; echo exit=$?`
Expected: `[verify] leak: ok` / `lint: ok` / `tests: ok`、`exit=0`。所要時間をメモする(Task 9 Step 5 で spec に記録する)。

- [ ] **Step 8: 全体テストと lint**

Run: `uv run ruff check hooks tests scripts && uv run pytest -q`
Expected: 全件 pass。

- [ ] **Step 9: コミット**

```bash
git add scripts/verify.py tests/test_verify.py .github/workflows/ci.yml pyproject.toml
git commit -m "feat: 検証ランナー scripts/verify.py(quick = CI と同じ 3 コマンド)"
```

---

### Task 2: 自リポジトリのゲート設定とドッグフーディング規約

**Files:**
- Create: `.loop-hooks.json`
- Create: `CLAUDE.md`
- Modify: `tests/test_packaging.py`(設定ファイルの妥当性)

**Interfaces:**
- Consumes: `uv run python scripts/verify.py quick`(Task 1)、`config.load(root) -> dict | None`(既存。`{"gate": {...}}` を返し、エラーなら `_error` キー)。`tests/test_packaging.py` には `ROOT = Path(__file__).resolve().parent.parent` が既にある。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_packaging.py` の末尾に追加:

```python
def test_自リポジトリのゲート設定が有効で検証ランナーを指す():
    """spec §2.2: loop-hooks 自身にゲートを掛ける(ドッグフーディング)。"""
    import sys
    sys.path.insert(0, str(ROOT / "hooks"))
    from lib import config
    cfg = config.load(str(ROOT))
    assert cfg is not None and "_error" not in cfg, cfg
    gate = cfg["gate"]
    assert gate["command"] == "uv run python scripts/verify.py quick"
    assert "*.py" in gate["watch"] and "skills/**/*.md" in gate["watch"]
    assert "docs/*" in gate["ignore"]
```

- [ ] **Step 2: 落ちることを確認する**

Run: `uv run pytest tests/test_packaging.py -q -k 自リポジトリ`
Expected: FAIL(`cfg is None`)。

- [ ] **Step 3: 設定ファイルを置く**

`.loop-hooks.json`:

```json
{
  "gate": {
    "command": "uv run python scripts/verify.py quick",
    "timeout_sec": 120,
    "watch": ["*.py", "*.json", "*.toml", "skills/**/*.md"],
    "ignore": [".superpowers/*", "docs/*"]
  }
}
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest tests/test_packaging.py -q`
Expected: pass。`config.load` は HEAD 版を優先するが、HEAD に無い場合は作業ツリーを読む(一度きりの通知つき)ので、コミット前でも通る。

- [ ] **Step 5: CLAUDE.md を書く**

`CLAUDE.md`(新規):

```markdown
# loop-hooks

Claude Code のフックプラグイン。ターン終了時にリポジトリの検証コマンドを実行し、
失敗ならターンを終わらせない。設計の背景は `README.md` の "Where it fits"、
仕様は `docs/superpowers/specs/`。

## 自リポジトリでの作業時の注意(ドッグフーディング)

このリポジトリには loop-hooks 自身のゲートが掛かっている(`.loop-hooks.json` →
`uv run python scripts/verify.py quick`)。想定内の挙動なので、以下で扱う。

1. **セッションで有効なプラグインは GitHub 版(marketplace `source: github`)。**
   作業ツリーの `hooks/` を編集してもゲートの挙動は変わらない。作業ツリーのコードは
   verify ランナー経由の pytest でだけ実行される。`directory` ソースで自インストールして
   動作確認したら、終わったら GitHub 版に戻して Claude Code を再起動する。
2. **入口ファイル(`hooks/gate.py`・`hooks/session_start.py`・`hooks/hooks.json`)を
   動かさない。** フック定義はセッション開始時のスナップショットなので、動かすと稼働中の
   セッションでゲートが無言で消える。動かす場合はリリースノートに「再起動が必要」と書く。
3. **ゲートで止められたらコードを直す。** `.loop-hooks.json` を変えて通さない、
   `disableAllHooks` を使わない。設定は HEAD 版が優先されるので、作業ツリーで書き換えても
   ゲートは変わらない(0.2.1)。
4. **プラグインを更新したら Claude Code を再起動する。** 再起動後の最初のセッションで
   `[loop-hooks] gate active: uv run python scripts/verify.py quick` が出ることが、
   更新が効いた確認。出なければ `/loop-hooks:status`。
5. `quick` は CI と同じ 3 コマンド(leak → ruff → pytest)。CI を変えるときは
   `scripts/verify.py` も変える(`tests/test_verify.py::test_quick_stage_mirrors_ci` が検出する)。

## 開発

- テスト: `uv run pytest -q`(`tests/conftest.py` が状態ディレクトリを tmp に隔離する)
- 検証一式: `uv run python scripts/verify.py quick`
- 状態の確認: `uv run hooks/gate.py --status .` または `/loop-hooks:status`
- 実ホームパスをソース・コミットメッセージに書かない(CI が落ちる)。プレースホルダーは `/home/USER`
```

- [ ] **Step 6: 全体検証**

Run: `uv run python scripts/verify.py quick; echo exit=$?`
Expected: `exit=0`。

- [ ] **Step 7: コミット**

```bash
git add .loop-hooks.json CLAUDE.md tests/test_packaging.py
git commit -m "feat: 自リポジトリにゲートを掛ける(.loop-hooks.json)とドッグフーディング規約"
```

---

### Task 3: fix — フィンガープリントが取れないとき(git 失敗)に無言で skipped にしない

**Files:**
- Modify: `hooks/gate.py`(`handle` 内の `current` 判定)
- Test: `tests/test_gate.py`

**背景:** `fingerprint.compute` は git が失敗すると `None` を返す。`handle` は
`current == state.read_verified(root)` で比較するため、検証記録も無い(`None`)リポジトリでは
`None == None` が真になり **`skipped` を記録してゲートを走らせない**。「静かな失敗を無くす」に反する。

**方針:** `current is None` なら**ゲートを走らせる**(安全側)。pass しても `verified` は書かない
(既存コードは `verified is not None` を見ているので変更不要)。ログに `note: "fingerprint unavailable"` を残す。

**Interfaces:**
- Consumes: `tests/test_gate.py` の `setup_repo(tmp_path, command) -> dict`(Stop イベント)、`blocked(out) -> str | None`、`log.tail(root, n)`、`state.read_verified(root)`。`gate` / `fingerprint` / `log` / `state` は同ファイル冒頭で import 済み。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_gate.py` の末尾に追加:

```python
def test_fingerprintが取れなければ安全側でゲートを走らせる(tmp_path, monkeypatch):
    """0.3.1: git 失敗で fp が None のとき、None == None(未検証)で skipped になっていた。"""
    event = setup_repo(tmp_path, "false")
    monkeypatch.setattr(fingerprint, "compute", lambda root, cfg: None)
    out = gate.handle(event)
    assert blocked(out) is not None
    rec = log.tail(str(tmp_path), 1)[0]
    assert rec["decision"] == "ran" and rec["note"] == "fingerprint unavailable"


def test_fingerprintが取れないままpassしてもverifiedを書かない(tmp_path, monkeypatch):
    event = setup_repo(tmp_path, "true")
    monkeypatch.setattr(fingerprint, "compute", lambda root, cfg: None)
    assert gate.handle(event) is None
    assert state.read_verified(str(tmp_path)) is None
```

- [ ] **Step 2: 落ちることを確認する**

Run: `uv run pytest tests/test_gate.py -q -k fingerprintが取れ`
Expected: 1 件目 FAIL(`blocked(out) is None`、decision が `skipped`)。2 件目は現状でも pass してよい。

- [ ] **Step 3: 実装する**

`hooks/gate.py` の `handle` 内

```python
    current = fingerprint.compute(root, gate_cfg)
    rec["fp"] = (current or "")[:12]
    if current == state.read_verified(root):
        log.append(root, {**rec, "decision": "skipped"})
        return None  # 前回グリーンから何も変わっていない
```

を

```python
    current = fingerprint.compute(root, gate_cfg)
    rec["fp"] = (current or "")[:12]
    if current is None:
        # git が観測できない。skipped に倒すと無言でゲートが消えるので、走らせる側に倒す
        rec["note"] = "fingerprint unavailable"
    elif current == state.read_verified(root):
        log.append(root, {**rec, "decision": "skipped"})
        return None  # 前回グリーンから何も変わっていない
```

に変える。後段の

```python
    if cfg.get("_notice"):
        rec["note"] = cfg["_notice"][:80]
```

は両方残るように

```python
    if cfg.get("_notice"):
        rec["note"] = "; ".join(filter(None, [rec.get("note"), cfg["_notice"][:80]]))
```

にする。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest tests/test_gate.py -q`
Expected: 全件 pass。既存テストが `None` 比較の旧挙動(skipped)を期待して落ちたら、そのテストの意図を読んで期待値を「走る」に直す(`test_status.py::test_fingerprintがNoneならverifiedと比較してwill_run` は status 側の話なので触らない)。

- [ ] **Step 5: コミット**

```bash
git add hooks/gate.py tests/test_gate.py
git commit -m "fix(gate): fp が取れないときは skipped にせずゲートを走らせる"
```

---

### Task 4: fix — `state._write` が書込失敗で例外を出さない

**Files:**
- Modify: `hooks/lib/state.py:_write`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `state.write_verified(root, fp)`、`state.read_verified(root)`。`state` は同ファイルで import 済み。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_state.py` の末尾に追加:

```python
def test_書き込めなくても例外を出さない(tmp_path, monkeypatch):
    """0.3.1: lib は例外を外に出さない。状態が書けない環境でもゲートは動く。"""
    blocked_dir = tmp_path / "file-not-dir"
    blocked_dir.write_text("x", encoding="utf-8")  # ファイルなのでその下にディレクトリを作れない
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(blocked_dir))
    state.write_verified("/home/USER/repo", "abc")  # 例外にならない
    assert state.read_verified("/home/USER/repo") is None
```

- [ ] **Step 2: 落ちることを確認する**

Run: `uv run pytest tests/test_state.py -q -k 書き込めなくても`
Expected: FAIL(`NotADirectoryError` または `FileExistsError`)。

- [ ] **Step 3: 実装する**

`hooks/lib/state.py` の `_write` を

```python
def _write(root: str, key: str, fingerprint: str) -> None:
    """書込失敗は握る。状態が残せなくてもゲートの判定は続行する(次回また走るだけ)。"""
    try:
        data = _read(root)
        data["root"] = root  # どのリポジトリの記録か辿れるように残す
        data[key] = fingerprint
        p = _path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass
```

にする。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest tests/test_state.py -q`
Expected: pass。

- [ ] **Step 5: コミット**

```bash
git add hooks/lib/state.py tests/test_state.py
git commit -m "fix(state): 書込失敗で例外を出さない"
```

---

### Task 5: fix — `log._trim` を原子的にする

**Files:**
- Modify: `hooks/lib/log.py:_trim`
- Test: `tests/test_log.py`

**背景:** `p.write_text(...)` で切詰めると、書込中にプロセスが落ちた場合や並行セッションが
同時に `append` した場合にログが途中で切れる。一時ファイルに書いて `os.replace` で差し替える。

**Interfaces:**
- Consumes: `log.append(root, record)`、`log.tail(root, n)`、`log.MAX_LINES`、`log.KEEP_LINES`、`log._path(root)`。`log` は同ファイルで import 済み。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_log.py` の末尾に追加:

```python
def test_切詰めは一時ファイル経由で差し替える(monkeypatch):
    """0.3.1: 途中で落ちてもログが欠けないよう、書いてから os.replace する。"""
    import os
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(log.os, "replace", spy)
    root = "/home/USER/repo-trim"
    for i in range(log.MAX_LINES + 1):
        log.append(root, {"event": "Stop", "decision": "skipped", "i": i})
    assert replaced, "os.replace が呼ばれていない"
    assert replaced[-1][1] == str(log._path(root))
    assert not log._path(root).with_suffix(".jsonl.tmp").exists()
    recs = log.tail(root, log.KEEP_LINES + 10)
    assert len(recs) == log.KEEP_LINES and recs[0]["i"] == log.MAX_LINES
```

- [ ] **Step 2: 落ちることを確認する**

Run: `uv run pytest tests/test_log.py -q -k 切詰め`
Expected: FAIL(`AttributeError: module 'lib.log' has no attribute 'os'`)。

- [ ] **Step 3: 実装する**

`hooks/lib/log.py`:

`import json` の下に `import os` を追加し、`_trim` を

```python
def _trim(p: Path) -> None:
    """上限を超えたら直近 KEEP_LINES 行に切り詰める。一時ファイルに書いて差し替える(原子的)。"""
    lines = p.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_LINES:
        return
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")
    os.replace(tmp, p)
```

にする。`append` の `except (OSError, TypeError, ValueError)` は既存のまま(`_trim` の失敗も握られる)。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest tests/test_log.py -q`
Expected: pass(既存の `test_上限を超えたら直近だけ残す` も引き続き pass)。

- [ ] **Step 5: コミット**

```bash
git add hooks/lib/log.py tests/test_log.py
git commit -m "fix(log): 切詰めを一時ファイル経由の差し替えにして原子的にする"
```

---

### Task 6: fix — `status` の `recent` が `skipped` で埋まって直近の実行が見えない

**Files:**
- Modify: `hooks/lib/status.py:collect`
- Test: `tests/test_status.py`

**背景:** `recent` は `log.tail(root, 5)` そのままなので、ターンを何度も終えると直近 5 件が全部
`skipped` になり「最後に走ったのはいつで、結果は何か」が見えない(0.3.0 spec §5.3 の未解決項目)。

**方針:** 直近 5 件に加えて、**最新の `ran` 記録**が 5 件に含まれていなければ末尾に足す。
`log.tail(root, 200)` から探す(200 件以内に `ran` が無ければ諦める)。

**Interfaces:**
- Produces: `status.collect(cwd)["recent"]` は最大 `RECENT + 1` 件。`status.RECENT_SEARCH = 200`。`render` は既存のまま(行数が 1 増えるだけ)。
- Consumes: `tests/test_status.py` の `repo(tmp_path, commit_config=True) -> Path`(設定をコミット済みの git リポジトリを作る)。`status` / `log` は同ファイルで import 済み(無ければ `from lib import log` を足す)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_status.py` の末尾に追加:

```python
def test_recentには最新のran記録が必ず含まれる(tmp_path):
    """0.3.1: skipped が 5 件続いても、最後に走った結果が status から消えない。"""
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "fail", "ms": 1200})
    for _ in range(8):
        log.append(root, {"event": "Stop", "decision": "skipped"})
    recent = status.collect(root)["recent"]
    assert len(recent) == status.RECENT + 1
    assert [r["decision"] for r in recent[:status.RECENT]] == ["skipped"] * status.RECENT
    assert recent[-1]["decision"] == "ran" and recent[-1]["result"] == "fail"


def test_recentに既にranがあれば重複して足さない(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "pass", "ms": 10})
    log.append(root, {"event": "Stop", "decision": "skipped"})
    recent = status.collect(root)["recent"]
    assert [r["decision"] for r in recent] == ["skipped", "ran"]
```

- [ ] **Step 2: 落ちることを確認する**

Run: `uv run pytest tests/test_status.py -q -k recent`
Expected: 1 件目 FAIL(`len(recent) == 5`)。

- [ ] **Step 3: 実装する**

`hooks/lib/status.py` の `RECENT = 5` の下に追加:

```python
RECENT_SEARCH = 200  # 最新の ran をこの範囲まで遡って探す


def _recent(root: str) -> list[dict]:
    """直近 RECENT 件。その中に ran が無ければ、最新の ran を末尾に 1 件足す。"""
    records = log.tail(root, RECENT_SEARCH)
    recent = records[:RECENT]
    if any(r.get("decision") == "ran" for r in recent):
        return recent
    last_ran = next((r for r in records[RECENT:] if r.get("decision") == "ran"), None)
    return recent + [last_ran] if last_ran else recent
```

`collect` の `"recent": log.tail(root or cwd, RECENT),` を `"recent": _recent(root or cwd),` に変える。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest tests/test_status.py tests/test_gate.py -q`
Expected: pass。

- [ ] **Step 5: 表示を目で確認する**

Run: `uv run hooks/gate.py --status .`
Expected: `recent` に直近の判定が並ぶ(このリポジトリで Task 2 以降にゲートが走っていれば `ran` の行が見える)。

- [ ] **Step 6: コミット**

```bash
git add hooks/lib/status.py tests/test_status.py
git commit -m "fix(status): recent が skipped で埋まっても最新の ran を表示する"
```

---

### Task 7: test — `--status` の例外ガードを強制例外で固定する

**Files:**
- Modify: `hooks/gate.py`(`__main__` の `--status` 分岐を関数 `status_main(target) -> int` に出す)
- Test: `tests/test_gate.py`

**背景:** `--status` は `try/except Exception` で「表示ツールは落ちない」を保証しているが、
`__main__` 直下にあるためテストできない。関数に出して monkeypatch で例外を注入する。
入口ファイルの**場所と import は変えない**(関数追加のみ)。

**Interfaces:**
- Produces: `gate.status_main(target: str) -> int`(常に 0 を返し、出力は stdout)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_gate.py` の末尾に追加:

```python
def test_statusは内部で例外が出ても落ちずに0で終わる(monkeypatch, capsys):
    """0.3.1: 表示ツールであって判定ツールではない。例外でも exit 0 と説明文。"""
    from lib import status

    def boom(target):
        raise RuntimeError("injected")

    monkeypatch.setattr(status, "collect", boom)
    assert gate.status_main("/home/USER/anything") == 0
    out = capsys.readouterr().out
    assert "loop-hooks status unavailable" in out and "injected" in out


def test_statusは正常時にrenderの結果を出す(tmp_path, capsys):
    assert gate.status_main(str(tmp_path)) == 0
    assert capsys.readouterr().out.startswith("loop-hooks status")
```

- [ ] **Step 2: 落ちることを確認する**

Run: `uv run pytest tests/test_gate.py -q -k statusは`
Expected: FAIL(`AttributeError: module 'gate' has no attribute 'status_main'`)。

- [ ] **Step 3: 実装する**

`hooks/gate.py` の `if __name__ == "__main__":` の**直前**に追加:

```python
def status_main(target: str) -> int:
    """`--status` の本体。表示ツールであって判定ツールではない: stdin を読まず、常に 0。"""
    from lib import status  # 表示専用。ゲート経路では読み込まない(0.3.0 spec §2)
    try:
        print(status.render(status.collect(target)))
    except Exception as exc:  # 表示が落ちてもゲートには関係ない
        print(f"loop-hooks status unavailable: {exc}")
    return 0
```

`__main__` 側の

```python
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        from lib import status  # 表示専用。ゲート経路では読み込まない(item 3)
        # 表示ツールであって判定ツールではない。stdin は読まず、常に exit 0。
        target = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
        try:
            print(status.render(status.collect(target)))
        except Exception as exc:  # 表示ツールであって判定ツールではない
            print(f"loop-hooks status unavailable: {exc}")
        sys.exit(0)
```

を

```python
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        sys.exit(status_main(sys.argv[2] if len(sys.argv) > 2 else os.getcwd()))
```

に置き換える。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest tests/test_gate.py -q && uv run hooks/gate.py --status . >/dev/null; echo exit=$?`
Expected: pass、`exit=0`。

- [ ] **Step 5: コミット**

```bash
git add hooks/gate.py tests/test_gate.py
git commit -m "test(gate): --status の例外ガードを関数化して強制例外で固定する"
```

---

### Task 8: 0.3.1 リリース準備

**Files:**
- Modify: `pyproject.toml`(`version = "0.3.1"`)
- Modify: `.claude-plugin/plugin.json`(`"version": "0.3.1"`)
- Modify: `uv.lock`(`uv lock` で同期)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `tests/test_packaging.py::test_pyprojectとplugin_jsonのバージョンが一致する`(既存)が不一致を検出する。

- [ ] **Step 1: バージョンを上げる**

`pyproject.toml` の `version = "0.3.0"` → `"0.3.1"`、`.claude-plugin/plugin.json` の `"version": "0.3.0"` → `"0.3.1"`。

Run: `uv lock && git diff --stat uv.lock && uv run pytest tests/test_packaging.py -q`
Expected: `uv.lock` の `loop-hooks` の version 行だけが変わる。packaging テスト pass。

- [ ] **Step 2: CHANGELOG を書く**

`CHANGELOG.md` の `## [0.3.0] - 2026-08-26` の**上**に追加(日付は実施日):

```markdown
## [0.3.1] - 2026-08-27

### Added
- **The plugin now gates its own repository** (`.loop-hooks.json` → `uv run python
  scripts/verify.py quick`, the same three commands as CI: home-path leak check, ruff,
  pytest). `tests/test_verify.py` keeps the runner and `ci.yml` in lockstep.
  Dogfooding rules for contributors are in `CLAUDE.md`.

### Fixed
- **A git failure no longer silently disables the gate.** When the fingerprint cannot
  be computed, the gate runs the verification command instead of recording `skipped`
  (`None == None` matched an unverified repository). The decision log notes
  `fingerprint unavailable`, and a pass in that state does not record `verified`.
- **`/loop-hooks:status` always shows the latest `ran` decision**, even when the last
  five decisions are all `skipped`. It is appended as a sixth line when needed.
- **State writes never raise.** A state directory that cannot be created is ignored;
  the gate simply runs again next time.
- **Decision-log trimming is atomic** (write to a temp file, then `os.replace`), so a
  crash or a concurrent session cannot leave a half-written log.
- `--status` is covered by a test that injects an exception and checks it still exits 0.

### Upgrading
- No configuration changes. No entry-point files moved, so a running session keeps
  working; restart Claude Code to pick up the fixes.
```

- [ ] **Step 3: 全体検証**

Run: `uv run python scripts/verify.py quick; echo exit=$?`
Expected: `exit=0`。

- [ ] **Step 4: コミット**

```bash
git add pyproject.toml .claude-plugin/plugin.json uv.lock CHANGELOG.md
git commit -m "chore: 0.3.1 のリリース準備(CHANGELOG、バージョン)"
```

---

### Task 9: 受け入れ確認 — ゲートが実際に自分を止めた記録を残す(手動)

spec §2.6 の受け入れ条件。**自動テストでは代替できない**(稼働中の Claude Code セッションで、
GitHub 版プラグインが作業ツリーの `.loop-hooks.json` を読んで走ることの確認)。
main にマージし GitHub の main が更新された後に行う。

**Files:**
- Modify: `docs/superpowers/specs/2026-08-26-verification-roadmap-design.md`(§2.6 とレビュー状況)

- [ ] **Step 1: ミラーテストが CI 変更で落ちることを確認する**

`.github/workflows/ci.yml` の Lint 行を一時的に `uv run ruff check hooks tests` に戻して
`uv run pytest tests/test_verify.py -q -k mirrors` を実行。
Expected: FAIL。確認後に `git checkout .github/workflows/ci.yml` で戻す。

- [ ] **Step 2: Claude Code を再起動し、SessionStart 告知を確認する**

Expected: セッション開始時に `[loop-hooks] gate active: uv run python scripts/verify.py quick` が出る。
出なければ `/loop-hooks:status` で `config` の出所と `state` を確認し、プラグインが GitHub 版の
最新(0.3.1)になっているかを見る。

- [ ] **Step 3: 意図的に壊してターンを終える**

`tests/test_state.py` の任意の assert を `assert False` にする編集をエージェントに依頼し、
そのままターンを終えさせる。
Expected: Stop でゲートが走り、`[loop-hooks] verification gate failed.` と pytest の失敗が
フィードバックされてターンが終わらない。エージェントが元に戻すとターンが終わる。

- [ ] **Step 4: 判定ログを確認する**

Run: `/loop-hooks:status`
Expected: `recent` に `ran fail` → `ran pass` の 2 行が並ぶ。

- [ ] **Step 5: 結果を spec に記録する**

`docs/superpowers/specs/2026-08-26-verification-roadmap-design.md` の §2.6 の末尾に
「確認済み(YYYY-MM-DD): `ran fail` → `ran pass` を判定ログで確認。`quick` の所要時間 N 秒(Task 1 Step 7 の計測)」を追記し、
「レビュー状況」表に「第 1 段階 完了(0.3.1)」の行を足す。計画本体は
`docs/superpowers/archive/plans/` へ移す(0.3.0 の計画と同じ扱い)。

```bash
git mv docs/superpowers/plans/2026-08-26-phase1-dogfooding.md docs/superpowers/archive/plans/
git add docs/superpowers/specs/2026-08-26-verification-roadmap-design.md
git commit -m "docs: 第 1 段階の受け入れ結果を記録し、計画をアーカイブ"
```

---

## 自己レビュー

**Spec coverage(§2):** §2.1 ランナー → Task 1 / §2.2 設定 → Task 2 / §2.3 規約 → Task 2 /
§2.4 修正 5 件 → Task 3〜7 / §2.5 CI(`scripts` を ruff 対象に)→ Task 1。`claude plugin validate`
相当は 0.3.0 の `test_packaging.py` に JSON 妥当性検査が既にあるため追加なし / §2.6 受け入れ → Task 9。

**裁定(spec に明記の無い判断):** Task 3 の「fp が None なら走らせる」。0.3.0 spec の原則
「静かな失敗を無くす」と、走らせて損は無い(重いだけ)ことから安全側に倒した。
実行者が違和感を持ったら spec §2.4 に裁定として追記する。

**型整合:** `verify.Check.ok_codes: frozenset[int]`、`verify.run_stage(...) -> bool`、
`gate.status_main(str) -> int`、`status._recent(str) -> list[dict]`、`status.RECENT_SEARCH` は
各 Task で同名。`tests/test_status.py` のヘルパは `repo(tmp_path) -> Path`(実ファイルで確認済み)。
