# 第 3 段階 — mutation testing + ラチェット(0.5.0)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** import をルート起点(`hooks.lib`)に揃えて mutmut を動かし、`scripts/verify.py mutation`(ファイル別 score のラチェット、baseline は `tests/mutation-baseline.json`)と `all` = quick + mutation を足し、初回トリアージで score を目標(5 ファイル ≥ 85、`status` ≥ 80)まで上げて 0.5.0 としてリリースする。

**Architecture:** `scripts/verify.py` に mutation 専用の関数群(`mutation_scores` / `check_mutation_baseline` / `run_mutation`)を追加し、`main` が `quick` / `mutation` / `all` を振り分ける。`quick` は従来どおり `Check` のリスト(CI ミラーの対象)、`mutation` は mutmut を毎回フル実行して `mutants/**/*.py.meta` を集計し、baseline を下回れば非ゼロ終了・上回れば baseline を書き換える。トリアージは「生き残りを `mutmut show` で見る → その変異を殺すテストを書く → `mutation` で killed を確認」の TDD。

**Tech Stack:** Python 3.10+、uv、pytest、mutmut 3.7、ruff、pyright、import-linter。

**Spec:** `docs/superpowers/specs/2026-08-27-phase3-mutation-design.md`(スパイク: `2026-08-27-mutation-spike-results.md`、親: `2026-08-26-verification-roadmap-design.md` §4)

## Global Constraints

- `requires-python = ">=3.10"`。CI は 3.10 と 3.14。
- 入口ファイル(`hooks/gate.py`・`hooks/session_start.py`・`hooks/hooks.json`)の**場所を動かさない**。本計画で変えるのは import 行のみ(spec §2.1)。`hooks/__init__.py` は作らない。
- `hooks/lib/*` の公開関数は例外を外に出さない。
- `mutation` / `all` を Stop ゲートにも CI にも載せない。`quick` は 6 チェックのまま ≤ 15 秒。
- baseline(`tests/mutation-baseline.json`)は `scripts/verify.py mutation` だけが上げる。手で下げない。除外は `# pragma: no mutate` + 理由 + spec への記録のみ。
- テストはまず失敗を見る(TDD)。mutation のトリアージでは「変異が生き残っている」ことが RED に相当し、テスト追加後に `mutation` で killed になることが GREEN。
- 実ホームパスをソース・コミットメッセージに書かない(`/home/USER`)。日本語の Conventional Commits。テスト名は日本語の `test_…`。`uv run …` はリポジトリルートから。
- ゲートが掛かっている: `.py` / `.json` / `.toml` / `.github/**/*.yml` を変えたターン終了時に `quick` が走る。赤いままなら止められる — 正しい挙動。

---

## ファイル構成

| ファイル | 責務 | 変更 |
|---|---|---|
| `hooks/gate.py` / `hooks/session_start.py` | import 行のみ(`sys.path` をプラグインルートに、`from hooks.lib import …`) | 変更 |
| `tests/*.py` | import 行のみ | 変更 |
| `pyproject.toml` | `[tool.importlinter]` を `hooks` 起点に、`[tool.mutmut]`、`mutants/` の除外、dev 依存 `mutmut` | 変更 |
| `.gitignore` / `.loop-hooks.json` | `mutants/` を除外 | 変更 |
| `scripts/verify.py` | `imports` Check の cwd 変更、mutation 関数群、`main` の振り分け | 変更 |
| `tests/test_verify.py` | mutation 関数群のテスト、`imports` Check の期待値更新 | 変更 |
| `tests/mutation-baseline.json` | ファイル別 score(ランナーが生成・更新) | 新規 |
| `tests/test_hook_io.py` | `read_event` / `emit` の直接テスト | 新規 |
| `tests/test_log.py` / `test_fingerprint.py` / `test_config.py` / `test_status.py` | トリアージで追加するテスト | 変更 |
| `CLAUDE.md` / README 英日 / CHANGELOG / バージョン 3 箇所 | 0.5.0 | 変更 |
| `.github/workflows/ci.yml` | `Import contracts` の行を新コマンドに | 変更 |

---

### Task 1: import のルート起点化

**Files:**
- Modify: `hooks/gate.py:23-24,175`、`hooks/session_start.py:19-20`
- Modify: `tests/test_config.py`, `test_fingerprint.py`, `test_gate.py`, `test_log.py`, `test_packaging.py`, `test_session_start.py`, `test_state.py`, `test_status.py`(import 行)
- Modify: `pyproject.toml`(`[tool.importlinter]`)、`scripts/verify.py`(`imports` Check)、`.github/workflows/ci.yml`(`Import contracts`)、`tests/test_verify.py`(`test_quickにimport契約チェックがある`)

**Interfaces:**
- Produces: 実行時モジュール名が `hooks.lib.<name>` / `hooks.gate` / `hooks.session_start` になる(Task 2 以降の mutmut がこれを前提にする)。`verify.STAGES["quick"]` の `imports` Check は `Check("imports", ["uv","run","lint-imports","--config","pyproject.toml"], env=(("PYTHONPATH","."),))`(cwd は既定 `.`)。

- [ ] **Step 1: 失敗するテストを書く(ミラーの期待値を先に変える)**

`tests/test_verify.py` の `test_quickにimport契約チェックがある` の最後の assert を

