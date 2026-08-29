# gitignore-style watch/ignore (0.11.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `watch` / `ignore` のマッチを `fnmatch` から gitignore 風(gitwildmatch: `*` は 1 階層、`**` が跨ぐ、スラッシュ無しは任意の深さ、`!` で後勝ちの否定)に切り替え、既定 ignore を簡素化する。

**Architecture:** 新モジュール `hooks/lib/patterns.py`(stdlib `re` のみ)がパターンを正規表現に変換し `matches(rel, patterns)` を公開する。`fingerprint.is_watched` がそれを使う。`config.GATE_DEFAULTS["ignore"]` を gitignore 風に書き換える。入口ファイルは無変更(再起動不要)。

**Tech Stack:** Python 3.10+(stdlib)、pytest、hypothesis、import-linter、mutmut。

**Spec:** `docs/superpowers/specs/2026-08-29-gitignore-patterns-design.md`

## Global Constraints

- `hooks/lib/patterns.py` は stdlib(`re` / `functools`)のみ、100 行以内、`subprocess` を使わない、例外を外に出さない(`re.error` はリテラルにフォールバック)。
- 変換規則は spec §2.1 の表のとおり。ディレクトリ一致の伝播(正規表現末尾 `(?:/.*)?$`)、末尾 `/` は `/.*$` 必須。
- 公開 API は `patterns.matches(rel: str, patterns: list[str]) -> bool` のみ(後勝ち、`!` 否定)。内部の `_compiled(tuple[str, ...])` は `functools.lru_cache(maxsize=64)`。
- `.loop-hooks.json` の schema・検証、入口ファイル(`hooks/gate.py` / `hooks/session_start.py` / `hooks.json`)、`status` の表示、判定ログ、contract golden は変更しない。
- `config.GATE_DEFAULTS["ignore"]` の新しい値は `["node_modules/", ".venv/", "dist/", "build/", "target/", ".claude/", ".loop/", "*.md"]`(この順)。
- import-linter: layers `["status", "log", "config", "fingerprint", "patterns", "state", "hook_io"]`、subprocess 禁止の `source_modules` に `hooks.lib.patterns`。mutmut `only_mutate` に `hooks/lib/patterns.py`。
- import は `from hooks.lib import …`(ルート起点)。実ホームパスをソース・コミットメッセージに書かない。
- ゲート `uv run python scripts/verify.py quick` は各コミット前に緑。`quick` の増分 ≤ 1 秒(0.10.0: 約 15.4 秒)。
- 各タスクは foreground で実行し、subagent を使わない。コミットメッセージは日本語の既存流儀。

---

### Task 1: `hooks/lib/patterns.py` と表駆動テスト

**Files:**
- Create: `hooks/lib/patterns.py`
- Create: `tests/test_patterns.py`
- Modify: `pyproject.toml`(import-linter layers / subprocess contract / mutmut only_mutate)

**Interfaces:**
- Produces: `patterns.matches(rel: str, patterns: list[str]) -> bool`、`patterns._compiled(patterns: tuple[str, ...]) -> tuple[tuple[bool, re.Pattern[str] | None], ...]`(テストが `is` 同一性で lru_cache を確認する)。

- [ ] **Step 1: 失敗するテストを書く(`tests/test_patterns.py`)**

