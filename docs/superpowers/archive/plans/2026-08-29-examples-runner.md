# examples/ runner template and configs (0.10.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 利用者が 3 手順で loop-hooks の運用(stage 分割・CI 一致)を始められるよう、stdlib のみの verify runner テンプレートとスタック別の `.loop-hooks.json` を `examples/` に同梱する。

**Architecture:** `examples/verify.py` は 1 ファイル・stdlib のみのランナー(`STAGES` 表 + `<stage>` / `all` / `--print-ci`)。`examples/<stack>/.loop-hooks.json` は 4 つ。`tests/test_examples.py` がテンプレートを一時ディレクトリで subprocess 実行して出力形式と終了コードを固定し、JSON 例が `config.load` を通ることを固定する。`hooks/` は無変更。

**Tech Stack:** Python 3.10+(stdlib)、pytest、ruff、pyright。

**Spec:** `docs/superpowers/specs/2026-08-29-examples-runner-design.md`

## Global Constraints

- `examples/verify.py` は stdlib のみ、Python 3.10 で動く(`str | None` は可、`match` 文は不可ではないが使わない)、200 行以内。
- 出力形式は厳密に: 成功 `[verify] <name>: ok`、失敗 `[verify] <name>: FAIL (exit <code>)` / `FAIL (timeout after 600s)` / `FAIL (command not found: <cmd[0]>)`。最初の失敗で停止、exit 1。全部通れば exit 0。未知の stage は stderr `unknown stage: <name> (known: quick, slow, all)` で exit 2。
- `REPO_ROOT = Path(__file__).resolve().parent.parent`、`CHECK_TIMEOUT_SEC = 600`、`FAIL_OUTPUT_TAIL = 4000`。
- テンプレートに `# --- STAGES BEGIN ---` / `# --- STAGES END ---` のマーカー行を置く(テストが差し替える)。
- `hooks/`・`skills/`・`hooks.json` は変更しない(再起動不要)。`scripts/verify.py` は `lint` / `format` に `examples` を足すだけ。CI の Lint / Format 行も同じく足す(`tests/test_verify.py::test_quick_stage_mirrors_ci` と `test_quickにformatチェックがある` を更新)。
- import は `from hooks.lib import …`(ルート起点)。lib は例外を外に出さない(変更しないので該当なし)。
- 実ホームパスをソース・コミットメッセージに書かない(プレースホルダーは `/home/USER`)。
- ゲート `uv run python scripts/verify.py quick` は各コミット前に緑。`quick` の増分 ≤ 1 秒。
- 各タスクは foreground で実行し、subagent を使わない。コミットメッセージは日本語の既存流儀。

---

### Task 1: `examples/verify.py` テンプレートとその subprocess テスト

**Files:**
- Create: `examples/verify.py`
- Create: `tests/test_examples.py`
- Modify: `scripts/verify.py`(`lint` / `format` の対象に `examples` を追加)、`.github/workflows/ci.yml`(Lint / Format の 2 行)、`pyproject.toml`(`[tool.pyright]` の `include` / `strict` に `examples`)、`tests/test_verify.py`(`test_quickにformatチェックがある` の期待 cmd)

**Interfaces:**
- Produces: `examples/verify.py`(CLI 仕様は Global Constraints)、テストヘルパー `_run_template(tmp_path, stages_src: str, *args) -> subprocess.CompletedProcess[str]`(Task 2 は使わない)。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_examples.py`)**

```python
"""examples/ の同梱物のテスト(0.10.0)。

examples/verify.py は利用者が scripts/ にコピーして使うテンプレート。ここでは一時ディレクトリの
scripts/verify.py にコピーし、STAGES を差し替えて subprocess で実行し、出力形式と終了コードを固定する。
"""

import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "examples" / "verify.py"
MARKER_RE = re.compile(r"# --- STAGES BEGIN ---\n.*?# --- STAGES END ---\n", re.S)