```python
    assert verify.shell_line(c) == "PYTHONPATH=. uv run lint-imports --config pyproject.toml"
```

に変える。Run: `uv run pytest tests/test_verify.py -q -k import契約` → FAIL(旧の `cd hooks && …`)。

- [ ] **Step 2: 入口の import を変える**

`hooks/gate.py`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import config, fingerprint, hook_io, log, state  # noqa: E402
```

`status_main` 内の `from lib import status  # …` を `from hooks.lib import status  # …`(コメントはそのまま)。

`hooks/session_start.py`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import config, fingerprint, hook_io, log  # noqa: E402
```

- [ ] **Step 3: テストの import を変える**

各ファイルで `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))` →
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`、
`from lib import …` → `from hooks.lib import …`(インデント付きの関数内 import も同様)、
`import gate  # noqa: E402` → `from hooks import gate  # noqa: E402`、
`import session_start  # noqa: E402` → `from hooks import session_start  # noqa: E402`、
`tests/test_packaging.py` の `sys.path.insert(0, str(ROOT / "hooks"))` → `sys.path.insert(0, str(ROOT))`。

一括置換の例(確認しながら):

```bash
sed -i 's|sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))|sys.path.insert(0, str(Path(__file__).resolve().parent.parent))|; s|^from lib import|from hooks.lib import|; s|^\(\s*\)from lib import|\1from hooks.lib import|; s|^import gate  # noqa: E402|from hooks import gate  # noqa: E402|; s|^import session_start  # noqa: E402|from hooks import session_start  # noqa: E402|; s|sys.path.insert(0, str(ROOT / "hooks"))|sys.path.insert(0, str(ROOT))|' tests/*.py
grep -rn "from lib import\|^import gate\b\|^import session_start\b\|/ \"hooks\"))" tests hooks || echo converted
```

`ruff check --select I` が import 順を直せと言ったら `uv run ruff check --fix tests hooks` で並べ替える。

- [ ] **Step 4: import-linter を `hooks` 起点に**

`pyproject.toml` の `[tool.importlinter]` を次に置き換える:

```toml
[tool.importlinter]
# 対象は hooks 名前空間パッケージ(__init__.py は作らない。PEP 420)。実行はリポジトリルートで
# PYTHONPATH=.(第 3 段階で lib 起点から hooks 起点に変更。mutmut の変異キーと合わせるため)。
root_packages = ["hooks"]
include_external_packages = true

[[tool.importlinter.contracts]]
name = "lib は入口(gate / session_start)を import しない"
type = "forbidden"
source_modules = ["hooks.lib"]
forbidden_modules = ["hooks.gate", "hooks.session_start"]

[[tool.importlinter.contracts]]
name = "subprocess を使うのは fingerprint だけ"
type = "forbidden"
source_modules = ["hooks.lib.config", "hooks.lib.hook_io", "hooks.lib.log", "hooks.lib.state", "hooks.lib.status"]
forbidden_modules = ["subprocess"]
allow_indirect_imports = true

[[tool.importlinter.contracts]]
name = "lib の層(上が下に依存する)"
type = "layers"
layers = ["status", "log", "config", "fingerprint", "state", "hook_io"]
containers = ["hooks.lib"]
```

`scripts/verify.py` の `imports` Check を

```python
        Check(
            "imports",
            ["uv", "run", "lint-imports", "--config", "pyproject.toml"],
            env=(("PYTHONPATH", "."),),
        ),
```

に。`.github/workflows/ci.yml` の `Import contracts` を
`run: PYTHONPATH=. uv run lint-imports --config pyproject.toml` に。

- [ ] **Step 5: 全部通す**

Run: `uv run python scripts/verify.py quick; echo exit=$?`
Expected: 6/6 ok、exit 0(import-linter が名前空間パッケージ `hooks` を root にできない場合は
`grimp` のエラーになる。その場合は spec §2.1 の判断を覆して `hooks/__init__.py`(空、docstring 1 行)を
足し、`.claude-plugin` のパッケージングに影響が無いこと(`test_packaging` pass)を確認して、
報告に「__init__.py を作った理由」を書く)。

入口が直接実行できることの確認:

```bash
echo '{"hook_event_name":"Stop","cwd":"'"$PWD"'","stop_hook_active":false}' | uv run hooks/gate.py; echo exit=$?
echo '{"hook_event_name":"SessionStart","cwd":"'"$PWD"'","source":"startup"}' | uv run hooks/session_start.py | head -c 300; echo; echo exit=$?
uv run hooks/gate.py --status . | head -3
```

Expected: いずれも exit 0(gate は変更が無ければ何も出さない。session_start は JSON を出す)。

- [ ] **Step 6: コミット**

```bash
git add hooks/gate.py hooks/session_start.py tests pyproject.toml scripts/verify.py .github/workflows/ci.yml
git commit -m "refactor: import をルート起点(hooks.lib)に揃える(入口ファイルの場所は不変、再起動不要)"
```

---

### Task 2: mutmut の設定と `mutants/` の除外

**Files:**
- Modify: `pyproject.toml`(`[tool.mutmut]`、`[tool.ruff] extend-exclude`、`[tool.pyright] exclude`、dev 依存)
- Modify: `.gitignore`、`.loop-hooks.json`
- Modify: `tests/test_packaging.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_packaging.py` の末尾:

```python
def test_mutmutの設定とmutantsの除外():
    """spec §2.2: 対象は hooks/lib 6 本。mutants/ は git・ruff・pyright・ゲートの対象外。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mut = cfg["tool"]["mutmut"]
    assert mut["source_paths"] == ["hooks"]
    assert sorted(mut["only_mutate"]) == sorted(
        f"hooks/lib/{n}.py" for n in ("config", "fingerprint", "hook_io", "log", "state", "status")
    )
    assert "scripts" in mut["also_copy"] and ".loop-hooks.json" in mut["also_copy"]
    assert "mutants" in cfg["tool"]["ruff"]["extend-exclude"]
    assert "mutants" in cfg["tool"]["pyright"]["exclude"]
    assert "mutants/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    gate = json.loads((ROOT / ".loop-hooks.json").read_text(encoding="utf-8"))["gate"]
    assert "mutants/*" in gate["ignore"]
```

Run: `uv run pytest tests/test_packaging.py -q -k mutmut` → FAIL(`KeyError: 'mutmut'`)。

- [ ] **Step 2: 設定を書く**

`uv add --dev mutmut`(3.7 以上が入ることを `uv run mutmut --version` で確認)。

`pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
# ruff format は Markdown の fenced code も整形対象にする。docs は対象外。mutants は mutmut の作業領域
extend-exclude = ["docs", "mutants"]
```

`[tool.pyright]` に `exclude = ["mutants"]` を追加。末尾に:

```toml
[tool.mutmut]
# 対象は hooks/lib 6 本。入口(gate.py / session_start.py)は subprocess 経由のテストが主体で
# mutmut のカバレッジに見えにくいため対象外(spec §2.2)。also_copy はテストが読むファイル群。
source_paths = ["hooks"]
only_mutate = [
  "hooks/lib/config.py", "hooks/lib/fingerprint.py", "hooks/lib/hook_io.py",
  "hooks/lib/log.py", "hooks/lib/state.py", "hooks/lib/status.py",
]
also_copy = [
  "scripts", "skills", ".claude-plugin", ".github", "docs",
  "README.md", "README.ja.md", "LICENSE", "CLAUDE.md", "CHANGELOG.md",
  ".loop-hooks.json", "uv.lock",
]
```

`.gitignore` に `mutants/` の行(コメント `# mutmut の作業領域(scripts/verify.py mutation が毎回作り直す)`)。
`.loop-hooks.json` の `ignore` を `[".superpowers/*", "docs/*", "mutants/*"]` に。

- [ ] **Step 3: 通す・コミット**

Run: `uv run pytest tests/test_packaging.py -q && uv run python scripts/verify.py quick`

```bash
git add pyproject.toml uv.lock .gitignore .loop-hooks.json tests/test_packaging.py
git commit -m "chore: mutmut の設定(hooks/lib 6 本)と mutants/ の除外"
```

---

### Task 3: `scripts/verify.py mutation` ステージとラチェット

**Files:**
- Modify: `scripts/verify.py`
- Modify: `tests/test_verify.py`

**Interfaces:**
- Produces: `verify.MUTATION_KILLED_CODES = frozenset({1, 3, -24})`、`verify.MUTMUT_CMD = ["uv","run","mutmut","run"]`、`verify.BASELINE_REL = Path("tests") / "mutation-baseline.json"`、`verify.mutation_scores(repo_root: Path) -> dict[str, dict[str, Any]]`、`verify.check_mutation_baseline(repo_root: Path, scores) -> tuple[bool, list[str]]`、`verify.run_mutation(repo_root=REPO_ROOT, runner=None) -> bool`、`verify.main(["mutation"|"all"])`。`STAGES` は `quick` のみのまま(ミラーテスト不変)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_verify.py` の末尾:

```python
def _write_meta(root: Path, rel: str, codes: dict[str, int]) -> None:
    meta = root / "mutants" / (rel + ".meta")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"exit_code_by_key": codes}), encoding="utf-8")


def test_mutation_scoresはmetaからファイル別に集計する(tmp_path):
    """spec §2.3: killed = 1/3/-24。survived(0)・no tests(5/33)・timeout は未検出扱い。"""
    _write_meta(tmp_path, "hooks/lib/a.py", {"k1": 1, "k2": 3, "k3": -24, "s": 0, "n": 5, "t": 24})
    _write_meta(tmp_path, "hooks/lib/b.py", {})
    scores = verify.mutation_scores(tmp_path)
    assert scores == {"hooks/lib/a.py": {"score": 50.0, "killed": 3, "total": 6}}


def test_baselineが無ければ作られる(tmp_path):
    ok, problems = verify.check_mutation_baseline(tmp_path, {"hooks/lib/a.py": {"score": 80.0, "killed": 8, "total": 10}})
    assert ok and problems == []
    data = json.loads((tmp_path / verify.BASELINE_REL).read_text(encoding="utf-8"))
    assert data["files"] == {"hooks/lib/a.py": 80.0}


