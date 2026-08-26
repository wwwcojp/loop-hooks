# loop-hooks 検証ロードマップ設計書 — 自リポジトリへの段階的導入

作成日: 2026-08-26
前提: 0.3.0(main = e19d994)。SessionStart 告知・判定ログ・`/loop-hooks:status` 導入済み
参照: news-collector `2026-08-20-verification-roadmap-design.md`、
safe-dev-hooks `loop-engineering-phase1/phase2` spec と `.claude/rules/dogfooding.md`、
0.3.0 spec(`2026-08-26-0.3.0-observability-design.md`)の「0.3.1 候補」

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-26 チャットで合意。静的セキュリティ検査 §3.1・import-linter §3.2 を追記のうえ承認) |

## 1. 目的と現状

loop-hooks は利用プロジェクト 3 件に「決定論的な停止条件」を提供しているが、
**loop-hooks 自身の開発にはその停止条件が掛かっていない。** 0.2.0 の入口ファイル移動で
ゲートが無言で消えた事象、0.3.0 実装中に出た None ガード・不正 UTF-8・例外の握り忘れは、
いずれも「自分の作ったゲートが自分を止めていれば、ターン内で検出できた」種類の欠陥だった。

本書は、姉妹プロジェクトで運用中の段階論(①決定論的ゲート → ②mutation → ③PBT → ④静的解析・
アーキテクチャ)を loop-hooks に適用する計画である。**各段階は独立に価値が出る。一気にやらず、
段階ごとに運用して次を判断する**という原則も同じ。

### 1.1 棚卸し(2026-08-26 時点)

| 手法 | 状態 | 備考 |
|---|---|---|
| 決定論的ゲート(loop-hooks 自身) | **未適用** | `.loop-hooks.json` も verify ランナーも無い |
| CI | あり | leak チェック → `ruff check` → `pytest`。Python 3.10 / 3.14 のマトリクス |
| 単体テスト | あり | 169 件、8.3 秒。`gate.py` の subprocess 起動が主体で 1 件あたりが重い |
| lint | あり | ruff check(E402 有効、行長 100) |
| フォーマッタ | なし | `ruff format` 未使用。手書きの整形 |
| 型検査 | なし | 型注釈は部分的にある(`state_dir() -> Path` 等) |
| 静的セキュリティ検査 | 一部 | CI の実ホームパス leak チェックのみ。ruff `S`(bandit 相当)・依存監査・Actions 検査は無し。GitHub の secret scanning / push protection / Dependabot は**無効**(公開リポジトリ) |
| mutation testing | なし | |
| Property-Based Testing | なし | |
| アーキテクチャ/契約テスト | 一部 | `test_packaging.py` が hooks.json・plugin.json・SKILL.md の整合を検査。import 依存の契約(import-linter)は無し |
| プロセス | あり | superpowers(spec → plan → archive)、CHANGELOG、リリースノート英日 |
| ドッグフーディング規約 | なし | safe-dev-hooks の `dogfooding.md` に相当するものが無い |

### 1.2 loop-hooks 固有の制約

姉妹プロジェクトと違い、**ゲートが実行するコードとゲート自身が同じリポジトリにある**。
ここから 3 つの制約が出る。

- **(A) 自己参照。** 作業ツリーの `hooks/gate.py` を壊した状態でターンを終えると、
  ゲートがそのコードを実行する場合、壊れたゲートが判定を下す(例外なら「ゲート未実行」で
  素通り)。対策: **セッションが使うプラグインは GitHub 経由の安定版**(marketplace の
  `source: github`)とし、`directory` ソースでの自インストールは動作確認時だけにする。
  作業ツリーのコードは verify ランナー経由の pytest でのみ実行される。
- **(B) セッションスナップショット。** フック定義はセッション開始時に固定される。
  入口ファイル(`hooks/gate.py`・`hooks/session_start.py`・`hooks.json`)を動かす変更は
  再起動まで反映されない。0.3.0 spec §2 の「`gate.py` は動かさない」を維持する。
- **(C) テストが subprocess 主体で遅め。** 8 秒は Stop ゲートとして許容範囲(目標 30 秒以内)
  だが、mutation の 1 変異あたりコストとしては重い。第 3 段階で純粋な `lib` テストと
  subprocess テストの分離が要る。

## 2. 第 1 段階 — 決定論的ゲート(ドッグフーディング) → 0.3.1

