# loop-hooks 第 4 段階 設計書 — Property-Based Testing(hypothesis)→ 0.6.0

作成日: 2026-08-27
前提: 0.5.0(main = 6fe32f0)。第 3 段階(mutation + 件数ラチェット、`all` = quick + mutation)完了。
親: `2026-08-26-verification-roadmap-design.md` §5(本書はその子 spec。親は 0.5.0 としていたが 0.6.0 で実施)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-27 チャットで合意: `config.load` の git 経路は対象外 / `all` に `properties` ステージ / mutation 時は 5 例に自動で絞る) |

## 1. 目的

loop-hooks の過去の欠陥はプロパティ形だった(任意の設定 JSON で例外を出さない / 任意のバイト列で
`tail` が落ちない / glob の一致が先頭一致に退化しない)。例示テストは「思いついた入力」しか
守れない。hypothesis で「任意の入力に対して成り立つべき性質」を 6 本書き、mutation で
「何かを証明しているテストか」を確認する。

## 2. 設計

### 2.1 プロパティ(`tests/test_properties.py`、新規)

| # | 対象 | 性質 | 戦略 / 例数 |
|---|---|---|---|
| P1 | `config._validate(raw)` | 任意の JSON 値で例外を出さない。戻り値は `{"_error": <非空 str>}` か `{"gate": {...}}` のどちらか一方。後者では `command` が非空 str、`on` / `watch` / `ignore` が list、`timeout_sec` が 1〜`TIMEOUT_MAX_SEC` の int | `st.recursive(st.none() \| st.booleans() \| st.integers() \| st.floats(allow_nan=False) \| st.text(), lambda c: st.lists(c) \| st.dictionaries(st.text(), c))` に、`gate` キーを持つ dict を混ぜる。25 例 |
| P2 | `fingerprint.is_watched(rel, cfg)` | (a) `rel` が `ignore` のいずれかに一致 → False(`watch` に関係なく)。(b) `watch` のどれにも一致しない → False。(c) `watch` に `rel` そのものが含まれ、`ignore` に一致しない → True | `rel`: `/` 区切りの安全な文字のパス(`[A-Za-z0-9_.-]+` を 1〜3 段)。パターン: `rel` 自身、`*.ext`、`dir/*`、無関係な文字列。25 例 |
| P3 | `fingerprint.compute(root, cfg)` | (a) 同じ作業ツリーで 2 回計算すると同じ。(b) `watch` に一致しないファイルの内容を書き換えても不変。(c) `watch` に一致するファイルの内容を変えると変わる | git リポジトリはモジュール fixture 1 個(`git init` + 初期コミット)。例ごとに `unwatched.md` / `watched.py` の内容を `st.binary()` で書き換える。subprocess を伴うので **10 例** |
| P4 | `log.tail(root, n)` | ログファイルに任意のバイト列を置いても例外を出さず、`list[dict]`、`len ≤ n`、要素はすべて dict | `st.binary()` を直接ファイルへ書く。25 例 |
| P5 | `log` の切詰め | `k` 回 `append` した後の行数は `min(k, MAX_LINES)` 以下、かつ `k > MAX_LINES` なら `KEEP_LINES ≤ 行数 ≤ MAX_LINES` | `k ∈ [0, MAX_LINES + 300]`。1 例 ≈ 数十 ms なので **15 例** |
| P6 | `state` round trip | (a) 任意の str(NUL を含まない)を `write_verified` して `read_verified` すると同じ値。`write_blocked` / `write_noticed` も同様で互いに干渉しない。(b) 状態ファイルに任意のバイト列を置くと `read_verified` は None(例外なし) | `st.text(alphabet=st.characters(blacklist_characters="\x00"))`、`st.binary()`。25 例 |

- 各テストは `tests/conftest.py` の `CLAUDE_PLUGIN_DATA` 隔離(autouse)の上で動く。hypothesis は
  関数スコープ fixture と相性が悪い(例ごとに fixture が再実行されない)ので、**例ごとに一意な
  `root`(`/home/USER/pbt-<uuid>` 相当)を生成**して衝突を避ける。P3 の git リポジトリだけは
  モジュール fixture(1 回だけ作る)。
- `deadline=None`(subprocess とファイル I/O の揺れで deadline 例外を出さない)。
- 生成した入力に実ホームパスを含めない(`/home/USER/...` 固定プレフィックス)。

### 2.2 プロファイル(`tests/conftest.py`)

