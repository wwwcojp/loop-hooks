# loop-hooks 0.10.0 設計書 — verify runner テンプレートと設定例の同梱(`examples/`)

作成日: 2026-08-29
前提: 0.9.0(main = 498512a)。親 spec `2026-08-26-verification-roadmap-design.md` §6.4「verify ランナーの利用者向け同梱(`examples/`)」。
関連: README "Pairings"(stage 分割・CI 一致・ratchet)、0.8.0 spec(`failure_reason` は最初の `FAIL` 行を拾う)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-29 チャットで合意: テンプレートランナー + スタック別設定例 + README / stdlib のみ / 自リポジトリ固有部分は含めない / `hooks/` 無変更で再起動不要) |

## 1. 目的

README の Pairings は「stage 分割」「CI と同じコマンド」「formatter を gate に含める」を勧めるが、
利用者が持ち帰れる実体がない。`scripts/verify.py` は自リポジトリ専用(leak 正規表現・mutmut・
baseline ratchet・hypothesis profile が一体)で、そのままでは流用できない。

利用者が 3 手順(ランナーをコピー → `STAGES` を書き換え → `.loop-hooks.json` を置いてコミット)で
同じ運用を始められるよう、汎用ランナーのテンプレートとスタック別の設定例を `examples/` に同梱する。
`hooks/` は変更しない(**再起動不要のリリース**)。

## 2. 設計

### 2.1 `examples/verify.py`(テンプレートランナー)

- Python 3.10+、stdlib のみ(`subprocess` / `shlex` / `dataclasses` / `argparse` / `sys` / `pathlib`)。
  1 ファイル、200 行以内。
- 先頭に利用者が書き換える部分だけを置く:

```python
@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    ok_codes: frozenset[int] = frozenset({0})


STAGES: dict[str, list[Check]] = {
    "quick": [
        Check("lint", ["ruff", "check", "."]),
        Check("format", ["ruff", "format", "--check", "."]),
        Check("tests", ["pytest", "-q"]),
    ],
    "slow": [],  # e.g. Check("mutation", ["mutmut", "run"]) — see scripts/verify.py in loop-hooks
}
```

  コメントで node(`["bun", "run", "test"]`)/ rust(`["cargo", "test"]`)/ go(`["go", "test", "./..."]`)
  の置き換え例を示す。
- CLI(`argparse`):
  - `verify.py <stage>`: その stage の check を順に実行。`all` は `STAGES` の全 stage を定義順に。
  - `verify.py --print-ci [stage]`: 各 check について CI の `run:` に書く 1 行(`shlex.join(cmd)`)を出す。
    stage 省略時は全 stage。exit 0。
  - 未知の stage は stderr に `unknown stage: <name> (known: quick, slow, all)` を出して exit 2。
- 実行: check ごとに `subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=CHECK_TIMEOUT_SEC)`。
  `REPO_ROOT = Path(__file__).resolve().parent.parent`(`scripts/` 配下に置く前提。README に明記)。
  `CHECK_TIMEOUT_SEC = 600`。
- 出力(自リポジトリ版と同じ形。0.8.0 の `failure_reason` が最初の `FAIL` 行を拾う):
  - 成功: `[verify] <name>: ok`
  - 失敗: `[verify] <name>: FAIL (exit <code>)` に続けて stdout+stderr の末尾 `FAIL_OUTPUT_TAIL = 4000` 字。
    timeout は `[verify] <name>: FAIL (timeout after 600s)`。コマンドが見つからない(`FileNotFoundError`)は
    `[verify] <name>: FAIL (command not found: <cmd[0]>)`。
  - 最初の失敗で止まる(後続 check は実行しない)。exit 1。全部通れば exit 0。
- 自リポジトリ固有のもの(leak 正規表現、mutmut、baseline、hypothesis profile、`env` / `cwd` 付き Check)は
  含めない。mutation ratchet は README から `scripts/verify.py` を参照するにとどめる。

### 2.2 `examples/<stack>/.loop-hooks.json`

4 つ。すべて `config.load` の検証を通る(`_error` なし)こと。

| stack | command | watch | ignore | timeout_sec |
|---|---|---|---|---|
| `python-uv` | `uv run python scripts/verify.py quick` | `*.py`, `pyproject.toml` | `.venv/*`, `*/.venv/*`, `.hypothesis/*` | 300 |
| `node-bun` | `bun run lint && bun test` | `*.ts`, `*.tsx`, `package.json`, `*tsconfig*.json` | `node_modules/*`, `*/node_modules/*`, `dist/*` | 300 |
| `rust-cargo` | `cargo fmt --check && cargo clippy -q -- -D warnings && cargo test -q` | `*.rs`, `Cargo.toml`, `Cargo.lock` | `target/*` | 600 |
| `go` | `gofmt -l . \| (! grep .) && go vet ./... && go test ./...` | `*.go`, `go.mod`, `go.sum` | `vendor/*` | 300 |

