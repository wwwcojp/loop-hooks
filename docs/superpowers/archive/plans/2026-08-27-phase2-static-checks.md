# 第 2 段階 — 静的検査の拡充(0.4.0)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `quick` ゲートと CI に `ruff format` / ruff `S` / import-linter / pyright strict を足し、CI に `security` ジョブ(zizmor・pip-audit)と Dependabot を加え、0.4.0 としてリリースする。

**Architecture:** `scripts/verify.py` の `STAGES["quick"]` が唯一の真実で、CI の `test` ジョブはその鏡。`Check` に `cwd` / `env` を足し、`shell_line(check)` が「CI に書くべき 1 行」を生成して、ミラーテストがそれと `ci.yml` を突き合わせる。以後、検査を足すときは `Check` を 1 つ足し、CI に同じ 1 行を足すだけ。`security` ジョブはミラーの対象外(外部ツール取得が要るため `quick` に入れない)。

**Tech Stack:** Python 3.10+、uv、pytest、ruff(check + format + `S`)、pyright(strict)、import-linter、zizmor、pip-audit、Dependabot。

**Spec:** `docs/superpowers/specs/2026-08-27-phase2-static-checks-design.md`(親: `2026-08-26-verification-roadmap-design.md` §3)

## Global Constraints

- `requires-python = ">=3.10"`。CI は 3.10 と 3.14。3.10 で動かない構文を使わない。
- 入口ファイル(`hooks/gate.py`・`hooks/session_start.py`・`hooks/hooks.json`)の**場所と import を動かさない**。再起動不要のリリースにする。
- `hooks/lib/*` の公開関数は例外を外に出さない。
- `quick` の所要時間 ≤ 15 秒。超えたら pyright を `all` に降ろす(spec §6)。
- ruff の除外: hooks / scripts は**行単位 `noqa` + 理由**のみ。tests は `per-file-ignores` で `S101`・`S603` のみ。
- Actions は**コミット SHA でピン留め**し `# vX.Y.Z` コメントを付ける。ピン先(2026-08-27 取得): `actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0`、`astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86 # v5.4.2`。
- 実ホームパスをソース・コミットメッセージに書かない(プレースホルダー `/home/USER`)。
- コミットは日本語の Conventional Commits。テスト名は日本語の `test_…`。`uv run …` はリポジトリルートから。
- **ゲートが掛かっている**: `.py` / `.json` / `.toml` / `.github/**/*.yml` を変えたターンの終了時に `quick` が走る。各タスクの途中で `quick` が赤いままターンを終えると止められる — それが正しい挙動。設定を変えて通さない。

---

## ファイル構成

| ファイル | 責務 | 変更 |
|---|---|---|
| `scripts/verify.py` | `Check` に `cwd` / `env`、`shell_line()`、`quick` に 4 チェック追加 | 変更 |
| `tests/test_verify.py` | ミラーテストを `shell_line` 駆動に、`test` ジョブ限定の抽出 | 変更 |
| `.github/workflows/ci.yml` | `test` ジョブに 4 ステップ、`security` ジョブ、permissions、SHA ピン留め | 変更 |
| `.github/dependabot.yml` | Actions と Python 依存の週次更新 | 新規 |
| `pyproject.toml` | ruff `S` / per-file-ignores、`[tool.pyright]`、`[tool.importlinter]`、dev 依存 | 変更 |
| `hooks/gate.py` / `hooks/lib/fingerprint.py` / `scripts/verify.py` | `noqa: S60x` + 理由(各 1 行) | 変更 |
| `hooks/**/*.py` / `tests/*.py` / `scripts/*.py` | `ruff format` 一括整形 | 変更(整形のみ) |
| `tests/test_packaging.py` | pyproject の設定・dependabot.yml の存在を固定 | 変更 |
| `CHANGELOG.md` / `pyproject.toml` / `.claude-plugin/plugin.json` / `uv.lock` / README 英日 | 0.4.0 | 変更 |

---

### Task 1: `ruff format` の一括適用と、`shell_line` 駆動のミラーテスト