```python
"""patterns: watch / ignore の gitignore 風マッチ(0.11.0)。

表は https://git-scm.com/docs/gitignore の規則から。対象は「ファイルのパス列」なので、
パターンが途中のディレクトリに一致したら配下すべてに一致する(git がディレクトリを除外すると
配下を見ないのと同じ)。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import patterns  # noqa: E402


@pytest.mark.parametrize(
    ("pattern", "rel", "expected"),
    [
        # スラッシュ無し = 任意の深さの basename
        ("*.md", "README.md", True),
        ("*.md", "docs/a/b.md", True),
        ("*.py", "a.pyc", False),
        ("node_modules", "node_modules", True),
        ("node_modules", "a/b/node_modules/x.js", True),  # ディレクトリ一致の伝播
        # スラッシュあり = ルート基準
        ("docs/*", "docs/a.md", True),
        ("docs/*", "docs/a/b.md", True),  # docs/a に一致し配下へ伝播(0.10.0 と同じ結果)
        ("docs/*", "src/docs/a.md", False),
        ("/docs", "docs/x", True),
        ("/docs", "a/docs/x", False),
        # * は / を跨がない
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/a/b.py", False),
        ("a*c", "a/c", False),
        ("*", "a/b", True),  # "*" は a に一致し配下へ伝播
        # **
        ("src/**/*.py", "src/a/b.py", True),
        ("src/**/*.py", "src/a.py", True),
        ("**/foo", "a/b/foo", True),
        ("**/foo", "foo", True),
        ("abc/**", "abc/x/y", True),
        ("abc/**", "abc", False),
        ("a/**/b", "a/b", True),
        ("a/**/b", "a/x/y/b", True),
        # 末尾 / = ディレクトリ指定(配下が必要)
        ("node_modules/", "a/b/node_modules/x.js", True),
        ("node_modules/", "node_modules", False),
        ("dist/", "dist/x", True),
        # ? と文字クラス
        ("?.py", "a.py", True),
        ("?.py", "ab.py", False),
        ("[ab].py", "a.py", True),
        ("[!ab].py", "a.py", False),
        ("[!ab].py", "c.py", True),
        ("[", "[", True),  # 閉じない [ はリテラル
        ("[", "x", False),
        # エスケープ・空
        ("\\!x", "!x", True),
        ("*tsconfig*.json", "tsconfig.build.json", True),
        ("", "a", False),
        ("/", "a", False),
    ],
)
def test_単一パターンの一致(pattern, rel, expected):
    assert patterns.matches(rel, [pattern]) is expected


def test_否定は後勝ち():
    assert patterns.matches("a.md", ["*.md", "!a.md"]) is False
    assert patterns.matches("b.md", ["*.md", "!a.md"]) is True
    assert patterns.matches("a.md", ["!a.md", "*.md"]) is True
    assert patterns.matches("a.md", ["!a.md"]) is False


def test_空のリストは何にも一致しない():
    assert patterns.matches("a", []) is False


def test_不正なパターンでも例外を出さない():
    for bad in ("[", "[a", "**", "!", "", "\\", "a[", "[]", "[!]"):
        assert isinstance(patterns.matches("a/b", [bad]), bool), bad


def test_既定のignoreは依存ディレクトリとドキュメントを除きソースを残す():
    from hooks.lib import config

    ignore = config.GATE_DEFAULTS["ignore"]
    for rel in ("a/b/node_modules/x.js", "dist/x", "docs/a.md", ".venv/lib/x.py", "target/debug/x"):
        assert patterns.matches(rel, ignore) is True, rel
    for rel in ("src/a.py", "README.py", "main.ts", "dist"):
        assert patterns.matches(rel, ignore) is False, rel


def test_コンパイル結果はキャッシュされる():
    key = ("*.md", "!a.md")
    assert patterns._compiled(key) is patterns._compiled(key)
```

(`test_既定のignoreは…` は Task 2 で既定が変わるまで `dist` の行で落ちる。Task 1 では
この 1 テストだけ `@pytest.mark.xfail(strict=True, reason="Task 2 で既定 ignore を更新")` を付け、
Task 2 で外す。)

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_patterns.py -q`
Expected: FAIL(`patterns` が無い)

- [ ] **Step 3: 実装(`hooks/lib/patterns.py`)**

```python
"""watch / ignore のパターンマッチ。gitignore(gitwildmatch)と同じ規則。

対象は「リポジトリ相対のファイルパス」の列なので、パターンが途中のディレクトリに一致したら
配下すべてに一致させる(git がディレクトリを除外すると配下を見ないのと同じ)。

- `*` は `/` を跨がない。`**/` `/**` `/**/` が跨ぐ。
- 末尾以外にスラッシュが無ければ任意の深さの basename に一致。あればルート基準。
- 末尾 `/` はディレクトリ指定(配下が必要)。先頭 `!` は否定(後勝ち)。`\\!` はリテラル。
- 不正なパターンは例外にせずリテラル扱い。
"""