`python-uv` だけランナー経由(テンプレートの使い方の例)。他は素のコマンド連結。`on` は省略(既定の 3 イベント)。

### 2.3 `examples/README.md`(英語)

- 導入 3 手順: `examples/verify.py` を `scripts/verify.py` にコピー → `STAGES` を書き換え → 該当 stack の
  `.loop-hooks.json` をリポジトリルートに置いてコミット。
- CI 一致: `python scripts/verify.py --print-ci quick` の出力を CI の `run:` に貼り、両者が一致することを
  検査するテストの書き方(loop-hooks の `tests/test_verify.py::test_quick_stage_mirrors_ci` を参照)。
- stage の分け方: `quick` は 30 秒以内(`--status` の `summary` が警告する予算)、それ以上は `slow` に。
- mutation ratchet: `scripts/verify.py mutation` を参照。
- `README.md` / `README.ja.md` の Pairings 冒頭に `examples/` への 1 行リンクを足す。

### 2.4 テスト(`tests/test_examples.py`)

- 4 つの JSON が `config.load(<examples/<stack>>)` で `_error` を持たず、`command` が表 §2.2 と一致する
  (JSON を直接読んで表と突き合わせる。`load` は HEAD 優先なので、一時ディレクトリに git init せず
  コピーして「作業ツリー版」として読む。`_notice` は無視)。
- テンプレートを一時ディレクトリの `scripts/verify.py` にコピーし、`STAGES` を書き換えて subprocess で実行:
  - `quick` が `true` × 2 → exit 0、stdout に `[verify] a: ok` `[verify] b: ok`。
  - `false` を含む → exit 1、`[verify] b: FAIL (exit 1)` が出て後続 check は走らない(marker ファイル)。
  - `--print-ci quick` → check ごとに 1 行、`shlex.join` と一致。
  - 未知の stage → exit 2、stderr に `unknown stage`。
  - `all` → 全 stage が定義順に走る。
  - `command not found` → exit 1、`FAIL (command not found: …)`。
  `STAGES` の書き換えは、テンプレート内の `STAGES` 定義ブロックをテストが正規表現で置換する
  (`# --- STAGES BEGIN ---` / `# --- STAGES END ---` のマーカー行をテンプレートに置く)。
- `examples/verify.py` を `ruff` / `pyright` の対象に含める: `scripts/verify.py` の `lint` / `format` に
  `examples` を追加、`pyproject.toml` の `[tool.pyright].include` に `examples` を追加、CI の該当 2 行も
  追加(`test_quick_stage_mirrors_ci` が検出)。
- `.loop-hooks.json` の `watch` は `*.py` / `*.json` が `examples/` 配下にもマッチするので変更しない。

### 2.5 変更しないもの

- `hooks/`(入口・lib)、`skills/`、`hooks.json`。再起動不要。
- `scripts/verify.py` の stage 定義(`examples` パスの追加のみ)。
- README の Pairings 本文(リンク 1 行の追加のみ)。

## 3. 受け入れ条件

- §2.4 のテストがすべて緑。`uv run python scripts/verify.py all` exit 0。`quick` 増分 ≤ 1 秒
  (subprocess を起動するテストは 6 本、各 0.1 秒程度)。
- 0.10.0(`pyproject.toml` / `plugin.json` / `uv.lock`)、CHANGELOG(Added: `examples/`。Upgrading:
  再起動不要と明記)。
- `examples/README.md` の手順どおりに一時リポジトリへ導入し、`uv run hooks/gate.py --status <dir>` で
  `command   uv run python scripts/verify.py quick` が表示される(手動確認、spec に記録)。

## 4. リスク

| リスク | 対処 |
|---|---|
| テンプレートが自リポジトリ版と乖離して古くなる | 出力形式(`[verify] name: ok/FAIL`)をテストで固定。構造は意図的に小さく保つ |
| 設定例のコマンドが利用者環境で動かない(`bun run lint` が未定義など) | README に「コマンドは各自の scripts に合わせる」と明記。例は形を示すもの |
| `--print-ci` の行が CI の YAML 上で引用符の扱いを要する | `shlex.join` は POSIX shell 向け。README に「そのまま `run:` に貼る」と書き、Windows は対象外と明記 |