def test_baselineを下回ればfailで一覧(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / verify.BASELINE_REL).write_text(json.dumps({"files": {"hooks/lib/a.py": 90.0}}), encoding="utf-8")
    ok, problems = verify.check_mutation_baseline(tmp_path, {"hooks/lib/a.py": {"score": 80.0, "killed": 8, "total": 10}})
    assert not ok and problems == ["hooks/lib/a.py: score 80.0 < baseline 90.0"]
    assert json.loads((tmp_path / verify.BASELINE_REL).read_text())["files"] == {"hooks/lib/a.py": 90.0}


def test_baselineを上回れば書き換える(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / verify.BASELINE_REL).write_text(json.dumps({"files": {"hooks/lib/a.py": 70.0}}), encoding="utf-8")
    ok, _ = verify.check_mutation_baseline(tmp_path, {"hooks/lib/a.py": {"score": 80.0, "killed": 8, "total": 10}})
    assert ok
    assert json.loads((tmp_path / verify.BASELINE_REL).read_text())["files"] == {"hooks/lib/a.py": 80.0}


def test_baselineにあって結果に無いファイルはfail(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / verify.BASELINE_REL).write_text(json.dumps({"files": {"hooks/lib/gone.py": 70.0}}), encoding="utf-8")
    ok, problems = verify.check_mutation_baseline(tmp_path, {})
    assert not ok and problems and problems[0].startswith("hooks/lib/gone.py: baseline 70.0 にあるが")


def test_run_mutationはmutmut失敗で偽(tmp_path, capsys):
    assert verify.run_mutation(tmp_path, runner=lambda root: (1, "boom")) is False
    assert "boom" in capsys.readouterr().err


def test_run_mutationは結果が無ければ偽(tmp_path, capsys):
    assert verify.run_mutation(tmp_path, runner=lambda root: (0, "")) is False
    assert "only_mutate" in capsys.readouterr().err


def test_run_mutationは表を出しbaselineを更新して真(tmp_path, capsys):
    def fake(root: Path) -> tuple[int, str]:
        _write_meta(root, "hooks/lib/a.py", {"k": 1, "s": 0})
        return 0, ""

    assert verify.run_mutation(tmp_path, runner=fake) is True
    out = capsys.readouterr().out
    assert "hooks/lib/a.py" in out and "50.0" in out
    assert (tmp_path / verify.BASELINE_REL).exists()


