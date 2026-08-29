# loop-hooks 0.8.0 設計書 — 失敗の可観測性と技術負債の解消

作成日: 2026-08-29
前提: 0.7.0(main = 91ddb10)。検証ロードマップ(`2026-08-26-verification-roadmap-design.md`)全 5 段階完了。
関連: 0.3.0 observability spec(判定ログの導入)、第 5 段階 spec(`FP_UNAVAILABLE_KEY` の deferred)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-29 チャットで合意: `reason` の抽出は「最初の FAIL/ERROR 行、なければ最後の非空行」/ summary はゲートが有効なときに status に 1 行 / 予算警告は定数 30 秒 / F の負債 3 件を同梱 / gate.py 変更のため再起動要件を明記) |

## 1. 目的

0.7.0 までの判定ログは `ran fail` と所要時間しか持たず、運用 2 日間の 62 回の fail について
「どの check が落ちたか」を後から答えられなかった(集計も手作業だった)。ゲートの価値を運用者が
確認でき、以後の拡張(並行セッション、watch パターン、runner 同梱)の判断材料になるよう、
失敗内容の記録と集計表示を加える。あわせて第 5 段階で deferred にした技術負債 3 件を解消する。

`hooks/gate.py`(入口)を変更するため、**再起動が必要なリリース**になる(CLAUDE.md 2 項)。

## 2. 設計

### 2.1 失敗内容の記録(`hooks/gate.py` → `hooks/lib/log.py`)

判定記録(`decision="ran"`, `result` が `fail` / `warn`)に `reason: str` を追加する。

- 抽出規則(`log.failure_reason(output: str) -> str`、`hooks/lib/log.py` に置く純関数):
  1. 出力を行に分け、正規表現 `\b(FAIL|FAILED|ERROR)\b|\berror:` に**最初に一致した行**を採る
     (verify runner: `[verify] lint: FAIL`、pytest: `FAILED tests/...`、ruff: `E501 ...` は不一致だが
     runner の `[verify] lint: FAIL` が先に出る)。
  2. 一致がなければ**最後の非空行**(`Found 2 errors.`、`1 failed, 310 passed in 12.15s`)。
  3. 先頭と末尾の空白を除き、**120 字**で切る。空なら `""`。
- `run_gate` の戻り値は変えない。`handle` の fail 経路で `rec["reason"] = log.failure_reason(detail)` を
  設定する。timeout(`timed out after Ns`)と実行不能(`could not run: …`)は `detail` がその 1 行なので
  規則 2 でそのまま入る。
- `reason` はログ専用。エージェントへ返す `additionalContext` / `systemMessage` は変えない
  (contract golden は無変更のはず。変わった場合は契約変更として golden を更新し記録する)。
- 旧形式の行(`reason` なし)はそのまま読める。

### 2.2 集計行(`hooks/lib/status.py`)

`collect` に `summary: dict | None` を追加し、`render` はゲートが有効なとき(設定が読めて `command` があるとき)に
`summary` 行を 1 行出す。設定なし・設定エラーの早期 return では従来どおり出さない(0.3.0 以来の短い表示を保つ)。
`/loop-hooks:status` の SKILL.md は無変更(`--status` の出力をそのまま表示している)。

- 集計対象: `log.tail(root, log.MAX_LINES)` の全行(最大 1,200 行、ファイルの上限と同じ)。
- `summary` の内容: `records`(行数)、`since`(最古の `ts`)、`ran`、`pass`、`fail`、`warn`、`skipped`、
  `median_ms`(`ms` を持つ行の中央値、なければ `None`)、`slow`(§2.3)。
- 表示(値は 0.7.0 運用ログの実測):

```
  summary   654 records since 2026-08-26 13:10: ran 256 (pass 164 / fail 62 / warn 30), skipped 382, median 11.5s
```

- ログが空なら `summary   (no records)`。`recent` の各行には `reason` があれば末尾に付ける
  (`note` の後、既存の `_format_recent` の並び)。
- `collect` の初期辞書に `summary` キーを足す(`test_configが無くてもinfoの全キーが揃う` と
  `INFO_KEYS` を更新)。

### 2.3 予算警告

`status.SLOW_BUDGET_SEC = 30`(設定項目にしない)。`median_ms` または直近 5 件の `ms` の最大が
`SLOW_BUDGET_SEC * 1000` を超えたら `summary["slow"] = True` とし、表示の末尾に
` (slow: over the 30s budget, split the command)` を付ける。