**最優先。他の全段階がこの上に載る。**

### 2.1 verify ランナー `scripts/verify.py`

姉妹プロジェクトと同型。ステージは `quick` / `all`(`all` は第 3 段階で `mutation` を足す)。

```
quick: leak チェック → ruff check hooks tests scripts → pytest -q
```

- `quick` の中身は CI と**同じコマンド・同じ順序**に保つ。`tests/test_verify.py::test_quick_stage_mirrors_ci`
  が `ci.yml` を読んで一致を検査する(safe-dev-hooks と同じ手法)。CI を先に変えてテストで
  ランナーを追随させる、という方向に固定する。
- ランナー自身は `hooks/` を import しない(ゲート対象とゲート実行者を混ぜない)。
- evidence(`.loop/evidence.jsonl`)は**書かない**。0.3.0 で判定ログが plugin 側に入ったので、
  「走ったか」は `/loop-hooks:status` で見える。ランナーは終了コードと出力だけを返す。

### 2.2 `.loop-hooks.json`

```json
{
  "gate": {
    "command": "uv run python scripts/verify.py quick",
    "timeout_sec": 120,
    "watch": ["*.py", "*.json", "*.toml", "skills/**/*.md", ".github/**/*.yml"],
    "ignore": [".superpowers/*", "docs/*"]
  }
}
```

- `skills/**/*.md` を watch に含めるのは `test_packaging.py` が SKILL.md の frontmatter を
  検査するため。`docs/` と README は除外(文書だけのターンで 8 秒払わない)。
- 0.3.0 で `watch` 既定は全ファイルになったが、上記の理由で明示する。
- `.github/**/*.yml` を含めるのは `test_verify.py` の CI ミラーテストが `ci.yml` を読むため
  (最終レビューでの追加)。

### 2.3 ドッグフーディング規約 `CLAUDE.md`

safe-dev-hooks `dogfooding.md` の loop-hooks 版。書くこと:

1. セッションで有効なのは **GitHub 版プラグイン**。作業ツリーの変更はゲートの挙動に影響しない。
   自インストール(`directory` ソース)での動作確認をしたら、終わったら戻して再起動する。
2. 入口ファイル(`hooks/*.py`・`hooks.json`)を動かさない。動かす場合は再起動が要ることを
   リリースノートに書く(0.3.0 の教訓)。
3. ゲートで止められたら**コードを直す**。`.loop-hooks.json` を変えて通さない、
   `disableAllHooks` を使わない。
4. プラグインを更新したら Claude Code を再起動する。再起動後の SessionStart 告知
   `[loop-hooks] gate active: uv run python scripts/verify.py quick` が効いている確認。

### 2.4 同梱する修正(0.3.0 spec の「0.3.1 候補」)

ゲートを入れた最初のリリースで、既知の小欠陥をまとめて片づける:
`--status` ガードの強制例外テスト / `state._write` の例外処理 / `_trim` の原子性 /
git 失敗時の fp 空記録 / `recent` が `skipped` で埋まる問題(§5.3)。

### 2.5 CI

- `scripts/` を ruff の対象に追加。
- `claude plugin validate .` 相当の検査が `test_packaging.py` に無ければ追加(公式 CLI が
  CI で使えるなら実行、使えないなら JSON 妥当性の自前検査を維持)。

### 2.6 受け入れ条件

- ゲートが**実際に自分のターンを止めた**記録が判定ログに残る(`ran fail` → 修正 → `ran pass`)。
  意図的に壊したテストで確認し、結果を CHANGELOG の 0.3.1 に一行残す。
- `test_quick_stage_mirrors_ci` が CI 変更で落ちることを確認(ミラーテストが機能している)。

## 3. 第 2 段階 — 静的検査の拡充(フォーマッタ・型・セキュリティ・import 契約) → 0.4.0 前半

姉妹プロジェクトは静的解析を最終段階に置いた(YAGNI)。loop-hooks では**前倒し**する。理由:

- 0.3.0 実装中の裁定の多くが「None ガードを gate と合わせる」「例外を握るか」の類で、
  Optional の扱いは pyright が機械的に拾う範囲。
- コード量が 780 行と小さく、今なら型注釈の補完コストが最小。
- フォーマッタは差分ノイズを消し、mutation(第 3 段階)の対象行を安定させる。

内容:

- `ruff format --check` を `quick` と CI に追加。初回は一括整形コミット(履歴を分ける)。
- `pyright`(basic モード)を dev 依存に追加し、`quick` と CI に入れる。`hooks/lib` は
  strict に上げられるか試し、無理なら basic で止める。
- 「`lib` の関数は例外を外に出さない」という設計原則は型では表せない。これは第 5 段階の
  アーキテクチャテストで扱う。

### 3.1 静的セキュリティ検査

#### 脅威モデル(何を守るか)

loop-hooks の攻撃面は小さいが特殊で、**「リポジトリの設定ファイルに書かれたコマンドをシェルで実行する」ことが機能そのもの**である。
したがって守るべきものは次の 3 つに限られる:

1. **実行するコマンドの出所。** 作業ツリーの `.loop-hooks.json` を書き換えれば任意コマンドが
   走る、という経路は 0.2.1 の「HEAD の設定を優先」で塞いだ。これは**セキュリティ上の
   不変条件**であり、退行させてはならない(§6.1 の契約テストで固定する)。
2. **リポジトリ外への書込先。** 状態・判定ログは `$CLAUDE_PLUGIN_DATA` または
   `~/.cache/loop-hooks` 配下に限る。パス構築に入力(リポジトリパス)が混ざるので、
   トラバーサル・シンボリックリンクを検査対象にする。
3. **公開リポジトリとしての衛生。** 秘密情報・実ホームパスの混入(leak チェックで一部済み)、
   CI ワークフローの権限、依存の脆弱性。

エージェント(Claude Code)自身が `.loop-hooks.json` を書き換えてゲートを迂回する経路は、
セキュリティというより「ゲートの回避」で、1. の不変条件と CLAUDE.md の規約で扱う。

#### 導入するもの(決定論的・高速なものを `quick` に、それ以外を CI に)

| 検査 | ツール | 置き場 | 判断 |
|---|---|---|---|
| コードのセキュリティ lint | ruff `S` ルール(flake8-bandit 相当) | `quick` + CI | **導入。** 追加ツール不要で速い。現状の検出は設計上の 2 箇所のみ(下記) |
| GitHub Actions の設定検査 | `zizmor` | CI(別ジョブ) | **導入。** `ci.yml` の `permissions` 未指定、`uses` の非ピン留めを検出する。実行は `uvx zizmor .github/workflows` |
| 依存の脆弱性 | `pip-audit`(`uv export` 経由) | CI | **導入(軽め)。** 実行時依存はゼロで dev 依存(pytest・ruff)だけなので価値は限定的だが、コストもほぼゼロ |
| 依存・Actions の更新 | Dependabot(`uv` と `github-actions`) | `.github/dependabot.yml` | **導入。** 週次 |
| 秘密情報の混入 | GitHub secret scanning + push protection | リポジトリ設定 | **有効化(ユーザー操作)。** 現状 disabled。leak チェックはホームパス専用で、トークン類は見ていない |
| 意味論的な静的解析 | semgrep(`p/python`・`p/github-actions`) | CI | **保留。** ruff `S` と zizmor で足りるかを運用で見てから判断(YAGNI、姉妹の Semgrep 判断と同型)。導入する場合はルールをリポジトリに vendoring し、ネットワーク依存を持ち込まない |
| モデルによるレビュー | `/security-review`、claude-security | リリース前に手動 | **ゲートにしない。** 確率的なので Stop / CI の停止条件には使わない。第 1 段階の思想(決定論的な層と確率的な層を分ける)をここでも守る |

#### ruff `S` の運用

- 現状の検出は `hooks/gate.py`(`S602`: `shell=True`)と `hooks/lib/fingerprint.py`(`S603`)の
  2 件で、どちらも設計どおり。**行単位の `# noqa: S602 -- <理由>` で明示的に受け入れる。**
  理由に「コマンドは HEAD の `.loop-hooks.json` から読む(0.2.1)」と書き、信頼境界の
  文書化を兼ねる。ファイル単位・ルール単位の除外はしない(新しい検出が埋もれる)。
- `tests/` には `S101`(assert)を除外して適用する。
- `S` を `[tool.ruff.lint] select` に足す。`E402` の既存設定と同じ場所。

#### 受け入れ条件