**Files:**
- Modify: `hooks/**/*.py`, `tests/*.py`, `scripts/verify.py`(整形のみ、1 コミット目)
- Modify: `scripts/verify.py`(`Check.cwd` / `Check.env` / `shell_line`、`format` チェック)
- Modify: `tests/test_verify.py`(ミラーテストの書き換え)
- Modify: `.github/workflows/ci.yml`(`Format` ステップ)

**Interfaces:**
- Produces: `verify.Check(name, cmd, ok_codes=frozenset({0}), cwd=".", env={})`(frozen dataclass。`env` は `tuple[tuple[str, str], ...]` で持つ — frozen で dict は使えない)、`verify.shell_line(check: Check) -> str`(CI の `run:` に書く 1 行。`cd <cwd> && ` と `K=V ` を前置し、コマンドは `shlex.join`)。Task 3〜5 はこの 2 つに依存する。

- [ ] **Step 1: 整形だけのコミットを作る**

Run: `uv run ruff format hooks tests scripts && uv run ruff check hooks tests scripts && uv run pytest -q`
Expected: 19 files reformatted、ruff clean、全件 pass(挙動変更なし)。

```bash
git add -A hooks tests scripts
git commit -m "style: ruff format を適用(挙動変更なし)"
```

`git show --stat HEAD` で `.py` 以外が混ざっていないことを確認する。

- [ ] **Step 2: 失敗するテストを書く(`Check.cwd/env` と `shell_line`)**

`tests/test_verify.py` の `test_quick_stage_mirrors_ci` を**丸ごと置き換え**、その上に `shell_line` のテストを足す:

```python
def test_shell_lineは単純コマンドをそのまま返す():
    assert verify.shell_line(verify.Check("x", ["uv", "run", "pytest", "-q"])) == "uv run pytest -q"


def test_shell_lineはcwdとenvを前置する():
    c = verify.Check("x", ["uv", "run", "lint-imports"], cwd="hooks", env=(("PYTHONPATH", "."),))
    assert verify.shell_line(c) == "cd hooks && PYTHONPATH=. uv run lint-imports"


def test_shell_lineは空白を含む引数をクォートする():
    c = verify.Check("x", ["git", "grep", "-nP", "a b", "--"])
    assert verify.shell_line(c) == "git grep -nP 'a b' --"


def test_run_stageはcwdとenvを反映する(tmp_path):
    (tmp_path / "sub").mkdir()
    c = verify.Check("x", ["sh", "-c", 'test "$(pwd)" = "$EXPECT" && test "$FLAG" = on'],
                     cwd="sub", env=(("FLAG", "on"), ("EXPECT", str((tmp_path / "sub").resolve()))))
    assert verify.run_stage("quick", [c], repo_root=tmp_path) is True


def test_quick_stage_mirrors_ci():
    """spec §3.5: quick は CI の test ジョブと同じコマンド・同じ順序。

    ランナー側の Check から CI に書くべき 1 行(shell_line)を生成し、ci.yml の test ジョブの
    run ステップと 1 対 1・順序も一致させる。leak だけは CI 側が if ブロックなので部分一致。
    どちらか片方だけ変えると落ちる(両方向)。
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    quick = verify.STAGES["quick"]
    assert quick[0].name == "leak" and quick[0].ok_codes == frozenset({1})
    run_steps = _extract_ci_run_steps(ci)
    assert len(run_steps) == len(quick), f"CI の run ステップ数が quick と違う: {run_steps!r}"
    leak_step = run_steps[0]
    assert verify.shell_line(quick[0]) in leak_step
    assert re.search(r"\bif\b.*\bexit 1\b", leak_step, re.S), leak_step
    for check, step in zip(quick[1:], run_steps[1:]):
        assert step.strip() == verify.shell_line(check), check.name


def test_quickにformatチェックがある():
    names = [c.name for c in verify.STAGES["quick"]]
    assert names.index("format") == names.index("lint") + 1
    fmt = next(c for c in verify.STAGES["quick"] if c.name == "format")
    assert fmt.cmd == ["uv", "run", "ruff", "format", "--check", "hooks", "tests", "scripts"]
```

- [ ] **Step 3: 落ちることを確認する**