from __future__ import annotations

import re
from functools import lru_cache

CACHE_SIZE = 64


def _glob_to_regex(body: str) -> str:
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "*":
            if body.startswith("**", i):
                before = body[i - 1] if i else ""
                after = body[i + 2] if i + 2 < n else ""
                if before in ("", "/") and after == "/":
                    out.append("(?:.*/)?")  # **/ と /**/
                    i += 3
                    continue
                if before in ("", "/") and after == "":
                    out.append(".*")  # /** 末尾
                    i += 2
                    continue
                out.append("[^/]*")  # セグメント途中の ** は * と同じ
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = body.find("]", i + 2)  # "[]" "[!]" は閉じていないとみなす
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                cls = body[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
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
        return negated, re.compile("^" + prefix + _glob_to_regex(pattern) + suffix)
    except re.error:
        return negated, re.compile("^" + prefix + re.escape(pattern) + suffix)


@lru_cache(maxsize=CACHE_SIZE)
def _compiled(patterns: tuple[str, ...]) -> tuple[tuple[bool, re.Pattern[str] | None], ...]:
    return tuple(_translate(p) for p in patterns)


def matches(rel: str, patterns: list[str]) -> bool:
    """rel がパターン列に一致するか。gitignore と同じ後勝ちで、`!` は直前までの一致を取り消す。"""
    result = False
    for negated, regex in _compiled(tuple(patterns)):
        if regex is not None and regex.match(rel):
            result = not negated
    return result
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_patterns.py -q`
Expected: PASS(xfail 1 件を含む)。ruff が複雑度(C901 / PLR0912)で `_glob_to_regex` を咎めたら、`[` の処理を `_char_class(body: str, i: int) -> tuple[str, int]`(正規表現片と次の位置を返す)に切り出す。pyproject の設定で有効でなければ何もしない。

- [ ] **Step 5: `pyproject.toml`**

```toml
[[tool.importlinter.contracts]]
name = "subprocess を使うのは fingerprint だけ"
type = "forbidden"
source_modules = ["hooks.lib.config", "hooks.lib.hook_io", "hooks.lib.log", "hooks.lib.patterns", "hooks.lib.state", "hooks.lib.status"]
forbidden_modules = ["subprocess"]
allow_indirect_imports = true

[[tool.importlinter.contracts]]
name = "lib の層(上が下に依存する)"
type = "layers"
layers = ["status", "log", "config", "fingerprint", "patterns", "state", "hook_io"]
containers = ["hooks.lib"]
```

`[tool.mutmut].only_mutate` に `"hooks/lib/patterns.py"` を追加(`hooks/lib/log.py` の後、アルファベット順)。

- [ ] **Step 6: ゲートを通してコミット**

Run: `uv run python scripts/verify.py quick` → exit 0(`quick` の所要時間を報告に書く)

```bash
git add hooks/lib/patterns.py tests/test_patterns.py pyproject.toml
git commit -m "feat(patterns): gitignore 風のパターンマッチを追加(fnmatch の置き換え用)"
```

---

### Task 2: `fingerprint` / `config` への組み込みとテスト更新

**Files:**
- Modify: `hooks/lib/fingerprint.py`(`is_watched`、`import fnmatch` 削除)、`hooks/lib/config.py`(`GATE_DEFAULTS["ignore"]`)
- Modify: `tests/test_config.py`(既定 ignore のテスト)、`tests/test_properties.py`(P2a〜c + P2d)、`tests/test_architecture.py`(no-raise)、`tests/test_patterns.py`(xfail を外す)

**Interfaces:**
- Consumes: `patterns.matches(rel, patterns) -> bool`(Task 1)。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_config.py` の `test_ignoreの既定に依存ディレクトリとドキュメントが含まれる` を置き換える:

```python
def test_ignoreの既定はgitignore風で依存ディレクトリとドキュメントを除く(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok"}})
    ignore = config.load(cwd)["gate"]["ignore"]
    assert ignore == [
        "node_modules/",
        ".venv/",
        "dist/",
        "build/",
        "target/",
        ".claude/",
        ".loop/",
        "*.md",
    ]
```

`tests/test_properties.py`: `import fnmatch` を削除し、`from hooks.lib import config, fingerprint, log, patterns, state` に。P2b / P2c の `assume(not any(fnmatch.fnmatch(rel, p) for p in …))` を `assume(not patterns.matches(rel, …))` に置き換え、P2c の直後に追加:

```python
@settings(deadline=None)
@given(rel=_rel_paths, watch=_patterns)
def test_P2d_否定を末尾に足すと一致が取り消される(rel: str, watch: list[str]):
    assert patterns.matches(rel, watch + [rel]) is True
    assert patterns.matches(rel, watch + [rel, "!" + rel]) is False
```

`tests/test_architecture.py` の `test_存在しないディレクトリでも例外を出さない` の直後に追加(`patterns` を import 行に足す):

```python
def test_patternsは壊れたパターンでも例外を出さない():
    for bad in (["["], ["**"], ["!"], [""], ["\\"], ["[", "**", "!", ""]):
        assert isinstance(patterns.matches("a/b", bad), bool), bad
```

`tests/test_patterns.py` の `test_既定のignoreは…` から `xfail` マーカーを外す。

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_config.py tests/test_patterns.py -q`
Expected: FAIL(既定 ignore が旧値)

- [ ] **Step 3: 実装**

`hooks/lib/fingerprint.py`: `import fnmatch` を削除し、`from hooks.lib import patterns` を(既存の lib import の並びに合わせて)追加。`is_watched` を:

```python
def is_watched(rel: str, gate_cfg: dict[str, Any]) -> bool:
    """リポジトリ相対パスがゲート対象か。gitignore 風のマッチで、ignore は watch より優先。"""
    if patterns.matches(rel, gate_cfg["ignore"]):
        return False
    return patterns.matches(rel, gate_cfg["watch"])
```

`hooks/lib/config.py` の `GATE_DEFAULTS["ignore"]`:

```python
    "ignore": [
        "node_modules/",
        ".venv/",
        "dist/",
        "build/",
        "target/",
        ".claude/",
        ".loop/",
        "*.md",
    ],
```

コメント「既定は「全部見張り、明らかな雑音だけ除く」…」はそのまま。

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest -q`
Expected: PASS。`tests/test_fingerprint.py` の `is_watched` テスト(`GATE = {"watch": ["*.ts", "package.json"], "ignore": [".loop/*", "*.md", "docs/*"]}`)は結果が変わらないはず。変わったら spec §2.3 と照らして報告する(期待値を黙って直さない)。

- [ ] **Step 5: ゲートを通してコミット**

Run: `uv run python scripts/verify.py quick` → exit 0(`lint-imports` の layers も含む)

```bash
git add hooks/lib/fingerprint.py hooks/lib/config.py tests/test_config.py tests/test_properties.py tests/test_architecture.py tests/test_patterns.py
git commit -m "feat(fingerprint): watch/ignore を gitignore 風マッチに切り替え、既定 ignore を簡素化"
```

---

### Task 3: ドキュメント・版・最終検証

**Files:**
- Modify: `README.md`、`README.ja.md`、`examples/README.md`、`CHANGELOG.md`、`pyproject.toml`、`.claude-plugin/plugin.json`、`uv.lock`、`tests/mutation-baseline.json`(runner が書く)

- [ ] **Step 1: README.md**

`gate.ignore` の表の既定値を `["node_modules/", ".venv/", "dist/", "build/", "target/", ".claude/", ".loop/", "*.md"]` に。段落「Patterns are `fnmatch` against repository-relative paths. Note that **`*` crosses `/`**: `docs/*` also matches `docs/a/b.md`.」を次に置き換える:

```
Patterns follow `.gitignore` rules, matched against repository-relative paths:

- `*` and `?` do not cross `/`; `**/`, `/**` and `/**/` do.
- A pattern without a slash (`*.md`, `node_modules`) matches at any depth; one with a
  slash (`docs/*`, `src/**/*.py`) is anchored at the repository root.
- A trailing `/` names a directory and matches everything under it (`node_modules/`).
- A pattern that matches a directory also matches everything inside it, so `docs/*`
  still covers `docs/a/b.md`.
- `!pattern` cancels an earlier match in the same list; the last match wins.
```

- [ ] **Step 2: README.ja.md**

`gate.ignore` の既定値を同じ値に。段落「パターンはリポジトリ相対パスに対する `fnmatch`。**`*` は `/` もまたぐ**ので、`docs/*` は `docs/a/b.md` にも一致する。」を:

```
パターンは `.gitignore` と同じ規則で、リポジトリ相対パスに対して照合する:

- `*` と `?` は `/` を跨がない。`**/`、`/**`、`/**/` が跨ぐ。
- スラッシュを含まないパターン(`*.md`、`node_modules`)は任意の深さに一致。含むもの(`docs/*`、
  `src/**/*.py`)はリポジトリルート基準。
- 末尾の `/` はディレクトリ指定で、その配下すべてに一致する(`node_modules/`)。
- ディレクトリに一致したパターンはその中のすべてにも一致するので、`docs/*` は `docs/a/b.md` も覆う。
- `!pattern` は同じリストの先行する一致を取り消す。後勝ち。
```

- [ ] **Step 3: examples/README.md**

「`watch` and `ignore` are `fnmatch` patterns against repository-relative paths; `*` crosses `/`.」を
「`watch` and `ignore` follow `.gitignore` rules against repository-relative paths (`*` stays within one directory, `**` crosses, a trailing `/` names a directory, `!` negates).」に。

- [ ] **Step 4: CHANGELOG.md の先頭に追加**

```markdown
## [0.11.0] - 2026-08-29

### Changed
- **`watch` / `ignore` follow `.gitignore` rules** instead of `fnmatch`: `*` and `?` stay within
  one directory, `**` crosses directories, a pattern without a slash matches at any depth, a
  trailing `/` names a directory, and `!pattern` cancels an earlier match (last match wins). A
  pattern that matches a directory still covers everything inside it, so `docs/*`, `.loop/*` and
  `skills/**/*.md` keep their meaning.
- The default `ignore` is now `["node_modules/", ".venv/", "dist/", "build/", "target/",
  ".claude/", ".loop/", "*.md"]` — directories are ignored at any depth, and the duplicated
  `*/node_modules/*`-style entries are gone.

### Upgrading
- Rewrite patterns that relied on `*` crossing `/`: `src/*.py` (any depth under `src/`) becomes
  `src/**/*.py`. Patterns like `*.py`, `docs/*`, `.loop/*` and `**/*.md` need no change.
- The gate may run once on the first turn after the update because the watched set can differ.
- No restart needed: nothing in `hooks/gate.py` / `hooks/session_start.py` changed.
```

- [ ] **Step 5: 版を上げる**

`pyproject.toml` `version = "0.11.0"`、`.claude-plugin/plugin.json` `"version": "0.11.0"`、`uv lock`。

- [ ] **Step 6: 最終検証**

Run: `uv run python scripts/verify.py all`
Expected: exit 0。`quick` は 0.10.0(約 15.4 秒)+ 1 秒以内。`tests/mutation-baseline.json` に `hooks/lib/patterns.py` が追加され、既存ファイルの killed が下がらないこと(diff を報告に書く)。`fingerprint.py` は total が変わるので再基準化される。

- [ ] **Step 7: コミット**

```bash
git add README.md README.ja.md examples/README.md CHANGELOG.md pyproject.toml .claude-plugin/plugin.json uv.lock tests/mutation-baseline.json
git commit -m "chore: 0.11.0 のリリース準備(gitignore 風マッチを文書化、移行案内、再起動不要を明記)"
```
