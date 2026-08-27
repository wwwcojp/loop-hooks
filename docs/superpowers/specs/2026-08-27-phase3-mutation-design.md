# loop-hooks 第 3 段階 設計書 — mutation testing + ラチェット → 0.5.0

作成日: 2026-08-27
前提: 0.4.0(main = f789086)。第 2 段階(quick 6 チェック、CI test/security)完了。
スパイク: `2026-08-27-mutation-spike-results.md`(mutmut 3.7.0、928 変異・172 秒・score 75.9)
親: `2026-08-26-verification-roadmap-design.md` §4(本書はその子 spec。食い違う点は本書が優先し、親に追記する)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-27 チャットで合意: 入口の import 行変更を許容 / 目標 score 85(status 80)/ 0.5.0) |

## 1. 目的

第 2 段階までで「テストが通るまで終われない」は成立した。第 3 段階は**テストそのものの品質**を機械検出する。
テストを壊しても落ちない(= 何も証明していない)テストを mutation で見つけ、score をラチェットで
下げられなくする。スパイクで既に本物の穴(`fingerprint._git` の timeout 未検査、`hook_io.py` が
subprocess 越しにしか呼ばれず 15 変異すべて no tests)が見えている。

## 2. 設計

### 2.1 import のルート起点化(前提)

mutmut は変異キーをファイルパス由来(`hooks.lib.config`)で期待する。現状の `sys.path.insert(hooks/)` +
`from lib import …` では実行時モジュール名が `lib.config` になり一致しない(スパイク §結論)。

| 場所 | 現状 | 変更後 |
|---|---|---|
| `hooks/gate.py` / `hooks/session_start.py` | `sys.path.insert(0, <hooks/>)`、`from lib import …`、`status_main` 内の `from lib import status` | `sys.path.insert(0, <plugin root>)`、`from hooks.lib import …`、`from hooks.lib import status` |
| `tests/*.py` | `sys.path.insert(hooks/)`、`from lib import …`、`import gate` / `import session_start` | `sys.path.insert(REPO_ROOT)`、`from hooks.lib import …`、`from hooks import gate` / `from hooks import session_start` |
| `tests/test_packaging.py` | `sys.path.insert(0, str(ROOT / "hooks"))` | `sys.path.insert(0, str(ROOT))` |
| `pyproject.toml` `[tool.importlinter]` | `root_packages = ["lib"]`、契約は `lib.*` | `root_packages = ["hooks"]`、契約は `hooks.lib.*`。入口禁止契約は `forbidden_modules = ["hooks.gate", "hooks.session_start"]` に(`hooks` がパッケージ化されるので入口も契約に入れられる) |
| `scripts/verify.py` `imports` Check | `cd hooks && PYTHONPATH=. …` | `cwd="."`、`env=(("PYTHONPATH", "."),)`、`--config pyproject.toml` |
| `hooks/__init__.py` | 無し | **作らない**(PEP 420 の名前空間パッケージで足りる。プラグインのファイル構成を増やさない) |

- **入口ファイルの場所と hooks.json は不変。** 変わるのは import 行だけ。稼働中セッションへの影響は無く、
  再起動不要。CHANGELOG にその旨を書く(親 §1.2 (B) の原則に対する例外として記録)。
- `hooks/lib/__init__.py` は既存のまま。
- pyright / ruff の対象は変えない。`E402` の noqa は引き続き必要。

### 2.2 mutmut の設定 `[tool.mutmut]`

```toml
[tool.mutmut]
source_paths = ["hooks"]
only_mutate = [
  "hooks/lib/config.py", "hooks/lib/fingerprint.py", "hooks/lib/hook_io.py",
  "hooks/lib/log.py", "hooks/lib/state.py", "hooks/lib/status.py",
]
also_copy = [
  "scripts", "skills", ".claude-plugin", ".github", "docs",
  "README.md", "README.ja.md", "LICENSE", "CLAUDE.md", "CHANGELOG.md",
  ".loop-hooks.json", "uv.lock", ".gitignore",
]
```

- 対象は `hooks/lib` 6 本。入口(`gate.py` / `session_start.py`)は対象外(subprocess で起動するテストが
  主体で、mutmut のカバレッジに見えにくい。第 3 段階の運用で必要になれば追加)。