Run: `uv run pytest tests/test_verify.py -q`
Expected: `shell_line` 系は `AttributeError`、`cwd=` は `TypeError`、format は `ValueError`(`'format' is not in list`)。

- [ ] **Step 4: 実装する**

`scripts/verify.py`:

`import subprocess` の上に `import shlex` を追加。`Check` を

```python
@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    # 終了コードがこの集合に含まれれば成功。git grep は「不一致=1」が成功なので反転に使う
    ok_codes: frozenset[int] = frozenset({0})
    cwd: str = "."  # repo_root からの相対
    env: tuple[tuple[str, str], ...] = ()  # 追加の環境変数(frozen なので tuple)


def shell_line(check: Check) -> str:
    """CI の `run:` に書くべき 1 行。tests/test_verify.py がこれと ci.yml を突き合わせる。"""
    prefix = f"cd {check.cwd} && " if check.cwd != "." else ""
    env = "".join(f"{k}={v} " for k, v in check.env)
    return prefix + env + shlex.join(check.cmd)
```

に置き換え、`STAGES["quick"]` の `lint` の直後に

```python
        Check("format", ["uv", "run", "ruff", "format", "--check", "hooks", "tests", "scripts"]),
```

を追加。`_run` を

```python
def _run(check: Check, repo_root: Path) -> tuple[bool, str]:
    env = {**os.environ, **dict(check.env)} if check.env else None
    try:
        r = subprocess.run(
            check.cmd, cwd=repo_root / check.cwd, capture_output=True, text=True,
            timeout=CHECK_TIMEOUT_SEC, env=env,
        )
```

に変える(`import os` を追加)。

- [ ] **Step 5: CI に Format ステップを足す**

`.github/workflows/ci.yml` の `Lint` ステップの直後:

```yaml
      - name: Format
        run: uv run ruff format --check hooks tests scripts
```

- [ ] **Step 6: 通ることを確認する**

Run: `uv run pytest tests/test_verify.py -q && uv run python scripts/verify.py quick`
Expected: pass、`[verify] format: ok` が `lint` の次に出て exit 0。

- [ ] **Step 7: コミット**

```bash
git add scripts/verify.py tests/test_verify.py .github/workflows/ci.yml
git commit -m "feat(verify): Check に cwd/env と shell_line を足し、quick と CI に ruff format --check を追加"
```

---

### Task 2: ruff `S`(セキュリティ lint)

**Files:**
- Modify: `pyproject.toml`(`[tool.ruff.lint]`)
- Modify: `hooks/gate.py`, `hooks/lib/fingerprint.py`, `scripts/verify.py`(各 1 行の noqa)
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: なし(`quick` の `lint` チェックが `ruff check` を既に含む)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_packaging.py` の末尾:

```python
def test_ruffのSルールが有効でtestsだけS101とS603を除外する():
    """spec §3.2: hooks/scripts は行単位 noqa のみ、tests は per-file-ignores。"""
    import tomllib
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lint = cfg["tool"]["ruff"]["lint"]
    assert "S" in lint["select"]
    assert lint["per-file-ignores"] == {"tests/*": ["S101", "S603"]}
```

`tomllib` は 3.11+ なので、ファイル先頭の import 群に

```python
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]
```

を置き、テスト内の `import tomllib` は削除する。`pyproject.toml` の dev 依存に `"tomli>=2.0; python_version < '3.11'"` を追加し `uv lock`。

- [ ] **Step 2: 落ちることを確認する**

Run: `uv run pytest tests/test_packaging.py -q -k Sルール`
Expected: FAIL(`"S" in ["E","F","I","W"]` が偽)。

- [ ] **Step 3: 設定と noqa**

`pyproject.toml`:

```toml
[tool.ruff.lint]
# E402 を有効にすることで、hooks/ を import path に足すための sys.path.insert より
# 後ろに置いた import に付けた `# noqa: E402` が意味を持つ。
# S(flake8-bandit): subprocess の呼出箇所を明示的に受け入れる(行単位 noqa + 理由)。
select = ["E", "F", "I", "W", "S"]

