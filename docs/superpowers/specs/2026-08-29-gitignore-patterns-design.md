# loop-hooks 0.11.0 設計書 — `watch` / `ignore` の gitignore 風マッチ

作成日: 2026-08-29
前提: 0.10.0(main = b84d6c0)。親 spec `2026-08-26-verification-roadmap-design.md` §6.4 の候補 D。
関連: 0.3.0 spec(`watch` / `ignore` と既定 ignore の導入)、第 4 段階 spec(P2 の PBT)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-29 チャットで合意: 既定で切り替え・設定キーは足さない / `!` 否定は後勝ち / 自前の変換器(stdlib のみ)/ 既定 ignore を簡素化 / `hooks/` の lib 変更のみで再起動不要) |

## 1. 目的

`watch` / `ignore` は `fnmatch` で、`*` が `/` を跨ぐ。そのため既定 ignore は `node_modules/*` と
`*/node_modules/*` を二重に持ち、README に「`*` は `/` を跨ぐ」という注意書きが要る。`.gitignore` に
慣れた利用者の直感(`*` は 1 階層、`**` が跨ぐ、スラッシュ無しは任意の深さ、`!` で除外解除)と
食い違う。

マッチを gitignore 風(gitwildmatch)に切り替える。設定 schema は変えず、既定で切り替える。
`hooks/gate.py` / `hooks/session_start.py` は変更しないので **再起動不要**(lib はフック起動ごとに
import される)。

## 2. 設計

### 2.1 `hooks/lib/patterns.py`(新規、stdlib `re` / `functools` のみ)

公開 API は 1 つ:

```python
def matches(rel: str, patterns: list[str]) -> bool:
    """リポジトリ相対パス rel が patterns に一致するか。gitignore と同じ後勝ち。
    `!` 先頭のパターンは直前までの一致を取り消す。例外は外に出さない。"""
```