```python
from hypothesis import settings
settings.register_profile("default", max_examples=25, deadline=None)
settings.register_profile("thorough", max_examples=300, deadline=None)
settings.register_profile("mutation", max_examples=5, deadline=None)
settings.load_profile(
    "mutation" if os.environ.get("MUTANT_UNDER_TEST")
    else os.environ.get("HYPOTHESIS_PROFILE", "default")
)
```

- `default`: `quick` と CI。P3 / P5 は上表の個別 `max_examples` を `@settings` で上書き(プロファイルより小さい値)。
- `thorough`: `scripts/verify.py all` の新ステージ `properties`(下記)。
- `mutation`: mutmut が各変異のテスト実行時に設定する `MUTANT_UNDER_TEST` を conftest が検出して自動選択。
  これが無いと mutation の所要時間が数倍になる(922 変異 × 6 本 × 25 例)。

### 2.3 `scripts/verify.py` の `properties` ステージ

- `STAGES["quick"]` は変えない(CI ミラー不変)。
- `run_properties(repo_root) -> bool`: `Check("properties", ["uv","run","pytest","-q","tests/test_properties.py"], env=(("HYPOTHESIS_PROFILE","thorough"),))` を `run_stage` と同じ `_run` で実行。
- `all` = `quick` → `properties` → `mutation`(どこかで落ちたら以降を回さない)。`main` の usage に
  `properties` を追加(単独実行可)。
- CI にも Stop ゲートにも載せない(`thorough` は 300 例 × 6 本で数十秒)。

### 2.4 除外・設定

- `.hypothesis/`(例のデータベース)を `.gitignore`、`[tool.ruff] extend-exclude`、`[tool.pyright] exclude`、
  `.loop-hooks.json` の `ignore` に追加。mutmut の `also_copy` には入れない(mutants 内で新規に作られてよい)。
- `hypothesis` を dev 依存に追加。
- `tests/test_properties.py` は `[tool.mutmut]` の対象ではない(テスト側)が、mutmut がテストとして
  実行する。プロパティが mutation の所要時間を伸ばしすぎる場合は `mutation` プロファイルの例数を下げる。

### 2.5 プロパティの「証明力」の確認

- 6 本すべてが**少なくとも 1 件の変異を殺す**ことを `mutmut results` の差分で確認する
  (プロパティ追加前後で killed が増える、または既存テストを一時的に外して測る)。増えないプロパティは
  「何も証明していない」印なので性質を書き直す。
- プロパティが本物の欠陥を見つけたら、第 3 段階と同じく「失敗するプロパティ → 修正 → 通る」の順で扱い、
  spec §3 に記録する。

### 2.6 文書・リリース

- `CLAUDE.md`: 「`all` = quick + properties(hypothesis 300 例)+ mutation」「mutation 中は 5 例に自動で絞る」。
- README 英日: Tests 節に一文。Pairings に PBT の節は無いので追加しない(YAGNI)。
- 0.6.0。再起動不要(入口・hooks.json 不変)。

## 3. 受け入れ条件

- `quick` の増分 ≤ 2 秒(実測を記録)。`properties`(thorough)≤ 60 秒。`mutation` の増分 ≤ 60 秒。
- 6 本すべてが mutation で最低 1 件を殺す(§2.5)。
- 全プロパティが `default` / `thorough` / `mutation` の各プロファイルで green。
- CI green(3.10 / 3.14 / security)。`quick` の CI ミラーテストは無変更で通る。

## 4. スコープ外

- ゲート本体(`gate.py` / `session_start.py`)のプロパティ(subprocess のコストが合わない)。
- `config.load` の git 経路(HEAD 優先)のプロパティ — 例示テストと mutation で担保済み。
- stateful testing(`RuleBasedStateMachine`)。必要なら第 5 段階以降。

## 5. リスク

| リスク | 対策 |
|---|---|
| hypothesis の flaky(deadline、shrink の時間) | `deadline=None`。`derandomize` はしない(再現は `--hypothesis-seed` で) |
| mutation の所要時間が伸びる | `mutation` プロファイル 5 例。実測して 60 秒超なら 3 例へ |
| fixture と例の相互作用(状態が例をまたいで残る) | 例ごとに一意な root。P3 のリポジトリは内容を毎回書き戻す |
| 生成入力にパストラバーサル等 | `is_watched` は純関数。`compute` の対象は fixture 内の固定 2 ファイルのみ |