[tool.ruff.lint.per-file-ignores]
# assert はテストの本質。S603 はテストが git を argv リストで呼ぶ箇所(ユーザー入力なし)。
"tests/*" = ["S101", "S603"]
```

Run: `uv run ruff check hooks scripts` → 3 件。各行に付ける:

- `hooks/gate.py` の `proc = subprocess.Popen(` 行: `  # noqa: S602 -- 検証コマンドのシェル実行が機能そのもの。コマンドは HEAD の .loop-hooks.json から読む(0.2.1)`
- `hooks/lib/fingerprint.py` の `r = subprocess.run(("git",) + args, ...` 行: `  # noqa: S603 -- argv は固定の git サブコマンド。入力はパスのみ`
- `scripts/verify.py` の `r = subprocess.run(` 行: `  # noqa: S603 -- argv は STAGES に固定。ユーザー入力なし`

行長 100 を超える場合は noqa の理由を短くするか、直前行のコメントに理由を書いて noqa は `# noqa: S602` だけにする(ruff は noqa 行の長さも E501 で見る)。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run python scripts/verify.py quick`
Expected: `lint: ok`(S の検出 0)、exit 0。

- [ ] **Step 5: コミット**

```bash
git add pyproject.toml uv.lock hooks/gate.py hooks/lib/fingerprint.py scripts/verify.py tests/test_packaging.py
git commit -m "feat(lint): ruff S を有効化し、設計どおりの subprocess 3 箇所を理由つき noqa で受け入れる"
```

---

### Task 3: import-linter(`lib` の 3 契約)

**Files:**
- Modify: `pyproject.toml`(`[tool.importlinter]`、dev 依存)
- Modify: `scripts/verify.py`(`imports` チェック)
- Modify: `.github/workflows/ci.yml`(`Import contracts` ステップ)
- Modify: `tests/test_verify.py`

**Interfaces:**
- Consumes: `verify.Check(cwd=, env=)`、`verify.shell_line`(Task 1)。

- [ ] **Step 1: 依存を足す**

Run: `uv add --dev import-linter && uv run lint-imports --version`
Expected: `import-linter 2.x`。

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_verify.py` の末尾:

```python
def test_quickにimport契約チェックがある():
    names = [c.name for c in verify.STAGES["quick"]]
    assert names.index("imports") == names.index("format") + 1
    c = next(c for c in verify.STAGES["quick"] if c.name == "imports")
    assert verify.shell_line(c) == (
        "cd hooks && PYTHONPATH=. uv run lint-imports --config ../pyproject.toml")
```

Run: `uv run pytest tests/test_verify.py -q -k import契約`
Expected: FAIL(`ValueError`)。

- [ ] **Step 3: 契約と Check を書く**

`pyproject.toml` に追加:

```toml
[tool.importlinter]
# 対象は lib パッケージのみ。入口(gate.py / session_start.py)はパッケージでないため
# root_packages にできない(親 spec §3.2)。実行は hooks/ を cwd に PYTHONPATH=. で。
root_packages = ["lib"]
include_external_packages = true

[[tool.importlinter.contracts]]
name = "lib は入口(gate / session_start)を import しない"
type = "forbidden"
source_modules = ["lib"]
forbidden_modules = ["gate", "session_start"]

[[tool.importlinter.contracts]]
name = "subprocess を使うのは fingerprint だけ"
type = "forbidden"
source_modules = ["lib.config", "lib.hook_io", "lib.log", "lib.state", "lib.status"]
forbidden_modules = ["subprocess"]
allow_indirect_imports = true

[[tool.importlinter.contracts]]
name = "lib の層(上が下に依存する)"
type = "layers"
layers = ["status", "log", "config", "fingerprint", "state", "hook_io"]
containers = ["lib"]
```

`scripts/verify.py` の `STAGES["quick"]`、`format` の直後:

```python
        Check("imports", ["uv", "run", "lint-imports", "--config", "../pyproject.toml"],
              cwd="hooks", env=(("PYTHONPATH", "."),)),
```

`.github/workflows/ci.yml` の `Format` の直後:

```yaml
      - name: Import contracts
        run: cd hooks && PYTHONPATH=. uv run lint-imports --config ../pyproject.toml
```

- [ ] **Step 4: 通ることと、契約が効くことを確認する**

Run: `uv run pytest tests/test_verify.py -q && uv run python scripts/verify.py quick`
Expected: pass、`[verify] imports: ok`、exit 0。

契約が効く確認(spec §4): `hooks/lib/state.py` の先頭に `from . import log` を一時的に足して
`cd hooks && PYTHONPATH=. uv run lint-imports --config ../pyproject.toml` → `lib の層 BROKEN`。
確認後 `git checkout hooks/lib/state.py`。結果(BROKEN になったこと)を Task 8 で spec に書く。

- [ ] **Step 5: コミット**

```bash
git add pyproject.toml uv.lock scripts/verify.py tests/test_verify.py .github/workflows/ci.yml
git commit -m "feat(lint): import-linter で lib の依存方向を契約にし、quick と CI に追加"
```

---

### Task 4: pyright strict

**Files:**
- Modify: `pyproject.toml`(`[tool.pyright]`、dev 依存)
- Modify: `scripts/verify.py`(`types` チェック)
- Modify: `.github/workflows/ci.yml`(`Type check` ステップ)
- Modify: `tests/test_verify.py`

**Interfaces:**
- Consumes: `verify.Check`、`verify.shell_line`(Task 1)。

- [ ] **Step 1: 依存とスパイク(tests を含められるか)**

Run: `uv add --dev pyright && uv run pyright --version`
Expected: `pyright 1.1.4xx`(初回は Node ランタイムの取得で数十秒)。

`pyproject.toml` に

```toml
[tool.pyright]
include = ["hooks", "scripts"]
strict = ["hooks", "scripts"]
pythonVersion = "3.10"
```

を書き、`uv run pyright` → `0 errors`。次に一時的に `include = ["hooks", "scripts", "tests"]`
(strict は変えない)で `uv run pyright` を回し、**tests が basic で 0 エラーなら `include` に残す、
エラーがあれば `tests` を外す**。どちらにしたかと件数を Task 8 で spec §3.4 に書く。

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_verify.py` の末尾:

```python
def test_quickに型検査がある():
    names = [c.name for c in verify.STAGES["quick"]]
    assert names.index("types") == names.index("imports") + 1
    c = next(c for c in verify.STAGES["quick"] if c.name == "types")
    assert c.cmd == ["uv", "run", "pyright"]
```

Run: `uv run pytest tests/test_verify.py -q -k 型検査` → FAIL。

- [ ] **Step 3: Check と CI**

`scripts/verify.py` の `imports` の直後: `Check("types", ["uv", "run", "pyright"]),`

`.github/workflows/ci.yml` の `Import contracts` の直後:

```yaml
      - name: Type check
        run: uv run pyright
```

- [ ] **Step 4: 通ることと所要時間を確認する**

Run: `uv run pytest tests/test_verify.py -q && time uv run python scripts/verify.py quick`
Expected: pass、`[verify] types: ok`、**合計 15 秒以内**(超えたら Global Constraints に従い
`types` を `quick` から外して `STAGES["all"] = quick + [types]` にし、CI の Type check は残す。
その場合ミラーテストは `quick` だけを見るので CI 側に `Type check` があると落ちる →
`security` ジョブへ移す。所要時間を Task 8 で spec に書く)。

- [ ] **Step 5: コミット**

```bash
git add pyproject.toml uv.lock scripts/verify.py tests/test_verify.py .github/workflows/ci.yml
git commit -m "feat(lint): pyright strict を quick と CI に追加"
```

---

### Task 5: CI — `security` ジョブ、permissions、SHA ピン留め

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_verify.py`(`test` ジョブだけを抽出)

**Interfaces:**
- Produces: `_extract_ci_run_steps(ci_yaml, job="test")`(既存関数にジョブ名の引数を足す)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_verify.py` の `_extract_ci_run_steps` を次に置き換える(`job` 引数で対象ジョブを絞る):

```python
def _extract_ci_run_steps(ci_yaml: str, job: str = "test") -> list[str]:
    """ci.yml の指定ジョブの `run:` 本文を出現順に取り出す(YAML パーサを足さないための最小実装)。

    `jobs:` 配下で `  <job>:` から次の 2 スペースインデントのキーまでを対象にする。
    `run: |` のブロックはインデントが戻るまで、`run: cmd` は 1 行。
    """
    lines = ci_yaml.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.rstrip() == f"  {job}:")
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^  \S", lines[i])), len(lines))
    section = lines[start:end]
    steps: list[str] = []
    i = 0
    while i < len(section):
        m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", section[i])
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
        while i < len(section) and (
            not section[i].strip() or len(section[i]) - len(section[i].lstrip()) > indent
        ):
            body.append(section[i])
            i += 1
        steps.append("\n".join(body))
    return steps