変換規則(`.gitignore` の仕様、https://git-scm.com/docs/gitignore に準拠):

| パターン | 意味 |
|---|---|
| 空文字列 | 何にも一致しない |
| 先頭 `!` | 否定(後勝ちで直前の一致を取り消す)。`\!` は先頭のリテラル `!` |
| 先頭 `/` | ルート基準(剥がすだけ) |
| 末尾以外に `/` を含む | ルート基準。含まなければ任意の深さの basename に一致(`(^|.*/)` を前置) |
| 末尾 `/` | ディレクトリ指定。剥がしたうえで「配下すべて」に一致 |
| `**/` 先頭 | 任意の深さ(0 個以上のディレクトリ) |
| `/**` 末尾 | 配下すべて |
| `/**/` 中間 | 0 個以上のディレクトリ |
| `*` | `[^/]*` |
| `?` | `[^/]` |
| `[...]` | 文字クラス(`[!` → `[^`)。閉じない `[` はリテラル |
| その他 | `re.escape` |

**ディレクトリ一致の伝播**: 変換した正規表現の末尾に `(/.*)?$` を付け、パターンがパスの途中の
ディレクトリに一致すればその配下すべてに一致させる(`docs/*` は `docs/a/b.md` に一致 —
現状と同じ結果。git がディレクトリを除外すると配下を見ないのと同じ)。

- 実装: `_translate(pattern) -> tuple[bool, re.Pattern | None]`(否定フラグと正規表現。空なら None)。
  `re.error` は `re.escape` した全体をリテラルとしてコンパイルし直す。`_compiled(patterns: tuple[str, ...])`
  を `functools.lru_cache(maxsize=64)` で持ち、`matches` は `tuple(patterns)` で引く。
- `matches` の判定: `result = False` から始め、各パターンについて一致すれば `result = not negated`。
  最後の値を返す。
- 100 行以内。`subprocess` を使わない(import-linter の contract に追加)。

### 2.2 利用側の変更

- `hooks/lib/fingerprint.py`: `is_watched` を
  `return not patterns.matches(rel, gate_cfg["ignore"]) and patterns.matches(rel, gate_cfg["watch"])`
  に。`import fnmatch` を削除。docstring に「gitignore 風。ignore は watch より優先」。
- `hooks/lib/config.py`: `GATE_DEFAULTS["ignore"]` を
  `["node_modules/", ".venv/", "dist/", "build/", "target/", ".claude/", ".loop/", "*.md"]` に。
  `watch` の既定 `["*"]` はそのまま(basename `*` = 任意の深さの全ファイル)。schema・検証は不変
  (不正なパターンは設定エラーにせず、リテラル扱い)。
- `pyproject.toml`: import-linter の layers を
  `["status", "log", "config", "fingerprint", "patterns", "state", "hook_io"]`、subprocess 禁止の
  `source_modules` に `hooks.lib.patterns` を追加。mutmut の `only_mutate` に `hooks/lib/patterns.py`
  を追加(baseline は runner が新規ファイル分を追加する)。
- `hooks/gate.py` / `hooks/session_start.py` / `hooks/lib/status.py` / `hooks.json` / `skills/` は無変更。

### 2.3 互換性

| 書き方 | 0.10.0(fnmatch) | 0.11.0(gitignore 風) |
|---|---|---|
| `*.py`, `*.md` | 任意の深さ | 任意の深さ(同じ) |
| `docs/*`, `.loop/*`, `mutants/*` | `docs/` 配下すべて | `docs/` 直下に一致し配下へ伝播(同じ結果) |
| `skills/**/*.md`, `.github/**/*.yml` | 同じ | 同じ |
| `node_modules/*` + `*/node_modules/*` | ルート直下 + 1 階層下 | `node_modules/*` はルートのみ。任意の深さは `node_modules/` 1 つで足りる |
| `src/*.py` | `src/` 配下の任意の深さ | `src/` 直下のみ → **`src/**/*.py` に書き換える** |
| `*tsconfig*.json` | 同じ | 同じ |

自リポジトリの `.loop-hooks.json` と README の設定例は書き換え不要。`examples/*/.loop-hooks.json` は
`*/node_modules/*` 型の重複を `node_modules/` 1 つに書き換える(最終レビューで判明: 旧形では
`packages/a/node_modules/` が ignore から外れる)。
更新後の最初のターンで fingerprint の対象集合が変わりうるため、ゲートが 1 回走ることがある(無害)。

### 2.4 変更しないもの

- `.loop-hooks.json` の schema(`watch` / `ignore` は文字列のリスト)。
- 入口ファイル、`status` の表示、判定ログ、contract golden(入出力は不変)。

## 3. 受け入れ条件

- `tests/test_patterns.py`: 表駆動 30 例程度(§2.1 の各行 + `*` が `/` を跨がない + 否定の後勝ち +
  `\!` + 閉じない `[` + ディレクトリ一致の伝播)。既定 ignore が `a/b/node_modules/x.js`、`dist/x`、
  `docs/a.md` を除き `src/a.py`、`README.py` を残す。`lru_cache` が同じタプルで同じオブジェクトを返す。
- `tests/test_properties.py` P2a〜c: `fnmatch` の `assume` を `patterns.matches` に置換。
  P2d 新規: 任意の `rel` と一致する `patterns` に `"!" + rel` を末尾に足すと `matches` が False。
- `tests/test_architecture.py`: lib の no-raise 表に `patterns.matches("a/b", ["[", "**", "!", ""])`。
  layers の contract が緑(`lint-imports`)。
- `tests/test_config.py`: 既定 ignore の新しい値。`tests/test_fingerprint.py`: `is_watched` の既存テストが
  そのまま緑(結果が変わるケースがあれば §2.3 に照らして期待値を直し、spec に記録)。
- `uv run python scripts/verify.py all` exit 0。`quick` 増分 ≤ 1 秒。baseline に `hooks/lib/patterns.py`
  が追加される(他ファイルの killed は下がらない)。
- 0.11.0(`pyproject.toml` / `plugin.json` / `uv.lock`)、CHANGELOG(Changed: マッチ規則と既定 ignore、
  Upgrading: `src/*.py` → `src/**/*.py` の書き換え案内、再起動不要)。
- README / README.ja: パターン段落を gitignore 風の規則(5 行)に、既定 ignore の表を更新。
  `examples/README.md` の fnmatch 記述を更新。

## 4. リスク

| リスク | 対処 |
|---|---|
| 利用者の設定で `*` が深い階層を拾わなくなる | CHANGELOG に書き換え例。ゲートは「走らない」側に倒れず、watch が狭まるだけ。`--status` の `watch` 行で確認できる |
| 自前変換器の gitignore との差異 | 表駆動テストを git の仕様書の例で書く。対象は「ファイルのパス列」なので、ディレクトリ伝播を明示的に足す |
| 正規表現の性能 | パターンは数個〜十数個、パスは数百件。`lru_cache` で毎回のコンパイルを避ける |