def test_mainはmutationとallを受け付ける(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(verify, "run_stage", lambda stage, checks=None, repo_root=None: calls.append(stage) or True)
    monkeypatch.setattr(verify, "run_mutation", lambda: calls.append("mutation") or True)
    assert verify.main(["mutation"]) == 0 and calls == ["mutation"]
    calls.clear()
    assert verify.main(["all"]) == 0 and calls == ["quick", "mutation"]


def test_mainのallはquickが落ちればmutationを回さない(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(verify, "run_stage", lambda stage, checks=None, repo_root=None: calls.append(stage) or False)
    monkeypatch.setattr(verify, "run_mutation", lambda: calls.append("mutation") or True)
    assert verify.main(["all"]) == 1 and calls == ["quick"]
```

`json` を `tests/test_verify.py` の import に足す。行長 100 を超える行は折り返す。

Run: `uv run pytest tests/test_verify.py -q` → 新規 10 件が `AttributeError` で FAIL。

- [ ] **Step 2: 実装する**

`scripts/verify.py`: import に `import json`、`import shutil`、`import time`、`from collections.abc import Callable, Sequence`、`from typing import Any` を足す(既存の `Sequence` import を置き換え)。定数の下に:

```python
# mutmut の終了コード→状態(mutmut/__main__.py status_by_exit_code)のうち "killed" のもの。
# survived(0)・no tests(5/33)・timeout・suspicious はすべて「検出できていない」として数える
MUTATION_KILLED_CODES = frozenset({1, 3, -24})
MUTMUT_CMD = ["uv", "run", "mutmut", "run"]
BASELINE_REL = Path("tests") / "mutation-baseline.json"
```

`main` の前に(spec §2.3。姉妹 PJ safe-dev-hooks `scripts/verify.py` からの移植、evidence 無し):

```python
def mutation_scores(repo_root: Path) -> dict[str, dict[str, Any]]:
    """mutants/ 配下の *.py.meta からファイル別 {score, killed, total} を集計する。

    キーはリポジトリ相対パス(例: hooks/lib/config.py)。変異が 0 のファイルは載せない。
    """
    mutants = repo_root / "mutants"
    scores: dict[str, dict[str, Any]] = {}
    for meta in sorted(mutants.rglob("*.py.meta")):
        codes = json.loads(meta.read_text(encoding="utf-8")).get("exit_code_by_key", {})
        if not codes:
            continue
        total = len(codes)
        killed = sum(1 for c in codes.values() if c in MUTATION_KILLED_CODES)
        rel = meta.relative_to(mutants).as_posix()[: -len(".meta")]
        scores[rel] = {"score": round(killed / total * 100, 1), "killed": killed, "total": total}
    return scores


def check_mutation_baseline(
    repo_root: Path, scores: dict[str, dict[str, Any]]
) -> tuple[bool, list[str]]:
    """ファイル別ラチェット。(ok, 問題の一覧) を返す。ok で変化があれば baseline を書き換える。

    - 下回ったファイル / baseline にあって結果に無いファイル → fail(全件列挙)
    - 新規ファイルは登録、上回った分だけ更新。変化が無ければファイルに触らない
    """
    path = repo_root / BASELINE_REL
    baseline: dict[str, float] = {}
    if path.exists():
        baseline = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    problems: list[str] = []
    for f, b in sorted(baseline.items()):
        if f not in scores:
            problems.append(
                f"{f}: baseline {b} にあるが今回の結果に無い(only_mutate から外れている?"
                " 対象の縮小は baseline を手で外す必要がある)"
            )
        elif scores[f]["score"] < b:
            problems.append(f"{f}: score {scores[f]['score']} < baseline {b}")
    if problems:
        return False, problems
    new = {f: max(s["score"], baseline.get(f, 0.0)) for f, s in scores.items()}
    if new != baseline:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"files": dict(sorted(new.items()))}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, []


def _run_mutmut(repo_root: Path) -> tuple[int, str]:
    # mutmut はソース関数のハッシュが変わらない限り *.py.meta の判定をキャッシュから再利用する。
    # テストだけを変えた場合に古い判定が残りラチェットを誤判定しうるので、毎回 mutants/ を消す。
    shutil.rmtree(repo_root / "mutants", ignore_errors=True)
    try:
        proc = subprocess.run(  # noqa: S603 -- argv は MUTMUT_CMD に固定
            MUTMUT_CMD, capture_output=True, encoding="utf-8", errors="replace",
            cwd=repo_root, check=False,
        )
    except OSError as exc:
        return 1, f"{MUTMUT_CMD[0]}: {exc}"
    return proc.returncode, proc.stdout + proc.stderr


def run_mutation(
    repo_root: Path = REPO_ROOT, runner: Callable[[Path], tuple[int, str]] | None = None
) -> bool:
    """mutmut を実行し、ファイル別 score を baseline とラチェット比較する。"""
    run = runner or _run_mutmut
    started = time.monotonic()
    code, output = run(repo_root)
    elapsed = time.monotonic() - started
    if code != 0:
        print(output[-FAIL_OUTPUT_TAIL:], file=sys.stderr)
        print(f"[verify] mutation: FAIL (mutmut exit {code})")
        return False
    scores = mutation_scores(repo_root)
    if not scores:
        print("mutants/ に変異結果(*.py.meta)が無い。[tool.mutmut] の only_mutate を確認する", file=sys.stderr)
        print("[verify] mutation: FAIL (no results)")
        return False
    for rel, s in sorted(scores.items()):
        print(f"  {rel:<28} {s['score']:>5}  ({s['killed']}/{s['total']} killed)")
    ok, problems = check_mutation_baseline(repo_root, scores)
    for p in problems:
        print(f"  ! {p}")
    print(f"[verify] mutation: {'ok' if ok else 'FAIL'} ({elapsed:.0f}s)")
    return ok
```

`main` を:

```python
def main(argv: Sequence[str]) -> int:
    stages = [*STAGES, "mutation", "all"]
    if len(argv) != 1 or argv[0] not in stages:
        print(f"usage: verify.py {{{'|'.join(stages)}}}", file=sys.stderr)
        return 2
    if argv[0] == "mutation":
        return 0 if run_mutation() else 1
    if argv[0] == "all":
        return 0 if run_stage("quick") and run_mutation() else 1
    return 0 if run_stage(argv[0]) else 1
```

モジュール docstring の 2 段落目を「`mutation` は mutmut を毎回フル実行し、ファイル別 score を
`tests/mutation-baseline.json` とラチェット比較する。`all` は quick 成功後に mutation。どちらも
Stop ゲート・CI には載せない(約 3 分)」に更新。

- [ ] **Step 3: 通す・コミット**

Run: `uv run pytest tests/test_verify.py -q && uv run python scripts/verify.py quick`
Expected: pass(pyright strict が `runner` の型などで文句を言えば注釈を直す)。

```bash
git add scripts/verify.py tests/test_verify.py
git commit -m "feat(verify): mutation ステージ(mutmut フル実行 + ファイル別 score のラチェット)と all"
```

---

### Task 4: baseline の初期化と「落ちるべきときに落ちる」確認

**Files:**
- Create: `tests/mutation-baseline.json`(ランナーが生成)

- [ ] **Step 1: 初回実行**

Run: `time uv run python scripts/verify.py mutation; echo exit=$?`
Expected: 6 ファイルの表(スパイクと同程度: config 86 / fingerprint 78 / hook_io 0 / log 73 / state 92 / status 69)、
`[verify] mutation: ok (約 170s)`、exit 0、`tests/mutation-baseline.json` が生成される。所要時間を記録する。

- [ ] **Step 2: 落ちることの確認(記録のみ、コミットしない)**

`tests/test_state.py` の `test_書いた値がそのまま読める` の本体を `pass` にして
`uv run python scripts/verify.py mutation` → `state.py` の score が下がり `! hooks/lib/state.py: score …
< baseline …` と `FAIL`、exit 1 になることを確認。`git checkout tests/test_state.py` で戻す。
baseline が書き換わっていないこと(`git diff tests/mutation-baseline.json` が空)を確認。結果を Task 8 で spec に書く。

- [ ] **Step 3: コミット**

```bash
git add tests/mutation-baseline.json
git commit -m "test: mutation の初期 baseline(トリアージ前)"
```

---

### Task 5: トリアージ ① `hook_io` と `log`

**Files:**
- Create: `tests/test_hook_io.py`
- Modify: `tests/test_log.py`
- Modify: `tests/mutation-baseline.json`(ランナーが更新)

**目標:** `hook_io.py` ≥ 85、`log.py` ≥ 85。

- [ ] **Step 1: 現状の生き残りを見る**

Run: `uv run mutmut results | grep -E "hooks\.lib\.(hook_io|log)\." | head -40` と、代表的なキーを
`uv run mutmut show <key>`。`hook_io` は全件 no tests(直接呼ぶテストが無い)。

- [ ] **Step 2: `tests/test_hook_io.py` を書く(直接呼ぶ)**

```python
"""hook_io: stdin の JSON を読み、stdout に JSON を書く。subprocess を通さず直接呼ぶ(mutmut 用)。"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import hook_io  # noqa: E402


def test_read_eventはdictをそのまま返す(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name": "Stop", "cwd": "/home/USER/r"}'))
    assert hook_io.read_event() == {"hook_event_name": "Stop", "cwd": "/home/USER/r"}


def test_read_eventはdict以外なら空dict(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2]"))
    assert hook_io.read_event() == {}


def test_read_eventは壊れたJSONなら空dict(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert hook_io.read_event() == {}


def test_read_eventは空入力なら空dict(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert hook_io.read_event() == {}


def test_emitは1行のJSONを改行つきで書く(capsys):
    hook_io.emit({"systemMessage": "ゲート"})
    out = capsys.readouterr().out
    assert out == '{"systemMessage": "ゲート"}\n'
    assert json.loads(out) == {"systemMessage": "ゲート"}
```

`ensure_ascii=False` の変異(`True`)は `"ゲート"` がエスケープされることで殺せる。

- [ ] **Step 3: `tests/test_log.py` に足す(スパイクの生き残り)**

```python
def test_tsはUTCのISO形式で秒まで():
    import re
    root = "/home/USER/repo-ts"
    log.append(root, {"event": "Stop", "decision": "skipped"})
    ts = log.tail(root, 1)[0]["ts"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts
    from datetime import datetime, timezone
    parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60


def test_上限ちょうどでは切り詰めない():
    root = "/home/USER/repo-edge"
    for i in range(log.MAX_LINES):
        log.append(root, {"event": "Stop", "decision": "skipped", "i": i})
    assert len(log.tail(root, log.MAX_LINES + 10)) == log.MAX_LINES


def test_上限を1超えたらKEEP_LINESに切り詰める():
    root = "/home/USER/repo-edge2"
    for i in range(log.MAX_LINES + 1):
        log.append(root, {"event": "Stop", "decision": "skipped", "i": i})
    recs = log.tail(root, log.MAX_LINES + 10)
    assert len(recs) == log.KEEP_LINES and recs[0]["i"] == log.MAX_LINES
```

- [ ] **Step 4: mutation で確認し、残りを潰す**

Run: `uv run pytest -q && uv run python scripts/verify.py mutation`
Expected: `hook_io.py` と `log.py` の score が上がり baseline が更新される。目標未達なら
`uv run mutmut results` / `show` で生き残りを見て、意味のある変異(等価変異でないもの)にテストを足す。
等価変異(挙動が変わらないもの)は殺さなくてよい — 報告に一覧を書く。目標に届かない場合は
理由を報告に書き、baseline はランナーが出した値のまま(手で触らない)。

- [ ] **Step 5: コミット**

```bash
git add tests/test_hook_io.py tests/test_log.py tests/mutation-baseline.json
git commit -m "test: hook_io を直接呼ぶテストと log の書式・境界テスト(mutation トリアージ①)"
```

---

### Task 6: トリアージ ② `fingerprint` と `config`

**Files:**
- Modify: `tests/test_fingerprint.py`, `tests/test_config.py`, `tests/mutation-baseline.json`

**目標:** `fingerprint.py` ≥ 85、`config.py` ≥ 85(維持)。

- [ ] **Step 1: 生き残りを見る**

`uv run mutmut results | grep -E "hooks\.lib\.(fingerprint|config)\."`、`mutmut show` で正体を確認。

- [ ] **Step 2: スパイクで判明した穴のテストを書く**

`tests/test_fingerprint.py`(モジュール冒頭で `import subprocess` があるか確認して足す):

```python
def test_gitはタイムアウトつきで呼ばれる(monkeypatch, tmp_path):
    """スパイク: timeout=None の変異が生き残っていた。git が固まるとゲートがフックの timeout まで止まる。"""
    seen: dict[str, object] = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(fingerprint.subprocess, "run", fake_run)
    fingerprint.repo_root(str(tmp_path))
    assert seen.get("timeout") == fingerprint.GIT_TIMEOUT_SEC
    assert seen.get("capture_output") is True


def test_gitがタイムアウトすればNone(monkeypatch, tmp_path):
    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(fingerprint.subprocess, "run", boom)
    assert fingerprint.repo_root(str(tmp_path)) is None
    assert fingerprint.compute(str(tmp_path), {"watch": ["*"], "ignore": []}) is None
```

`tests/test_config.py`:

```python
def test_エラーはキー名_errorで返す():
    """呼び出し側(gate / session_start / status)が "_error" in cfg で分岐する。キー名を固定する。"""
    assert set(config._validate({})) == {"_error"}
    assert set(config._validate({"gate": {"command": ""}})) == {"_error"}


def test_作業ツリーが読めずHEADにも無ければエラー(tmp_path, monkeypatch):
    """スパイク: `if committed is None` の反転が生き残っていた。"""
    import subprocess as sp
    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    p = tmp_path / config.CONFIG_NAME
    p.write_text("{}", encoding="utf-8")
    real_is_file = Path.is_file

    def boom(self):
        if self.name == config.CONFIG_NAME:
            raise OSError("permission denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", boom)
    result = config.load(str(tmp_path))
    assert result is not None and "_error" in result and "cannot read" in result["_error"]
```

(`test_config.py` の既存 import に `Path` があるか確認。無ければ `from pathlib import Path` を足す。)

- [ ] **Step 3: mutation で確認し、残りを潰す**

Run: `uv run pytest -q && uv run python scripts/verify.py mutation`
`fingerprint.is_watched` / `_changed_paths` の境界(リネーム行の読み飛ばし、`ignore` 優先)など、
生き残りが示す穴にテストを足す。目標未達の扱いは Task 5 Step 4 と同じ。

- [ ] **Step 4: コミット**

```bash
git add tests/test_fingerprint.py tests/test_config.py tests/mutation-baseline.json
git commit -m "test: fingerprint の git timeout と config の経路・キー名(mutation トリアージ②)"
```

---

### Task 7: トリアージ ③ `status`(ゴールデン)

**Files:**
- Modify: `tests/test_status.py`, `tests/mutation-baseline.json`

**目標:** `status.py` ≥ 80。

- [ ] **Step 1: ゴールデンテストを書く**

`tests/test_status.py` の末尾(`repo`、`GATE`、`status`、`log`、`config` は既に import 済み):

```python
def test_renderのゴールデン_有効で未検証(tmp_path, monkeypatch):
    """render の書式(ラベル幅・区切り・文言)を固定する。mutation で書式の変異を一括で殺す。"""
    monkeypatch.setattr(config, "plugin_version", lambda: "9.9.9")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "pass", "ms": 1234})
    info = status.collect(root)
    info["fingerprint"] = "f" * 64
    out = status.render(info)
    ts = info["recent"][0]["ts"][:16].replace("T", " ")
    expected = "\n".join([
        "loop-hooks status (9.9.9)",
        f"  repo      {root}",
        "  config    HEAD (.loop-hooks.json)",
        f"  command   {GATE['command']}",
        "  on        stop, subagent_stop, teammate_idle",
        "  watch     *.ts",
        "  ignore    *.md",
        "  timeout   600s",
        "  state     changed since last pass -> gate will run at next stop",
        "  blocked   no",
        f"  records   {tmp_path / 'data' / 'state'}",
        f"  recent    {ts:<16} Stop          ran       pass  1.2s",
    ])
    assert out == expected


def test_renderのゴールデン_設定なし(tmp_path):
    import subprocess as sp
    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    out = status.render(status.collect(str(tmp_path)))
    assert out.splitlines()[1:] == [
        f"  repo      {tmp_path}",
        "  config    no .loop-hooks.json -> gate inactive in this repository",
    ]


def test_format_recentは各項目を幅つきで並べる():
    r = {"ts": "2026-08-27T01:02:03Z", "event": "SubagentStop", "decision": "skipped"}
    assert status._format_recent(r) == "2026-08-27 01:02 SubagentStop  skipped"
    r2 = {"ts": "2026-08-27T01:02:03Z", "event": "Stop", "decision": "ran", "result": "fail",
          "ms": 10811, "note": "fingerprint unavailable"}
    assert status._format_recent(r2) == "2026-08-27 01:02 Stop          ran       fail  10.8s fingerprint unavailable"
```

`GATE` の `on` / `timeout_sec` は `config.GATE_DEFAULTS` から補われる(`on` 3 つ、`timeout_sec` 600)。
実際の出力と 1 文字でも違えば `repr` を見て期待値を直す — ただし**期待値を実装に合わせる**のは
初回だけ。以降は実装を変えたら期待値も意図して変える。

- [ ] **Step 2: mutation で確認し、残りを潰す**

Run: `uv run pytest -q && uv run python scripts/verify.py mutation`
Expected: `status.py` ≥ 80。未達なら `collect` 側(`_recent` の境界、`will_run` / `blocked` の式)にテストを足す。

- [ ] **Step 3: コミット**

```bash
git add tests/test_status.py tests/mutation-baseline.json
git commit -m "test: status.render のゴールデン(mutation トリアージ③)"
```

---

### Task 8: 文書と 0.5.0 リリース準備

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `README.ja.md`, `CHANGELOG.md`, `pyproject.toml`, `.claude-plugin/plugin.json`, `uv.lock`
- Modify: `docs/superpowers/specs/2026-08-27-phase3-mutation-design.md`(§3 に実測)

- [ ] **Step 1: CLAUDE.md**

「開発」節に追加:

```markdown
- 検証一式(mutation 込み): `uv run python scripts/verify.py all`(約 3 分。コミット前・フェーズ完了時に回す)。
  `tests/mutation-baseline.json` はランナーだけが上げる。手で下げない。`mutants/` は作業領域で触らない
```

規約 5 の後に:

```markdown
6. import は `from hooks.lib import …`(ルート起点)。`sys.path` にはプラグインルートを入れる。
   `from lib import …` に戻すと mutmut の変異キーが合わなくなる(第 3 段階 spec §2.1)。
```

- [ ] **Step 2: README 英日**

`README.md` の Tests 節の末尾に:

```markdown
`uv run python scripts/verify.py all` adds mutation testing (mutmut over `hooks/lib`, about three
minutes) with a per-file score ratchet in `tests/mutation-baseline.json`; it is not part of the gate.
```

`README.ja.md` の対応節に同趣旨の一文。Pairings の「Mutation testing with a ratchet」節の末尾に
「This repository does exactly this for itself; see `scripts/verify.py mutation`.」(日本語版も)。

- [ ] **Step 3: バージョンと CHANGELOG**

`pyproject.toml` / `.claude-plugin/plugin.json` を `0.5.0`、`uv lock`。`CHANGELOG.md` の先頭(`## [0.4.0]` の上):

```markdown
## [0.5.0] - 2026-08-27

### Added
- **Mutation testing with a ratchet for the repository itself.** `uv run python
  scripts/verify.py mutation` runs mutmut over `hooks/lib` (N mutants, about M seconds),
  scores each file, and fails when a file drops below `tests/mutation-baseline.json`;
  the runner raises the baseline itself when a score improves. `all` = `quick` + `mutation`.
  Neither is part of the end-of-turn gate or CI.
- Tests added by the first triage: `hook_io` is now called directly (it was only exercised
  through subprocesses), `fingerprint` pins the git timeout, `log` pins the UTC timestamp
  format and trim boundaries, `status.render` has golden output.

### Changed
- **Imports are rooted at the plugin directory** (`from hooks.lib import …`) so that
  mutmut's mutant keys match runtime module names. The hook entry files did not move and
  `hooks.json` is unchanged — **no restart is needed**.

### Upgrading
- Nothing to do.
```

`N` / `M` は Task 4 の実測に置き換える。

- [ ] **Step 4: spec §3 に実測を追記し、全体検証・コミット**

spec §3 の末尾に「確認済み(YYYY-MM-DD): `all` X 分 / baseline: config … / テスト無効化で mutation が
FAIL(Task 4 Step 2)/ 未達のファイルと理由」を書く。

Run: `uv run python scripts/verify.py all; echo exit=$?` → exit 0。

```bash
git add CLAUDE.md README.md README.ja.md CHANGELOG.md pyproject.toml .claude-plugin/plugin.json uv.lock docs
git commit -m "chore: 0.5.0 のリリース準備(CHANGELOG、バージョン、mutation の運用を文書化)"
```

---

### Task 9: 受け入れ(main マージ後、手動を含む)

- [ ] **Step 1: push 後、CI の `test`(3.10 / 3.14)と `security` が green**
- [ ] **Step 2: GitHub 版プラグインを更新して再起動せずに** `/loop-hooks:status` と次の SessionStart 告知が
  動くことを確認(import 変更が入口の実行に影響しないことの実証。更新自体は
  `/plugin marketplace update loop-hooks` → `/plugin update loop-hooks@loop-hooks`。
  ※更新後の初回セッションは再起動で始まるので、「再起動不要」は既存セッションで
  `gate.py` が新コードで走ることを判定ログ(`ran`)で見る)
- [ ] **Step 3: spec の「レビュー状況」に第 3 段階完了、親 spec の表を更新、計画をアーカイブ**

```bash
git mv docs/superpowers/plans/2026-08-27-phase3-mutation.md docs/superpowers/archive/plans/
git add docs
git commit -m "docs: 第 3 段階の受け入れ結果を記録し、計画をアーカイブ"
```

---

## 自己レビュー

**Spec coverage:** §2.1 → Task 1 / §2.2 → Task 2 / §2.3 → Task 3 / §2.4 → Task 3(ハーネステスト)+ Task 4 Step 2(落ちる確認)/ §2.5 → Task 4〜7 / §2.6 → Task 8 / §3 → Task 4(所要時間)、Task 8(記録)、Task 9。

**判断(spec に無いもの):** (1) `mutation` / `all` は `STAGES` に入れず `main` で振り分ける(`STAGES` は CI ミラーの対象なので `Check` 以外を混ぜない)。(2) baseline には `updated` 時刻を入れない(差分ノイズを避ける)。(3) Task 1 で名前空間パッケージが import-linter に通らなければ `hooks/__init__.py` を作る分岐を明記。(4) トリアージの「等価変異は殺さない」を明記。

**型整合:** `mutation_scores(Path) -> dict[str, dict[str, Any]]`、`check_mutation_baseline(Path, scores) -> tuple[bool, list[str]]`、`run_mutation(repo_root=REPO_ROOT, runner=None) -> bool`、`BASELINE_REL = Path("tests") / "mutation-baseline.json"`、`MUTATION_KILLED_CODES`、`MUTMUT_CMD` は Task 3 の実装とテストで同名。Task 1 の `imports` Check の `shell_line` は `PYTHONPATH=. uv run lint-imports --config pyproject.toml` で CI 行と一致。