def test_extractはジョブを絞る():
    ci = "jobs:\n  test:\n    steps:\n      - run: a\n  security:\n    steps:\n      - run: b\n"
    assert _extract_ci_run_steps(ci, "test") == ["a"]
    assert _extract_ci_run_steps(ci, "security") == ["b"]


def test_ciのsecurityジョブはzizmorとpip_auditを回す():
    """spec §3.5: quick には入れない検査。CI 側だけに存在する。"""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = _extract_ci_run_steps(ci, "security")
    assert any("zizmor" in s for s in steps) and any("pip-audit" in s for s in steps)


def test_ciのActionsはSHAでピン留めされている():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)", ci)
    assert uses, "uses が無い"
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u), f"SHA でピン留めされていない: {u}"


def test_ciはpermissionsを明示する():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read\n", ci, re.M)
```

Run: `uv run pytest tests/test_verify.py -q`
Expected: `test_extractはジョブを絞る` は pass、`security` / `SHA` / `permissions` の 3 件が FAIL。

- [ ] **Step 2: `ci.yml` を書き換える**

全体を次にする(`test` ジョブの `run` 行は Task 1〜4 で入れたものをそのまま保つ):

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        # 宣言している最低バージョン(pyproject.toml requires-python)と、現在の
        # 既定バージョンの両方で回す。ピン無しの `uv run` だけだと uv が解決した
        # 1バージョンしか通らず、他バージョンだけで再現する退行を検出できない。
        python-version: ["3.10", "3.14"]
    env:
      UV_PYTHON: ${{ matrix.python-version }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86 # v5.4.2
        with:
          python-version: ${{ matrix.python-version }}
      - name: 実ホームパスのリークチェック
        run: |
          if git grep -nP '/(home|Users)/(?!USER\b|alice\b|user\b)[A-Za-z_][A-Za-z0-9._-]*' --; then
            echo '::error::実ホームパスの可能性がある記述を検出しました。$HOME・/home/USER・/home/alice のプレースホルダーに置き換えてください'
            exit 1
          fi
      - name: Lint
        run: uv run ruff check hooks tests scripts
      - name: Format
        run: uv run ruff format --check hooks tests scripts
      - name: Import contracts
        run: cd hooks && PYTHONPATH=. uv run lint-imports --config ../pyproject.toml
      - name: Type check
        run: uv run pyright
      - name: Test
        run: uv run pytest -q

  security:
    # quick には入れない検査(外部ツールの取得とネットワークが要る)。spec §3.5
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86 # v5.4.2
      - name: GitHub Actions の設定検査(zizmor)
        run: uvx zizmor --min-severity low .github/workflows
      - name: 依存の脆弱性(pip-audit)
        run: uv export --format requirements-txt --no-hashes | uvx pip-audit -r /dev/stdin
```

