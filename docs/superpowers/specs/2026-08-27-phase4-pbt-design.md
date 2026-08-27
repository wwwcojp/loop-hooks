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
# import 時は HYPOTHESIS_PROFILE だけを見る(MUTANT_UNDER_TEST では選ばない — §3 参照)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

MUTATION_MAX_EXAMPLES: int = settings.get_profile("mutation").max_examples


def pytest_runtest_setup(item):
    # mutmut は 1 つの永続プロセスを fork して変異ごとに pytest を再実行し、conftest は最初の
    # import 時にしか評価されない。変異ごとの絞り込みは各テストの実行直前に行う。
    if not os.environ.get("MUTANT_UNDER_TEST"):
        return
    fn = getattr(getattr(item, "obj", None), "__func__", getattr(item, "obj", None))
    current = getattr(fn, "_hypothesis_internal_use_settings", None)
    if current is not None:
        fn._hypothesis_internal_use_settings = settings(current, max_examples=MUTATION_MAX_EXAMPLES)
```

- `default`: `quick` と CI。P3 / P5 は上表の個別 `max_examples` を `@settings` で上書き(プロファイルより小さい値)。
- `thorough`: `scripts/verify.py all` の新ステージ `properties`(下記)。
- `mutation`: mutmut が各変異のテスト実行時に設定する `MUTANT_UNDER_TEST` を `pytest_runtest_setup` が
  テストごとに検出し、その場で例数を差し替える(import 時の選択は mutmut の stats フェーズで
  固定されるので使わない)。
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

### 確認済み(2026-08-27、第 6 タスク)

**所要時間**

- `quick` 増分: 約 1.1 秒(`uv run pytest -q` の内部時間で比較。`tests/test_properties.py` を除いた
  284 件が 10.82 秒、含めた 293 件が 11.89 秒)。`quick` 全体は 13.7 秒。
- `properties`(thorough、300 例): 5.8 秒。
- `mutation` 増分: 約 +13 秒(第 3 段階の 64 秒 → 全プロパティ有効時 77 秒)。いずれも ≤ 60 秒の
  予算内。`MUTATION_MAX_EXAMPLES` は 5 のまま変更不要だった。

**組ごとの killed 増分(§2.5 の証明)**

「全プロパティ skip」を基準に、6 組それぞれだけを有効にして `scripts/verify.py mutation` を
1 回ずつ回した(`pytestmark = pytest.mark.skip(reason="proof")` で全 skip → 組ごとに対象だけ
skip 解除)。

| 組 | 対象ファイル | 基準(全 skip) | 組単独 | 増分 |
|---|---|---|---|---|
| P1 | `config.py`(`_validate`) | 144/154 | 144/154 | 0 → 強化後も 0 |
| P2a+b+c | `fingerprint.py`(`is_watched`) | 139/147 | 139/147 | 0 → 強化後も 0 |
| P3 | `fingerprint.py`(`compute`) | 139/147 | 140/147 | **+1** |
| P4 | `log.py`(`tail`) | 65/77 | 65/77 | 0 → 強化後も 0 |
| P5 | `log.py`(`append`/`_trim`) | 65/77 | 65/77 | 0 → 強化後も 0 |
| P6a+b | `state.py`(round trip) | 129/140 | 129/140 → 強化後 **130/140** | **+1**(強化後) |

全プロパティ有効(強化込み)での最終 baseline: `config.py` 144/154(変化なし)、
`fingerprint.py` 140/147(P3 の +1)、`hook_io.py` 13/15(対象プロパティ無し、変化なし)、
`log.py` 65/77(変化なし)、`state.py` 130/140(P6 強化の +1)、`status.py` 387/389(対象
プロパティ無し、変化なし)。

**プロパティ単独での killed(最終レビューで測定、例示テストをすべて外し `tests/test_properties.py`
だけを test suite にした mutmut、5 例)**: `config.py` 14/154、`fingerprint.py` 81/147、`log.py` 44/77、
`state.py` 74/140。6 組すべてが単独で変異を殺しており、§3 の「最低 1 件を殺す」は満たす。上表の
増分 0 は例示テストに対する **増分** であり、例示テストが既に飽和している箇所(等価変異のみ残存)。

**増分 0 だった組の扱い(§2.5「性質を書き直す」)**

`mutants/*.py.meta` と `mutants/hooks/lib/*.py` の生存変異を実際に読み、以下を確認した:

- P1・P2・P4・P5 が増分 0 だった理由はプロパティの弱さではなく、対象関数の生存変異が
  **どのテストでも原理的に殺せない等価変異**(`typing.cast(T, x)` → `cast(None, x)`。`cast` は
  実行時に恒等関数なので型引数を変えても挙動は変わらない)、または**この実行環境で等価な変異**
  (コーデック名の大小文字違い `"utf-8"`→`"UTF-8"`、`json.dumps` の `ensure_ascii=False`→`None`
  は両方 falsy で同じ分岐を通る、`encoding=<リテラル>`→`encoding=None` はこの環境のロケールが
  `LANG=C.UTF-8` で既定エンコーディングが utf-8 と一致するため区別不能)であり、加えて P1 の
  一部生存変異(`load`/`plugin_version`)はそもそも `_validate` の対象外(spec §4 のスコープ外)
  だったため。それでも brief の指示どおり各プロパティを最小限強化した:
  - P1: `_error` が `CONFIG_NAME` で始まることの検査、gate で省略したキーが `GATE_DEFAULTS` の
    値そのもので埋まることの検査を追加。
  - P2a: 戻り値が厳密に `bool` 型であることの検査を追加。
  - P4: ガベージ入力ケースに加えて、実際に選ばれた行の**中身と順序**(壊れていない行だけを
    新しい順に n 件で打ち切る)をオラクル再実装と突き合わせる検査を追加。
  - P5: 切詰め後も直前の `append`(`i = k-1`)が生き残っていることの検査を追加(切詰めが「新しい方を
    残す」ことを直接確認)。
  - いずれも再実行で増分は 0 のままだった(等価変異のみが残っているという上の説明と整合)。
- P6a+b だけは**本物の穴**が見つかった: `state.key()` の戻り値の長さ(sha256 の先頭 16 桁)を
  検査していなかったため `x_key__mutmut_6`(`hexdigest()[:16]` → `[:17]`)が生存していた。
  `assert len(state.key(root)) == 16` を追加したところ、この変異は殺せるようになった
  (`state.py` 129/140 → 130/140)。P6a+P6b で `x_key__mutmut_5`(`encode("utf-8")` →
  `encode("UTF-8")`、コーデック名の大小文字違いで等価)は引き続き生存する。

**発見した欠陥**

1. `tests/conftest.py` の profile 選択にバグがあった。`settings.load_profile("mutation" if
   os.environ.get("MUTANT_UNDER_TEST") else ...)` を import 時に評価していたが、mutmut は
   1 つの永続プロセスを使い回し、この conftest は最初の import 時にしか評価されない。その
   最初の import が mutmut 自身の内部フェーズ(stats 収集など、`MUTANT_UNDER_TEST="stats"` の
   ように実際の変異キーではない値が立っている)の最中に起きると、"mutation"(5 例)が永続
   プロセスの既定プロファイルとして固定されてしまい、以降の stats/clean 実行や、実行時に
   その場で作った hypothesis 関数まで、既定 25 例のはずが 5 例になっていた。この第 6 タスクの
   Step 1 の初回実行(`uv run python scripts/verify.py mutation`)がこれで即座に落ちた
   (`tests/test_packaging.py::test_MUTANT_UNDER_TESTがあればhypothesisの例数が実行時に5へ絞られる`
   が `assert 5 == 25` で失敗、mutmut の stats 収集自体が失敗コードで終了)。修正は import 時の
   選択から `MUTANT_UNDER_TEST` の分岐を外し、`HYPOTHESIS_PROFILE`(既定 `default`)だけで
   決めるようにした。実行時の絞り込みは既存の `pytest_runtest_setup` フックが `MUTANT_UNDER_TEST`
   を都度読んで行うので、影響を受けない。
2. 上記「増分 0 だった組」の調査を通じて、`config.py` / `fingerprint.py` / `log.py` /
   `state.py` の非 100% mutation score の大半が、実装の欠陥ではなく mutmut の変異演算子が
   生成する等価変異(`typing.cast` の型引数、コーデック名の大小文字、`ensure_ascii` の
   `False`/`None`、`encoding` の既定値と明示値の一致)で説明できることを確認した。今回の
   baseline(§ 上表)はこれらを除いた実質的な上限に近い。

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