- `mutants/` は `.gitignore`、`[tool.ruff] extend-exclude`、`[tool.pyright] exclude`、`.loop-hooks.json`
  の `ignore` に追加する。

### 2.3 `scripts/verify.py mutation` ステージとラチェット

姉妹 PJ(safe-dev-hooks `scripts/verify.py`)の `mutation_scores` / `check_mutation_baseline` / `run_mutation`
を移植する(evidence 記録は除く)。

- `uv run python scripts/verify.py mutation`: `mutants/` を削除してから `uv run mutmut run` を実行
  (増分実行だと古い判定が残りラチェットを誤判定するため)。`mutants/hooks/lib/*.py.meta` の
  `exit_code_by_key` からファイル別 `{score, killed, total}` を集計。killed = 終了コード `1/3/-24`、
  それ以外(survived 0、no tests 5/33、timeout、suspicious)はすべて未検出扱い。
- **baseline**: `tests/mutation-baseline.json`(リポジトリ内、コミットする)。形式は
  `{"files": {"hooks/lib/config.py": 86.4, …}}`。
  - 下回ったファイルがあれば非ゼロ終了(全件列挙)。
  - baseline にあって結果に無いファイル(`only_mutate` から外れた)は fail。対象の縮小は baseline も手で外す。
  - 上回ったファイルがあれば baseline を書き換える(ランナー自身だけが上げる。下げる経路は無い)。
  - ラチェットは 1 変異分(100/total)の下振れを許容する(理由: タイムアウトが killed 扱いになる揺れ)。
- `all` = `quick` + `mutation`。コミット前・フェーズ完了時に手で回す。**Stop ゲートには載せない**
  (約 3 分)。CI でも回さない(時間と再現性。ランナー自身の改変はレビューで見る)。
- 出力: ファイル別の表と baseline との差分、fail の理由。survived の一覧は `uv run mutmut results` /
  `mutmut show <key>` に任せる。

### 2.4 ハーネスのテスト(`tests/test_verify.py`)

- `mutation_scores`: 手で作った `.meta` から集計できる(killed/survived/no tests/timeout の各コード)。
- `check_mutation_baseline`: 下回り → fail と一覧 / 同値 → ok で baseline 不変 / 上回り → ok で baseline
  更新 / baseline にあって結果に無い → fail。
- `run_stage("all")` の順序が `quick` の後に `mutation`。
- 「テストを壊したら mutation が落ちる」は本体実装時に手で 1 回確認し spec に記録(自動化は
  3 分の実行が要るので CI/ゲートには載せない)。

### 2.5 初回トリアージ(score を上げる)

| ファイル | 初回 | 目標 | 手段 |
|---|---|---|---|
| `hook_io.py` | 0.0(全件 no tests) | 85 | `read_event` / `emit` を直接呼ぶ単体テスト(subprocess を通さない) |
| `status.py` | 68.6 | 80 | `render` の出力をゴールデン(固定文字列)で 2〜3 ケース固定。書式の変異を一括で殺す |
| `log.py` | 72.7 | 85 | `ts` が UTC・`%Y-%m-%dT%H:%M:%SZ` であることのテスト。切詰め境界の等号 |
| `fingerprint.py` | 78.4 | 85 | `_git` の `timeout` が渡ることのテスト(`subprocess.run` を spy)。`is_watched` の境界 |
| `config.py` | 86.4 | 85 | `_error` キー名の固定(呼び出し側が分岐に使う)、作業ツリー読取失敗の経路 |
| `state.py` | 92.1 | 85 | 維持 |

- 到達できない分は **pragma で除外せず**、baseline にそのまま記録して残す(次の運用で上げる)。
  除外が要る場合(例: 等価変異)は `# pragma: no mutate` に理由を添え、spec に一覧を持つ。
- pragma 一覧: `hooks/lib/fingerprint.py::_changed_paths` の `i += 1` — 理由: 変異 47 が無限ループ化し
  RAM 11GB+(等価ではないが実行不能)。
- テストを足すときは通常の TDD(先に失敗させる)を守る。mutation が「落ちるべきなのに落ちない」を
  示した後、その変異を殺すテストを書いて `mutation` で killed になることを確認する。

### 2.6 文書・リリース

- `CLAUDE.md`: 「`verify all` = quick + mutation(約 3 分)。コミット前・フェーズ完了時に回す。
  `tests/mutation-baseline.json` はランナーだけが上げる。下げない」。`mutants/` に触らない。