- [ ] **Step 3: 手元で zizmor を回す**

Run: `uvx zizmor --min-severity low .github/workflows`
Expected: `No findings`(0 件)。残るなら指摘どおりに `ci.yml` を直す(例: `uv export` のステップに
`persist-credentials` は無関係。`template-injection` が出たら `${{ }}` を `env:` 経由にする)。

- [ ] **Step 4: 通ることを確認する**

Run: `uv run pytest tests/test_verify.py -q && uv run python scripts/verify.py quick`
Expected: pass、exit 0(ミラーテストは `test` ジョブの 6 ステップ = `quick` の 6 チェック)。

- [ ] **Step 5: コミット**

```bash
git add .github/workflows/ci.yml tests/test_verify.py
git commit -m "ci: security ジョブ(zizmor・pip-audit)を追加し、permissions 明示と Actions の SHA ピン留め"
```

---

### Task 6: Dependabot

**Files:**
- Create: `.github/dependabot.yml`
- Modify: `tests/test_packaging.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_packaging.py` の末尾:

```python
def test_dependabotがActionsを週次で追う():
    """spec §3.6: SHA ピン留めした Actions の更新は Dependabot に任せる。"""
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "github-actions"' in text
    assert 'interval: "weekly"' in text
```

Run: `uv run pytest tests/test_packaging.py -q -k dependabot` → FAIL(`FileNotFoundError`)。