def _run_template(tmp_path: Path, stages_src: str, *args: str) -> subprocess.CompletedProcess[str]:
    """テンプレートを tmp_path/scripts/verify.py に置き、STAGES を stages_src に差し替えて実行する。"""
    src = TEMPLATE.read_text(encoding="utf-8")
    assert MARKER_RE.search(src), "テンプレートに STAGES BEGIN/END マーカーが無い"
    src = MARKER_RE.sub("# --- STAGES BEGIN ---\n" + stages_src + "# --- STAGES END ---\n", src)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "verify.py").write_text(src, encoding="utf-8")
    return subprocess.run(  # noqa: S603 -- argv は固定
        [sys.executable, str(scripts / "verify.py"), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


OK_STAGES = (
    'STAGES: dict[str, list[Check]] = {\n'
    '    "quick": [Check("a", ["true"]), Check("b", ["true"])],\n'
    '    "slow": [Check("c", ["true"])],\n'
    "}\n"
)


def test_全部通ればexit0でokが並ぶ(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "quick")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == ["[verify] a: ok", "[verify] b: ok"]


def test_失敗したらexit1でFAIL行が出て後続は走らない(tmp_path):
    marker = tmp_path / "ran-c"
    stages = (
        'STAGES: dict[str, list[Check]] = {\n'
        '    "quick": [Check("a", ["true"]), Check("b", ["false"]), Check("c", ["touch", '
        + repr(str(marker))
        + "])],\n"
        "}\n"
    )
    r = _run_template(tmp_path, stages, "quick")
    assert r.returncode == 1
    lines = r.stdout.splitlines()
    assert lines[0] == "[verify] a: ok"
    assert lines[1] == "[verify] b: FAIL (exit 1)"
    assert not marker.exists()


def test_失敗時は出力の末尾が続く(tmp_path):
    stages = (
        'STAGES: dict[str, list[Check]] = {\n'
        '    "quick": [Check("a", ["sh", "-c", "echo DETAIL_LINE; exit 3"])],\n'
        "}\n"
    )
    r = _run_template(tmp_path, stages, "quick")
    assert r.returncode == 1
    assert "[verify] a: FAIL (exit 3)" in r.stdout
    assert "DETAIL_LINE" in r.stdout


def test_コマンドが無ければcommand_not_found(tmp_path):
    stages = (
        'STAGES: dict[str, list[Check]] = {\n'
        '    "quick": [Check("a", ["no-such-command-loop-hooks"])],\n'
        "}\n"
    )
    r = _run_template(tmp_path, stages, "quick")
    assert r.returncode == 1
    assert "[verify] a: FAIL (command not found: no-such-command-loop-hooks)" in r.stdout


def test_allは全stageを定義順に走らせる(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "all")
    assert r.returncode == 0
    assert r.stdout.splitlines() == ["[verify] a: ok", "[verify] b: ok", "[verify] c: ok"]


def test_未知のstageはexit2(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "nope")
    assert r.returncode == 2
    assert "unknown stage: nope (known: quick, slow, all)" in r.stderr


def test_print_ciはcheckごとに1行(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "--print-ci", "quick")
    assert r.returncode == 0
    assert r.stdout.splitlines() == [shlex.join(["true"]), shlex.join(["true"])]
    r = _run_template(tmp_path, OK_STAGES, "--print-ci")
    assert r.stdout.splitlines() == ["true", "true", "true"]


def test_テンプレートは同梱のSTAGESでヘルプが出る():
    r = subprocess.run(  # noqa: S603
        [sys.executable, str(TEMPLATE), "--help"], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0
    assert "quick" in r.stdout and "--print-ci" in r.stdout


def test_テンプレートは200行以内でstdlibのみ():
    src = TEMPLATE.read_text(encoding="utf-8")
    assert len(src.splitlines()) <= 200
    imports = re.findall(r"^(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)", src, re.M)
    assert set(imports) <= {"argparse", "dataclasses", "pathlib", "shlex", "subprocess", "sys", "__future__"}
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_examples.py -q`
Expected: FAIL(`examples/verify.py` が無い)

- [ ] **Step 3: テンプレートを書く(`examples/verify.py`)**

```python
#!/usr/bin/env python3
"""Verify runner template for loop-hooks.

Copy this file to `scripts/verify.py` in your repository, edit the STAGES table
below, and point `.loop-hooks.json` at it:

    {"gate": {"command": "python scripts/verify.py quick"}}

Usage:
    python scripts/verify.py quick        # run one stage
    python scripts/verify.py all          # run every stage in definition order
    python scripts/verify.py --print-ci   # print the `run:` line for each check

Output is one line per check: `[verify] <name>: ok` or `[verify] <name>: FAIL (...)`
followed by the tail of the command output. The runner stops at the first failure
and exits 1; exits 0 when every check passes; exits 2 for an unknown stage.
loop-hooks records the first FAIL line as the failure reason in `--status`.

Standard library only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # this file lives in <repo>/scripts/
CHECK_TIMEOUT_SEC = 600
FAIL_OUTPUT_TAIL = 4000


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]  # argv, run from REPO_ROOT without a shell
    ok_codes: frozenset[int] = frozenset({0})  # exit codes that count as success


# --- STAGES BEGIN ---
# Edit this table. Keep `quick` under ~30 seconds: it runs at every turn end.
# Move anything slower (mutation testing, end-to-end suites) to `slow`.
#
# Other stacks:
#   node:  Check("lint", ["bun", "run", "lint"]), Check("tests", ["bun", "test"])
#   rust:  Check("fmt", ["cargo", "fmt", "--check"]), Check("tests", ["cargo", "test", "-q"])
#   go:    Check("vet", ["go", "vet", "./..."]), Check("tests", ["go", "test", "./..."])
STAGES: dict[str, list[Check]] = {
    "quick": [
        Check("lint", ["ruff", "check", "."]),
        Check("format", ["ruff", "format", "--check", "."]),
        Check("tests", ["pytest", "-q"]),
    ],
    # e.g. Check("mutation", ["mutmut", "run"]) — see scripts/verify.py in the loop-hooks
    # repository for a per-file score ratchet.
    "slow": [],
}
# --- STAGES END ---


def shell_line(check: Check) -> str:
    """The line to put under `run:` in CI so CI and the gate stay identical."""
    return shlex.join(check.cmd)


def run_check(check: Check) -> tuple[bool, str]:
    try:
        r = subprocess.run(  # noqa: S603 -- argv is fixed in STAGES
            check.cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False, f"FAIL (timeout after {CHECK_TIMEOUT_SEC}s)"
    except FileNotFoundError:
        return False, f"FAIL (command not found: {check.cmd[0]})"
    except OSError as exc:
        return False, f"FAIL (could not run: {exc})"
    if r.returncode in check.ok_codes:
        return True, "ok"
    output = (r.stdout or "") + (r.stderr or "")
    return False, f"FAIL (exit {r.returncode})\n{output[-FAIL_OUTPUT_TAIL:]}"


def run_stage(name: str, checks: list[Check]) -> bool:
    for check in checks:
        ok, detail = run_check(check)
        print(f"[verify] {check.name}: {detail}", flush=True)
        if not ok:
            return False
    return True


def stages_for(name: str) -> list[tuple[str, list[Check]]] | None:
    if name == "all":
        return list(STAGES.items())
    if name in STAGES:
        return [(name, STAGES[name])]
    return None


def main(argv: list[str]) -> int:
    known = ", ".join([*STAGES, "all"])
    parser = argparse.ArgumentParser(description=f"Run a verification stage ({known}).")
    parser.add_argument("stage", nargs="?", default="quick", help=f"one of: {known}")
    parser.add_argument(
        "--print-ci", action="store_true", help="print the CI `run:` line for each check and exit"
    )
    args = parser.parse_args(argv)
    if args.print_ci and len(argv) == 1:
        selected = list(STAGES.items())  # --print-ci without a stage: every stage
    else:
        selected = stages_for(args.stage)
    if selected is None:
        print(f"unknown stage: {args.stage} (known: {known})", file=sys.stderr)
        return 2
    if args.print_ci:
        for _, checks in selected:
            for check in checks:
                print(shell_line(check))
        return 0
    for name, checks in selected:
        if not run_stage(name, checks):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_examples.py -q`
Expected: PASS(9 件)

- [ ] **Step 5: lint / format / pyright / CI の対象に `examples` を足す**

`scripts/verify.py` の `STAGES["quick"]`:

```python
        Check("lint", ["uv", "run", "ruff", "check", "hooks", "tests", "scripts", "examples"]),
        Check("format", ["uv", "run", "ruff", "format", "--check", "hooks", "tests", "scripts", "examples"]),
```

`.github/workflows/ci.yml`:

```yaml
      - name: Lint
        run: uv run ruff check hooks tests scripts examples
      - name: Format
        run: uv run ruff format --check hooks tests scripts examples
```

`pyproject.toml`:

```toml
[tool.pyright]
include = ["hooks", "scripts", "examples"]
strict = ["hooks", "scripts", "examples"]
```

`tests/test_verify.py::test_quickにformatチェックがある` の期待値:

```python
    assert fmt.cmd == ["uv", "run", "ruff", "format", "--check", "hooks", "tests", "scripts", "examples"]
```

`ruff` の行長は 100。`scripts/verify.py` の `format` 行が超えるなら `Check(` の引数を複数行に割る。

- [ ] **Step 6: ゲートを通す**

Run: `uv run python scripts/verify.py quick`
Expected: exit 0(pyright strict がテンプレートに文句を言うなら型注釈を足す。`subprocess.run` の戻り値は `CompletedProcess[str]`)。`quick` の所要時間を報告に書く(0.9.0: 14.6 秒)。

- [ ] **Step 7: コミット**

```bash
git add examples/verify.py tests/test_examples.py scripts/verify.py .github/workflows/ci.yml pyproject.toml tests/test_verify.py
git commit -m "feat(examples): verify runner のテンプレートを同梱(stdlib のみ、quick/all/--print-ci)"
```

---

### Task 2: スタック別 `.loop-hooks.json` と `examples/README.md`

**Files:**
- Create: `examples/python-uv/.loop-hooks.json`, `examples/node-bun/.loop-hooks.json`, `examples/rust-cargo/.loop-hooks.json`, `examples/go/.loop-hooks.json`
- Create: `examples/README.md`
- Modify: `tests/test_examples.py`(追記)、`README.md`(Pairings 冒頭)、`README.ja.md`(組み合わせ 冒頭)

**Interfaces:**
- Consumes: `hooks.lib.config.load(root) -> dict | None`(`_error` / `_source` / `_notice` / `gate` を持つ)。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_examples.py` に追記)**

```python
import shutil

from hooks.lib import config

EXAMPLES = REPO_ROOT / "examples"
EXPECTED_COMMANDS = {
    "python-uv": "uv run python scripts/verify.py quick",
    "node-bun": "bun run lint && bun test",
    "rust-cargo": "cargo fmt --check && cargo clippy -q -- -D warnings && cargo test -q",
    "go": "gofmt -l . | (! grep .) && go vet ./... && go test ./...",
}


def test_設定例は4つ():
    dirs = sorted(p.name for p in EXAMPLES.iterdir() if (p / ".loop-hooks.json").is_file())
    assert dirs == sorted(EXPECTED_COMMANDS)


@pytest.mark.parametrize("stack", sorted(EXPECTED_COMMANDS))
def test_設定例はconfigの検証を通る(stack, tmp_path):
    # git リポジトリではない一時ディレクトリにコピーして作業ツリー版として読む(HEAD 優先を避ける)
    shutil.copy(EXAMPLES / stack / ".loop-hooks.json", tmp_path / ".loop-hooks.json")
    cfg = config.load(str(tmp_path))
    assert cfg is not None and "_error" not in cfg, cfg
    assert cfg["gate"]["command"] == EXPECTED_COMMANDS[stack]
    assert cfg["gate"]["timeout_sec"] in (300, 600)
    assert cfg["gate"]["watch"] and cfg["gate"]["ignore"]


def test_READMEはexamplesへリンクする():
    for name in ("README.md", "README.ja.md"):
        assert "examples/README.md" in (REPO_ROOT / name).read_text(encoding="utf-8"), name
    assert (EXAMPLES / "README.md").is_file()
```

`import pytest` をファイル先頭の import 群に足す(既に `re` / `shlex` / `subprocess` / `sys` がある)。

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_examples.py -q -k "設定例 or README"`
Expected: FAIL(ディレクトリが無い)

- [ ] **Step 3: 4 つの JSON を書く**

`examples/python-uv/.loop-hooks.json`:

```json
{
  "gate": {
    "command": "uv run python scripts/verify.py quick",
    "timeout_sec": 300,
    "watch": ["*.py", "pyproject.toml"],
    "ignore": [".venv/*", "*/.venv/*", ".hypothesis/*"]
  }
}
```

`examples/node-bun/.loop-hooks.json`:

```json
{
  "gate": {
    "command": "bun run lint && bun test",
    "timeout_sec": 300,
    "watch": ["*.ts", "*.tsx", "package.json", "*tsconfig*.json"],
    "ignore": ["node_modules/*", "*/node_modules/*", "dist/*"]
  }
}
```

`examples/rust-cargo/.loop-hooks.json`:

```json
{
  "gate": {
    "command": "cargo fmt --check && cargo clippy -q -- -D warnings && cargo test -q",
    "timeout_sec": 600,
    "watch": ["*.rs", "Cargo.toml", "Cargo.lock"],
    "ignore": ["target/*"]
  }
}
```

`examples/go/.loop-hooks.json`:

```json
{
  "gate": {
    "command": "gofmt -l . | (! grep .) && go vet ./... && go test ./...",
    "timeout_sec": 300,
    "watch": ["*.go", "go.mod", "go.sum"],
    "ignore": ["vendor/*"]
  }
}
```

- [ ] **Step 4: `examples/README.md` を書く**

```markdown
# Examples

Ready-to-copy pieces for gating a repository with loop-hooks. Everything here is
a starting point: adjust the commands to the scripts your repository actually has.

## Verify runner template (`verify.py`)

A single-file, standard-library-only runner that splits verification into stages
and prints one line per check (`[verify] lint: ok` / `[verify] tests: FAIL (exit 1)`).
loop-hooks records the first `FAIL` line as the failure reason in `--status`.

1. Copy `examples/verify.py` to `scripts/verify.py` in your repository (the
   runner resolves the repository root as the parent of `scripts/`).
2. Edit the `STAGES` table at the top: keep `quick` under about 30 seconds
   (the `--status` summary warns beyond that budget), move slower checks to
   `slow`.
3. Put the matching `.loop-hooks.json` from this directory at the repository
   root and commit it — the gate reads the committed version.

```
python scripts/verify.py quick        # one stage
python scripts/verify.py all          # every stage, in definition order
python scripts/verify.py --print-ci   # the `run:` line for each check
```

### Keep CI identical to the gate

`--print-ci quick` prints the exact command line for each check. Paste those
lines into your CI job as separate `run:` steps, in the same order, and add a
test that regenerates them from `STAGES` and compares with the workflow file —
then a green gate implies a green CI run. loop-hooks does this for itself in
`tests/test_verify.py::test_quick_stage_mirrors_ci`. The lines are POSIX-shell
quoted; Windows runners are out of scope.

### Mutation testing with a ratchet

Not part of the template. See `scripts/verify.py mutation` in this repository
for a per-file killed-count baseline that only the runner may raise.

## Configuration examples

| Directory | `gate.command` | Notes |
| --- | --- | --- |
| `python-uv/` | `uv run python scripts/verify.py quick` | uses the runner template |
| `node-bun/` | `bun run lint && bun test` | expects a `lint` script in `package.json` |
| `rust-cargo/` | `cargo fmt --check && cargo clippy -q -- -D warnings && cargo test -q` | 600 s timeout for cold builds |
| `go/` | `gofmt -l . \| (! grep .) && go vet ./... && go test ./...` | `gofmt -l` lists unformatted files; the pipe fails when it prints any |

`watch` and `ignore` are `fnmatch` patterns against repository-relative paths;
`*` crosses `/`. Omit `on` to gate all three events (`stop`, `subagent_stop`,
`teammate_idle`). See the top-level README for every field.
```

- [ ] **Step 5: README / README.ja にリンクを足す**

`README.md` の `## Pairings` 直下の段落の末尾に 1 文:

```
Ready-to-copy pieces — a standard-library verify runner template and
`.loop-hooks.json` examples for Python, Node, Rust and Go — live in
[`examples/README.md`](examples/README.md).
```

`README.ja.md` の `## 組み合わせ` 直下の段落の末尾に 1 文:

```
そのまま使える verify runner のテンプレート(標準ライブラリのみ)と Python / Node / Rust / Go の
`.loop-hooks.json` の例は [`examples/README.md`](examples/README.md) にある。
```

- [ ] **Step 6: テストとゲート**

Run: `uv run pytest tests/test_examples.py -q` → PASS(15 件)
Run: `uv run python scripts/verify.py quick` → exit 0(`ruff format` は Markdown の fenced code は対象外設定。JSON は ruff の対象外)

- [ ] **Step 7: コミット**

```bash
git add examples tests/test_examples.py README.md README.ja.md
git commit -m "docs(examples): スタック別の .loop-hooks.json と導入手順を同梱"
```

---

### Task 3: 版・CHANGELOG・最終検証・手動確認

**Files:**
- Modify: `CHANGELOG.md`、`pyproject.toml`、`.claude-plugin/plugin.json`、`uv.lock`、`docs/superpowers/specs/2026-08-29-examples-runner-design.md`(§3 に確認結果を追記)

- [ ] **Step 1: CHANGELOG の先頭に追加**

```markdown
## [0.10.0] - 2026-08-29

### Added
- **`examples/`**: a standard-library-only verify runner template (`examples/verify.py` —
  `quick` / `all` / `--print-ci`, one `[verify] <name>: ok|FAIL` line per check, stops at the
  first failure) and `.loop-hooks.json` examples for Python (uv), Node (bun), Rust (cargo) and Go,
  with a three-step setup guide in `examples/README.md`. The template is lint-, format- and
  type-checked in the gate and CI, and exercised by subprocess tests.

### Upgrading
- No restart needed: nothing under `hooks/` changed.
```

- [ ] **Step 2: 版を上げる**

`pyproject.toml` `version = "0.10.0"`、`.claude-plugin/plugin.json` `"version": "0.10.0"`、`uv lock`。

- [ ] **Step 3: 手動確認(spec §3)**

```bash
d=$(mktemp -d) && cd "$d" && git init -q && mkdir scripts && cp /home/USER/loop-hooks/examples/verify.py scripts/ \
  && cp /home/USER/loop-hooks/examples/python-uv/.loop-hooks.json . && git add -A && git -c user.email=t@example.com -c user.name=t commit -qm init \
  && uv run --project /home/USER/loop-hooks python /home/USER/loop-hooks/hooks/gate.py --status . | head -5
```

(`/home/USER` は実パスに読み替える。出力の `command` 行が `uv run python scripts/verify.py quick` であることを確認し、spec §3 の末尾に「確認済み(日付): status の command 行 …」を 1 行追記する。実パスは書かない。)

- [ ] **Step 4: 最終検証**

Run: `uv run python scripts/verify.py all`
Expected: exit 0。`quick` の所要時間を 0.9.0 の 14.6 秒と比べて増分 ≤ 1 秒。mutation baseline は `hooks/lib` のみ対象なので変化しないはず(変化したら diff を報告)。

- [ ] **Step 5: コミット**

```bash
git add CHANGELOG.md pyproject.toml .claude-plugin/plugin.json uv.lock docs/superpowers/specs/2026-08-29-examples-runner-design.md
git commit -m "chore: 0.10.0 のリリース準備(examples/ の同梱を文書化、再起動不要を明記)"
```