- `quick` の増分が 1 秒以内(ruff は同じ 1 回の実行で済む)。
- zizmor が `ci.yml` に対してゼロ検出になるまで直す(`permissions: contents: read` の明示、
  `actions/checkout` / `setup-uv` の SHA ピン留め + Dependabot による追随)。
- 脅威モデル 1. の不変条件(「作業ツリーの設定は HEAD より優先されない」)に、
  **セキュリティ退行テスト**としての名前を付けて `test_gate.py` に残す(既存テストがあれば
  docstring に「セキュリティ不変条件」と明記する)。

### 3.2 import 依存の契約(import-linter)

[import-linter](https://github.com/seddonym/import-linter) で `hooks/lib` の依存方向を契約として
固定する。姉妹プロジェクトでは第 4 段階(アーキテクチャ)の項目だが、loop-hooks では
**すでに層構造が守られており、契約を書くだけで効く**ので第 2 段階に入れる
(2026-08-26 スパイク: `lib` に対して 3 契約を書いて実行、層契約は KEPT、所要 1 秒未満)。

#### 構成上の制約

- import-linter の `root_packages` は**パッケージのみ**(`__init__.py` のあるディレクトリ)。
  入口の `hooks/gate.py` / `hooks/session_start.py` はパッケージでないトップレベルモジュールなので
  root にできない(スパイクで確認: "'gate' is a module, not a package")。
  したがって **契約の対象は `lib` パッケージ**。入口側の規則(§6.1「入口は `lib` 以外を
  import しない」)は小さな AST テストのまま残す。
- `hooks/` をパッケージ化して解決する案は採らない。入口は `sys.path.insert` + `from lib import`
  で動いており、`hooks.lib` に変えると入口ファイルの import が変わる(= 0.3.0 spec §2 の
  「入口を動かさない」に触れる)。
- 実行は `hooks/` を作業ディレクトリに `PYTHONPATH=. lint-imports`。設定は `pyproject.toml` の
  `[tool.importlinter]` に置く(`.importlinter` ファイルを増やさない)。

#### 契約(初期 3 本)

| 契約 | type | 内容 | 根拠 |
|---|---|---|---|
| `lib` の層 | `layers` | `status` → `log` → `config` → `fingerprint` → `state` → `hook_io`(上が下に依存する) | 現状の依存グラフ。`status` が集約点、`hook_io` が最下層 |
| `lib` は入口を import しない | `forbidden` | `lib.*` → `gate`, `session_start` を禁止 | 0.3.0 spec §2「ロジックは lib に置き、入口はそれを呼ぶ」 |
| subprocess を使うのは `fingerprint` だけ | `forbidden`(`allow_indirect_imports = True`) | `config` / `hook_io` / `log` / `state` / `status` → `subprocess` を禁止 | git 実行の箇所を 1 モジュールに閉じる。§3.1 脅威モデルの「実行する場所を限定する」と対応。スパイクでは間接 import(`config → fingerprint → subprocess`)で BROKEN になったため、間接を許す設定が要る |

`gate.py` の `subprocess`(検証コマンドの実行)は入口側なので契約の外。これは設計どおり。

#### 置き場と受け入れ

- `quick` と CI に追加(`uv run lint-imports`。dev 依存に `import-linter` を足す)。
- 受け入れ: 3 契約 KEPT、`quick` の増分 1 秒以内。契約を意図的に破る変更(例: `state.py` に
  `from . import log`)で BROKEN になることを 1 度確認し、結果を子 spec に記録する。
- 第 5 段階(§6.1)で入口側の AST テストと組み合わせ、依存規則を「`lib` は import-linter、
  入口は pytest」の二本立てで完成させる。

受け入れ(段階全体): `quick` の所要時間が 15 秒以内に収まる(pyright 初回は遅いのでキャッシュ前提)。
超えるなら pyright を `all` に降ろす。

## 4. 第 3 段階 — mutation testing + ラチェット → 0.4.0 後半

### 4.1 スパイク(先行、結果は `2026-MM-DD-mutation-spike-results.md`)

姉妹プロジェクトの流儀どおり、本体の前に判定する:

- `mutmut` が `hooks/lib` + `sys.path.insert` 構成で動くか(`hooks/gate.py` は E402 で
  `sys.path` を触っている。姉妹の `scripts/` フラット配置と同型の懸念)。
- **1 変異あたりのコスト。** 全テスト 8 秒 × 変異数(780 行なら 300〜500)= 40〜70 分は
  Stop ゲートに載らない。対策候補: (a) `lib` の純粋テストだけを mutation のテスト集合に
  する(`-m "not subprocess"` マーカーで分離)、(b) 対象を `config.py` / `fingerprint.py` /
  `log.py` / `state.py` に絞る(過去の欠陥はすべてここ)、(c) mutmut のカバレッジ連動。
- 目標: `verify all` が 5 分以内。無理なら対象を `fingerprint.py` + `config.py` から始める。

### 4.2 本体

- `scripts/verify.py mutation` ステージ。score を `tests/mutation-baseline.json` に記録し、
  下回ったら非ゼロ終了(ラチェット)。向上時はランナー自身が baseline を書き換える。
- `verify all` = `quick` + `mutation`。コミット前・フェーズ完了時に手で回す。Stop ゲートには
  載せない(所要時間の制約)。
- CI では回さない(姉妹と同じ判断。時間と再現性の問題)。ランナー自身の改変はブランチレビューで見る。
- **ハーネスのテスト**: 「テストを壊したら mutation が落ちる」ことを `test_verify.py` で固定する。

### 4.3 loop-hooks で特に見たい生存変異

- `fingerprint.py` の watch/ignore 一致(0.1.x で「先頭一致の穴」があった)
- `gate.py` の二重ブロック防止条件(`fp == last_blocked_fp`)
- `log.py` の切詰め閾値(1200 → 1000)と `tail` の境界
- `config.py` の型検証の各分岐

## 5. 第 4 段階 — Property-Based Testing → 0.5.0

loop-hooks の過去の欠陥は**全部プロパティ形**だった(任意の設定 JSON で例外を出さない /
任意のバイト列で `tail` が落ちない / glob の一致が先頭一致に退化しない)。hypothesis の
適用先として相性が良い。

プロパティ候補(6 本、姉妹と同規模):

1. `config.load`: 任意の JSON 値に対して例外を出さず、`_source` を必ず返す
2. `fingerprint`: `watch` に一致しないファイルを変更しても fp が変わらない / 一致すると変わる
3. `fingerprint`: 同じ作業ツリーなら順序・呼び出し回数によらず fp が同じ(決定性)
4. `log.tail`: 任意のバイト列(不正 UTF-8 含む)で例外を出さず、行数上限を守る
5. `log` の切詰め: 何回 append しても行数が上限(1000〜1200)の範囲に収まる
6. `state` の round trip: `write → read` が恒等、壊れたファイルは既定値に戻る

制約:

- property テスト自体も mutation ゲートの対象(「何も証明していないテスト」の検出)。
- `max_examples` を絞り、`quick` の増分を 2 秒以内に抑える。超える分は `all` へ。
- subprocess を伴うゲート本体には適用しない(コストが合わない)。

## 6. 第 5 段階 — アーキテクチャ/契約テスト → 0.5.0 以降

### 6.1 アーキテクチャ規則(pytest で固定)

- **`hooks/lib/*` の公開関数は例外を外に出さない。** モジュールごとに「壊れた入力・
  書込不能なパス・存在しない git」を与えて例外が出ないことを網羅する表駆動テスト。
  第 4 段階のプロパティと重なる部分は片方に寄せる。
- **入口ファイルは `lib` 以外の自リポジトリモジュールを import しない**(AST で検査。入口は
  パッケージでないため import-linter の対象外、§3.2)。`lib` 側の依存規則は §3.2 の
  import-linter 契約で第 2 段階から効いている。
- **`gate.py` と `status.py` の判定式は同じ**(0.3.0 の裁定「will_run は gate と同じ式」)。
  共有リゾルバを抽出しない判断を維持するなら、同じ入力で同じ答えを返すテストで固定する。
- **セキュリティ不変条件(§3.1 の脅威モデル)を表駆動で固定する**: 作業ツリーの
  `.loop-hooks.json` は HEAD より優先されない / 状態・ログの書込先はリポジトリ外の既定領域から
  出ない(`..` やシンボリックリンクを含むリポジトリパスを与えても) / `timeout` 時に
  プロセスグループが残らない。

### 6.2 Claude Code との契約

- hooks の入出力 JSON(`stop_hook_active`、`hookSpecificOutput.additionalContext`、
  `systemMessage`、各イベントの入力形)をゴールデンとして `tests/contracts/` に置き、
  入口ファイルの出力がそれに一致することを検査する。
- 公式リファレンスの変更検知は本書の範囲外(手動で追う)。ただし ゴールデンに参照 URL と
  確認日を書き、古びたときに辿れるようにする。

### 6.3 Limitations の解消候補(需要が出てから)

- 同一 worktree での並行セッション(fp を 1 つしか持たない)
- `TaskCompleted` イベント(0.3.0 spec §8 で保留)

## 7. 横断事項

- **プロセスの所有者は superpowers ひとつ**に保つ。各段階は spec → plan → 実装 → archive。
  本書は各段階の spec を書くための親文書で、段階ごとに子 spec を切る(第 1 段階は本書 §2 を
  そのまま plan に落とせる規模なので子 spec は省く)。
- **リリース単位**: 段階とバージョンの対応は上記のとおりだが、段階を跨いだ「ついで」を入れない。
  破壊的変更(既定値・入口ファイル)は必ず「再起動が必要」を CHANGELOG に書く。
- **CI は段階ごとに追随**し、`quick` ミラーテストで整合を保つ。mutation は CI に載せない。
- **計測を残す**: 各段階の受け入れ時に `quick` / `all` の所要時間を子 spec に記録する
  (姉妹の「0.5 秒 / 5.1 秒」と同じ扱い)。所要時間が Stop ゲートの予算(30 秒)を超えたら、
  次の段階に進む前に分離する。

## 8. 順序と判断点

| 順 | 段階 | 版 | 着手条件 | 完了条件 |
|---|---|---|---|---|
| 1 | 決定論的ゲート + 0.3.1 修正 | 0.3.1 | なし(即時) | 判定ログに `ran fail → ran pass`、ミラーテスト稼働 |
| 2 | フォーマッタ・型・静的セキュリティ検査・import 契約 | 0.4.0 | 1 完了 | `quick` ≤ 15 秒、pyright basic クリーン、ruff `S` / zizmor ゼロ検出(受け入れ済み noqa を除く)、Dependabot と secret scanning 有効、import-linter 3 契約 KEPT |
| 3 | mutation スパイク → 本体 | 0.4.0 | 2 完了 | `verify all` ≤ 5 分、baseline とハーネステスト |
| 4 | PBT | 0.5.0 | 3 完了(プロパティは mutation で品質確認) | 6 本、`quick` 増分 ≤ 2 秒 |
| 5 | アーキテクチャ/契約 | 0.5.0〜 | 4 完了、または契約違反が実際に起きたとき | 規則 3 本、ゴールデン契約 |

第 2 と第 3 の順序は入れ替え可(型を後回しにする判断もあり得る)。第 1 だけは動かさない。

## 9. リスク

| リスク | 内容 | 対策 |
|---|---|---|
| 自己参照でゲートが壊れる | 作業ツリーの壊れた gate.py をゲートが実行する | セッションは GitHub 版を使う(§1.2 A)。`directory` ソースは確認時のみ |
| ゲートが無言で消える | 入口ファイル移動・プラグイン更新後の再起動忘れ | SessionStart 告知を見る習慣を CLAUDE.md に書く。入口を動かさない |
| mutation が遅すぎる | 8 秒 × 数百変異 | スパイクで実測、`lib` 純粋テストの分離、対象の絞込 |
| mutmut が構成に合わない | `sys.path.insert` 構成 | スパイクで判定。代替: `cosmic-ray`、または自前の最小 mutator(姉妹の spike 文書に代替案) |
| pyright で `quick` が重くなる | 初回解析 | キャッシュ前提。超えたら `all` へ降ろす |
| ゲートを避ける経路 | `.loop-hooks.json` を書き換えて通す | 0.2.1 で HEAD の設定が優先される。加えて CLAUDE.md で禁止を明文化 |
| セキュリティ lint の形骸化 | `noqa` が増えて検出が埋もれる | 行単位 + 理由必須。ファイル/ルール単位の除外を禁止。レビューで noqa の増分を見る |
| Dependabot の PR ノイズ | dev 依存と Actions の更新 PR | 週次にまとめる(`groups`)。実行時依存ゼロなので量は少ない |

## 10. スコープ外

- verify ランナーの利用者向け同梱(`examples/`)。本書のランナーは自リポジトリ用。
- ログの集計・可視化。
- 他プロジェクトのロードマップ変更。