- [ ] **Step 2: スパイク — `uv` エコシステム**

GitHub の Dependabot ドキュメント(`https://docs.github.com/en/code-security/dependabot/ecosystems-supported-by-dependabot/supported-ecosystems-and-repositories`)で `uv` が
`package-ecosystem` に**あるか**を確認する。ある → Step 3 の 2 ブロック目を書く。無い →
1 ブロック目だけにして、`dependabot.yml` にコメントで「uv.lock は Dependabot 未対応(確認日)。
dev 依存の更新は `uv lock --upgrade` を手で回す」と残す。

- [ ] **Step 3: ファイルを書く**

`.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      actions:
        patterns: ["*"]
  # Step 2 で uv 対応が確認できた場合のみ:
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      python:
        patterns: ["*"]
```

- [ ] **Step 4: 通ることを確認してコミット**

Run: `uv run pytest tests/test_packaging.py -q`

```bash
git add .github/dependabot.yml tests/test_packaging.py
git commit -m "ci: Dependabot で Actions(と uv 依存)を週次で追う"
```

---

### Task 7: 0.4.0 リリース準備

**Files:**
- Modify: `pyproject.toml`, `.claude-plugin/plugin.json`(`0.4.0`)、`uv.lock`
- Modify: `CHANGELOG.md`
- Modify: `README.md`, `README.ja.md`(Tests 節に開発時の検査一式を 1 行)
- Modify: `CLAUDE.md`(規約 5 の「3 コマンド」を「6 チェック」に)

- [ ] **Step 1: バージョン**

`pyproject.toml` と `.claude-plugin/plugin.json` の version を `0.4.0` にし `uv lock`。
Run: `uv run pytest tests/test_packaging.py -q` → pass。

- [ ] **Step 2: CHANGELOG(`## [0.3.2]` の上)**

```markdown
## [0.4.0] - 2026-08-27

### Added
- **More checks in the repository's own gate and CI**, all deterministic and fast:
  `ruff format --check`, ruff's `S` (bandit) rules with the three designed `subprocess`
  call sites accepted by line-level `noqa` and a stated reason, import-linter contracts
  for `hooks/lib` (layering, no import of entry points, `subprocess` confined to
  `fingerprint`), and pyright in strict mode. `quick` now runs six checks in about
  N seconds (measured).
- **CI `security` job**: zizmor over the workflows and pip-audit over the exported lock
  file. Workflow actions are pinned to commit SHAs, `permissions: contents: read` is
  explicit, and Dependabot keeps the pins current.
- `scripts/verify.py`: `Check` gained `cwd` / `env`, and `shell_line()` is the single
  source for the CI mirror test.

### Changed
- Source reformatted with `ruff format` (no behaviour change).

### Upgrading
- Nothing to do. No entry-point files or hook definitions changed; no restart needed.
```

`N` は Task 4 Step 4 の実測値に置き換える。

- [ ] **Step 3: README / CLAUDE.md**

`README.md` の `## Tests` を

```markdown
## Tests

```bash
uv run pytest -v
```

The repository gates itself with this plugin: `uv run python scripts/verify.py quick`
runs the same checks as CI (home-path leak check, ruff check/format, import-linter,
pyright, pytest).
```

