# loop-hooks 第 2 段階 設計書 — 静的検査の拡充(フォーマッタ・型・セキュリティ・import 契約)→ 0.4.0

作成日: 2026-08-27
前提: 0.3.2(main = cf71196)。第 1 段階(自リポジトリのゲート、`scripts/verify.py quick`)完了
親: `2026-08-26-verification-roadmap-design.md` §3(本書はその子 spec。親 §3 と食い違う点は本書が優先し、親に追記する)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-27 チャットで合意: pyright は strict、tests は `S101,S603` を per-file-ignore、CI は `test` / `security` の 2 ジョブ) |

## 1. 目的

第 1 段階でゲートは掛かった。第 2 段階は、**ゲートが走らせる検査の中身を厚くする**。追加するのは
すべて決定論的で速い検査(1 秒前後)で、`quick` の予算 15 秒に収める。確率的な検査(モデルによる
レビュー)は入れない。

## 2. 実測(2026-08-27、main = cf71196)

| 検査 | 現状 | 所要 |
|---|---|---|
| `ruff format --check hooks tests scripts` | 19/20 ファイルが要整形、約 250 行(hooks 127 行)。差は行の折返し方が主で、引用符の変更はほぼ無い | 0.2 秒 |
| pyright strict(`hooks` + `scripts`、pythonVersion 3.10) | **0 エラー** | 1.4 秒 |
| ruff `S`(hooks / scripts) | 3 件: `hooks/gate.py` S602(`shell=True`)、`hooks/lib/fingerprint.py` S603、`scripts/verify.py` S603。すべて設計どおりの subprocess | — |
| ruff `S`(tests、S101 除外後) | 11 件、すべて S603(テストが git を argv リストで呼ぶ) | — |
| zizmor(`.github/workflows`) | 4 件: `unpinned-uses` ×2(high: `actions/checkout@v4`、`astral-sh/setup-uv@v5`)、`excessive-permissions`(medium: `permissions` 未指定)、`artipacked`(medium: `persist-credentials: false` 無し) | — |
| pip-audit(`uv export` 経由) | 既知の脆弱性なし(実行時依存ゼロ、dev 依存のみ) | — |
| import-linter(`lib` に 3 契約) | 層 KEPT / 入口禁止 KEPT / subprocess 契約は `allow_indirect_imports = True` が必要 | 0.55 秒 |
| `quick` ベースライン | leak → ruff check → pytest | 10.5 秒 |

## 3. 設計

### 3.1 フォーマッタ — `ruff format`

- 初回は **`style: ruff format を適用` の単独コミット**(挙動変更なし、レビューは「format だけか」を見る)。
- `[tool.ruff]` は既存(`line-length = 100`)。`format` の追加設定は入れない(既定に従う)。
- `quick` と CI に `uv run ruff format --check hooks tests scripts` を `ruff check` の直後に追加。

### 3.2 セキュリティ lint — ruff `S`

- `[tool.ruff.lint] select` に `S` を追加(現状の既定セット + `E402` + `S`)。
- hooks / scripts の 3 件は**行単位の `# noqa: S60x -- <理由>`** で受け入れる。理由は信頼境界の文書化:
  - `gate.py`: 「コマンドは HEAD の `.loop-hooks.json` から読む(0.2.1)。シェル実行が機能そのもの」
  - `fingerprint.py`: 「argv は固定の git サブコマンド。入力はパスのみ」
  - `scripts/verify.py`: 「argv は `STAGES` に固定。ユーザー入力なし」
- **tests は `per-file-ignores = {"tests/*" = ["S101", "S603"]}`**。S101(assert)はテストの本質、S603 は
  argv リストでの git 呼出 11 件で、行単位 noqa はノイズにしかならない。親 spec §3.1 の
  「ファイル単位の除外をしない」は hooks / scripts に対する規則とし、tests の例外を親に追記する。
- ファイル単位・ルール単位の除外は hooks / scripts では引き続き禁止。

### 3.3 import 依存の契約 — import-linter

`pyproject.toml` の `[tool.importlinter]`:

```toml
[tool.importlinter]
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

- 実行は `hooks/` を作業ディレクトリに `PYTHONPATH=. lint-imports`。`scripts/verify.py` の `Check` に
  `cwd` を足す(現状は全チェックがリポジトリルート固定)。CI も同じコマンド。
- 入口(`gate.py` / `session_start.py`)はパッケージでないため対象外(親 §3.2)。
- `import-linter` を dev 依存に追加。

### 3.4 型検査 — pyright strict

- `[tool.pyright]`: `include = ["hooks", "scripts"]`、`strict = ["hooks", "scripts"]`、`pythonVersion = "3.10"`。
  実測で strict が 0 エラーなので basic を経由しない。
- `tests` は初期は対象外。計画のスパイクで basic が 0 エラーなら `include` に足す(strict にはしない。
  monkeypatch 等の動的コードと相性が悪い)。
- `quick` と CI に `uv run pyright` を追加。`pyright` を dev 依存に追加(uv が Node ランタイムを同梱する
  wheel を解決する。CI の 3.10 / 3.14 両方で動くことを確認する)。

### 3.5 CI — `test` / `security` の 2 ジョブ

`test` ジョブ = `quick` の鏡(順序も同じ):

```
leak → ruff check → ruff format --check → pyright → lint-imports → pytest
```

`security` ジョブ(`quick` には入れない。外部ツール取得とネットワークが要るため):

```
uvx zizmor --min-severity low .github/workflows
uv export --format requirements-txt --no-hashes | uvx pip-audit -r /dev/stdin
```

`ci.yml` 自体の修正(zizmor をゼロにする):

- トップレベル `permissions: contents: read`
- `actions/checkout` / `astral-sh/setup-uv` を **コミット SHA でピン留め**し `# vX.Y.Z` コメントを付ける
  (Dependabot が追随する)
- `actions/checkout` に `persist-credentials: false`

`tests/test_verify.py::test_quick_stage_mirrors_ci` は **`test` ジョブの `run` ステップだけ**を抽出して
比較するよう更新する(`security` ジョブは対象外)。

### 3.6 Dependabot

`.github/dependabot.yml`: `github-actions`(週次)と Python 依存(週次、`uv` エコシステム。計画の
スパイクで `package-ecosystem: "uv"` が受理されるか確認し、駄目なら `pip` + `uv.lock` 非対応を
記録して Actions だけにする)。`groups` で 1 PR にまとめる。

### 3.7 GitHub リポジトリ設定 — secret scanning / push protection

コードでは出来ない。`gh api -X PATCH repos/wwwcojp/loop-hooks` の `security_and_analysis` で
`secret_scanning` と `secret_scanning_push_protection` を `enabled` にする。**リポジトリ設定の
変更なので、実行前にユーザーの承認を得る**(計画の最終タスク、手動扱い)。

## 4. 受け入れ条件

- `quick` の所要時間 ≤ 15 秒(見積: 10.5 + 1.4 + 0.6 + 0.2 ≈ 13 秒。実測を本書に追記)。
- pyright strict 0 エラー / import-linter 3 契約 KEPT / ruff `S` は受け入れ済み noqa 3 件のみ。
- 契約を意図的に破る変更(`state.py` に `from . import log`)で lint-imports が BROKEN になり、
  ミラーテストが `ci.yml` の変更で落ちることを確認(実装時)。
- zizmor ゼロ検出(`--min-severity low`)。CI の `security` ジョブ green。
- Dependabot の初回 PR が届く(Actions のピン留め更新)。
- secret scanning / push protection が `enabled`(`gh api` で確認)。
- 0.4.0 として CHANGELOG・バージョン。再起動は不要(入口ファイル・hooks.json は触らない)。

## 5. スコープ外

- semgrep(親 §3.1 の保留判断を維持)。
- `tests` の strict 型検査。
- mutation / PBT / アーキテクチャテストの残り(第 3〜5 段階)。
- 親 §6.1 のセキュリティ不変条件テストは第 5 段階のまま。ただし noqa の理由文で信頼境界は文書化する。

## 6. リスク

| リスク | 対策 |
|---|---|
| `ruff format` の一括整形がレビュー困難 | 単独コミット。レビューは `git diff -w --stat` と `ruff check` / pytest の green で「整形だけ」を確認 |
| pyright の wheel が CI の 3.14 で解決できない | 計画冒頭のスパイクで `uv add --dev pyright` → 両バージョンで `uv run pyright` を確認。駄目なら `uvx pyright` に切替え |
| Dependabot の `uv` エコシステム未対応 | スパイクで判定。未対応なら Actions のみ + 記録 |
| SHA ピン留めで Actions の更新が止まる | Dependabot(`github-actions`)が SHA + コメントを更新する |
| `quick` が 15 秒を超える | pyright を `all` に降ろす(親 §3 の判断を維持) |