- README 英日: Tests 節に `all` を一言。Pairings の「Mutation testing with a ratchet」節は既にある
  (自リポジトリでの実例として一文追加)。
- 親 spec §4 に本書へのリンクと「0.5.0 として実施」を追記(親は 0.4.0 後半としていた)。
- 0.5.0。**再起動不要**(入口ファイルの場所・hooks.json 不変)を CHANGELOG に明記。

## 3. 受け入れ条件

- `uv run python scripts/verify.py all` が exit 0。所要は `mutation` 168〜400 秒(負荷・暴走変異の
  有無で変動)、`all` 3〜7 分(実測を記録)。5 分以内という受け入れ条件は隔離環境なら 3 分前後で
  収まるが、負荷がかかると超える、というのが正直な実態。
- `tests/mutation-baseline.json` に 6 ファイルの score。トリアージ後の目標: `hook_io` / `log` /
  `fingerprint` / `config` / `state` ≥ 85、`status` ≥ 80。届かないファイルは理由を本書に記録。
- テストを 1 つ無効化して `mutation` が非ゼロ終了することを確認(記録)。
- import 変更後: quick 6/6 ok、CI green、`/loop-hooks:status` と SessionStart 告知が再起動なしで
  動くこと(GitHub 版更新後に確認)。
- import-linter 3 契約 KEPT(`hooks.lib` 読み替え後)。

確認済み(2026-08-27): `all` 約3分(quick 11.5秒 + mutation 約170秒、928 変異)。baseline:
config 93.5 / fingerprint 95.4 / hook_io 86.7 / log 84.4 / state 92.1 / status 99.5(初回:
86.4 / 78.4 / 0.0 / 72.7 / 92.1 / 68.6)。テスト無効化で mutation が FAIL することを確認
(Task 4 Step 2): `tests/test_log.py` の切詰めテスト2本(`test_上限を超えたら直近だけ残す` と
`test_切詰めは一時ファイル経由で差し替える`)を同時に弱体化 →
`! hooks/lib/log.py: score 50.6 < baseline 72.7`、exit 1、baseline 不変(単独ではカバレッジが
重なって落ちなかった)。未達のファイル: `log.py` は目標 85 に対し 84.4。残り生存 12 件のうち
`ensure_ascii=None` と `cast` は等価変異、`encoding="utf-8"→None` 系 約10件は環境依存の生存
(C.UTF-8 ロケールでのみ区別不能。等価ではない)として記録する。`fingerprint.py` の
`x__changed_paths__mutmut_45` は非等価の生存だが目標(85)を超過しているため未対応。

## 4. スコープ外

- CI での mutation 実行。
- 入口ファイル(`gate.py` / `session_start.py`)の mutation。
- PBT(第 4 段階)。mutation で見えた穴のうちプロパティ形のものは第 4 段階に持ち越す。

## 5. リスク

| リスク | 対策 |
|---|---|
| import 変更で稼働中セッションのゲートが壊れる | ファイルの場所は不変。GitHub 版更新前に手元で `uv run hooks/gate.py --status .` と stdin 付き実行で確認 |
| mutmut の増分実行が古い判定を残す | 毎回 `mutants/` を削除(2.3) |
| `also_copy` 漏れで mutants 内のテストが落ちる | スパイクの一覧を使う。落ちたら「no tests」ではなく suspicious/error で見える |
| 3 分の実行が運用で省かれる | `all` の実行を CLAUDE.md の規約に。将来 TaskCompleted フックに載せる案は親 §6.3 |
| score 目標に届かない | 未達を baseline に記録して次に回す。pragma で誤魔化さない |
| 変異 `fingerprint.x__changed_paths__mutmut_47`(リネーム後の `i -= 1`)が無限ループ化し
  11GB+ の RAM を消費(kill が必要、mutmut は segfault=killed と記録する) |
  対策済み(0.5.0): 対象行に `# pragma: no mutate` を付けて除外(2.5) |
| mutmut のスコアが非決定的に揺れる(ソース・テストに変更が無くても score が変動した例:
  fingerprint 94.8→95.4)。ラチェットが下がる方向に揺れると偽の FAIL になりうる |
  対策済み(0.5.0): ラチェットに 1 変異分(100/total)の許容幅を持たせた(2.3) |