### 2.4 技術負債(第 5 段階の deferred)

- **`FP_UNAVAILABLE_KEY` を `hooks/lib/state.py` へ移す。** `gate._refuse` と `status.collect` の両方が
  `state.FP_UNAVAILABLE_KEY` を参照する。`status.FP_UNAVAILABLE_KEY` は削除(0.7.0 で公開したが
  利用者はない)。`tests/test_architecture.py` の文字列一致テスト
  (`test_gateの指紋不能キーはstatusの定数と同じ文字列`)は「gate.py の AST に文字列リテラル
  `"fp-unavailable"` が無く、`state.FP_UNAVAILABLE_KEY` の属性参照がある」検査に置き換える。
- **AST 検査の top-level 判定を `try` / `if` / `with` の body まで再帰させる。**
  `_own_imports` の「モジュール直下」を「関数・クラス定義の外側」と定義し直す
  (`ast.FunctionDef` / `AsyncFunctionDef` / `ClassDef` の中だけを非 top-level とする)。
  `test_入口はモジュール直下でstatusをimportしない` が `try:` 内の import を捕まえることを、
  一時ファイルを `_own_imports` に食わせるテストで固定する。
- **`stop-pass` golden の検査に `decision == "ran"` を追加。** golden の形式は変えず、
  `tests/test_contracts.py` の in-process テストで `stop-pass` のときだけ `log.tail(root, 1)[0]` の
  `decision` を検査する(既定が skip に変わる回帰を検出)。

### 2.5 変更しないもの

- `.loop-hooks.json` の schema、`additionalContext` / `systemMessage` の文言、`hooks/hooks.json`、
  `hooks/session_start.py`。
- ログの保持上限(1,200 / 1,000 行)。

## 3. 受け入れ条件

- `log.failure_reason` の表駆動テスト(runner 出力 / pytest 出力 / ruff 出力 / timeout / could not run /
  空 / 120 字超過)。
- `gate` の fail・warn 記録に `reason` が入り、pass・skipped には入らない(`test_gate.py`)。
- `status` の `summary` golden(上記の表示)、空ログ、`slow` 付き、`recent` の `reason` 表示。
- architecture test の更新 3 件(§2.4)が緑で、`try:` 内 import の検出テストが RED → GREEN を経る。
- contract golden 9 本が無変更で緑(変わった場合は理由を記録)。
- `uv run python scripts/verify.py all` exit 0。`quick` 増分 ≤ 1 秒。mutation baseline は
  `log` / `status` / `state` の total 変化で再基準化(runner が書く)。
- 0.8.0(`pyproject.toml` / `plugin.json` / `uv.lock`)、CHANGELOG の Upgrading に
  「`hooks/gate.py` が変わった。プラグイン更新後に Claude Code を再起動する」を明記。
- 再起動後の SessionStart に `[loop-hooks 0.8.0]`、`/loop-hooks:status` に `summary` 行が出る。

確認済み(2026-08-29): quick 15.35s / 16.16s(2 回。超過分 1.0 秒は新設のタイムアウトテストによるもので、既存のタイムアウトテストに統合して回収済み。統合後の実測は下記) / all 116.4s(exit 0)/ baseline の再基準化は
`hooks/lib/log.py`(total 77→95, killed 65→83)と `hooks/lib/status.py`(total 403→556, killed 401→546)
の 2 ファイル、`hooks/lib/state.py` は total 140 のまま変化なし / contract golden 9 本は無変更。
summary 行は有効なゲートのみ(§2.2 を実装に合わせて修正) / 統合後の quick: 14.1 秒。

## 4. リスク

| リスク | 対処 |
|---|---|
| `reason` の抽出規則が利用側の runner に合わない | 規則 2(最後の非空行)が常に何かを返す。規則は log の docstring に書き、設定項目にはしない |
| summary の集計で status が遅くなる | 1,200 行の JSON parse は数 ms。`log.tail` は既存の実装を使う |
| `FP_UNAVAILABLE_KEY` 移動でプラグイン更新前後の混在 | 文字列は同じ `"fp-unavailable"` のまま。記録の互換性は保たれる |
| gate.py 変更の再起動忘れ | CHANGELOG と SessionStart の version 表示で確認(CLAUDE.md 4 項) |