に。`README.ja.md` の対応節も同趣旨で。`CLAUDE.md` の規約 5 を
「`quick` は CI の `test` ジョブと同じ 6 チェック(leak → ruff check → ruff format → import-linter → pyright → pytest)。CI を変えるときは `scripts/verify.py` も変える(`tests/test_verify.py::test_quick_stage_mirrors_ci` が検出する)」に。

- [ ] **Step 4: 全体検証とコミット**

Run: `uv run python scripts/verify.py quick; echo exit=$?` → `exit=0`。

```bash
git add pyproject.toml .claude-plugin/plugin.json uv.lock CHANGELOG.md README.md README.ja.md CLAUDE.md
git commit -m "chore: 0.4.0 のリリース準備(CHANGELOG、バージョン、README/CLAUDE.md)"
```

---

### Task 8: 受け入れ(main マージ後、手動を含む)

**Files:**
- Modify: `docs/superpowers/specs/2026-08-27-phase2-static-checks-design.md`(§2 の実測、§3.4 の tests 判断、§4 の結果)

- [ ] **Step 1: push 後、CI の `test`(3.10 / 3.14)と `security` が green**

Run: `gh run watch --exit-status $(gh run list --branch main --limit 1 --json databaseId --jq '.[0].databaseId')`

- [ ] **Step 2: secret scanning / push protection(ユーザー承認のうえ実行)**

リポジトリ設定の変更なので、**実行前にユーザーに確認する**。承認後:

```bash
gh api -X PATCH repos/wwwcojp/loop-hooks \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
gh api repos/wwwcojp/loop-hooks --jq '.security_and_analysis'
```

Expected: 両方 `enabled`。

- [ ] **Step 3: Dependabot の初回 PR**

数日以内に `github-actions` の更新 PR(v4 → v7 等のメジャー更新を含む)が届く。届いたら
zizmor と CI が green なことを見てマージする(本計画の範囲外。届いたことだけ spec に書く)。

- [ ] **Step 4: spec に記録し、計画をアーカイブ**

spec §4 の末尾に「確認済み(YYYY-MM-DD): quick N 秒 / pyright strict 0 / import-linter 3 KEPT
(state.py に `from . import log` で BROKEN を確認)/ zizmor 0 / security ジョブ green /
secret scanning enabled / Dependabot PR 到着(日付)」を追記。親 spec のレビュー状況表を
「第 2 段階 完了(0.4.0)」に。

```bash
git mv docs/superpowers/plans/2026-08-27-phase2-static-checks.md docs/superpowers/archive/plans/
git add docs
git commit -m "docs: 第 2 段階の受け入れ結果を記録し、計画をアーカイブ"
```

---

## 自己レビュー

**Spec coverage:** §3.1 → Task 1 / §3.2 → Task 2 / §3.3 → Task 3 / §3.4 → Task 4 / §3.5 → Task 1・3・4(test ジョブのステップ)+ Task 5(security・permissions・ピン留め・ミラーの job 限定)/ §3.6 → Task 6 / §3.7 → Task 8 Step 2 / §4 受け入れ → Task 4 Step 4(所要時間)、Task 3 Step 4(BROKEN 確認)、Task 5 Step 3(zizmor 0)、Task 8。

**判断(spec に無いもの):** (1) `Check.env` は frozen dataclass のため `tuple[tuple[str,str],...]`。(2) ミラーテストを `shell_line` 駆動に書き換え、以後の追加を「Check 1 つ + CI 1 行」にした(spec §3.5 の「鏡」を機械化)。(3) Actions のピンは同メジャーの最新(v4.4.0 / v5.4.2)。メジャー更新は Dependabot の PR で別途判断。(4) `tomllib` の 3.10 対応に `tomli` を dev 依存に足す。

**型整合:** `verify.Check(name, cmd, ok_codes, cwd, env)`、`verify.shell_line(Check) -> str`、`_extract_ci_run_steps(ci_yaml, job="test") -> list[str]` は各 Task で同名。`STAGES["quick"]` の順序は `leak, lint, format, imports, types, tests`(Task 1〜4 が順に挿入し、Task 5 の `ci.yml` 全文がその順序)。
