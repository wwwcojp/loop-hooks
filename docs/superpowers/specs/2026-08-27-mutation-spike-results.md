# Mutation スパイク結果(第 3 段階の入口)

実施日: 2026-08-27。対象: main = f789086(0.4.0)。作業はリポジトリのコピー(scratch)で行い、コードは残していない。
親: `2026-08-26-verification-roadmap-design.md` §4.1

## 結論: mutmut 3.7.0 を採用できる。前提は import のルート起点化(姉妹 PJ と同じ)

- **動く**: `source_paths = ["hooks"]`、`only_mutate` に `hooks/lib/*.py` 6 本、`also_copy` にテストが読む
  ファイル群、で完走。**928 変異、172 秒(6.6 mutations/sec)**、全体 score 75.9%。
- **前提**: 実行時のモジュール名を mutmut の期待(`hooks.lib.config`)に合わせる必要がある。現状は
  `sys.path.insert(hooks/)` + `from lib import …` で `lib.config` になり、キーが一致しない(姉妹 PJ の
  スパイクと同じ事象)。変換内容:
  - `hooks/gate.py` / `hooks/session_start.py`: `sys.path.insert(0, <plugin root>)` + `from hooks.lib import …`
    (**ファイルの場所は動かさない**。変わるのは import 行だけで、hooks.json の登録は不変。ただし
    `gate.py` の `status_main` 内の遅延 import も同様に変える)
  - `tests/*.py`: `from hooks.lib import …`、`from hooks import gate / session_start`、
    `sys.path.insert(REPO_ROOT)`。`test_packaging.py` の `sys.path.insert(ROOT / "hooks")` も同様。
  - 変換後 pytest 217 件 green(スパイクで確認済み)。import-linter の `root_packages` は `hooks` に、
    契約の `lib` は `hooks.lib` に読み替える必要がある(本体で確認)。
- **`verify all` = quick(11.5 秒)+ mutation(172 秒)≈ 3 分**。親 spec の目標 5 分の内側。
  Stop ゲートには載せない(親 §4.2 どおり)。
- 姉妹 PJ(122 mutations/sec)より 1 桁遅い。テストの多くが `gate.py` を subprocess で起動するため。
  mutmut は変異ごとに「その関数を呼んだテストだけ」を回すので、subprocess 経由の呼出は
  カバレッジとして見えず、**`hook_io.py` は 15 変異すべて「no tests」**になった(下表)。

## 実測(hooks/lib 6 本)

| ファイル | 変異 | killed | survived | no tests | timeout | score |
|---|---|---|---|---|---|---|
| `config.py` | 154 | 133 | 21 | 0 | 0 | 86.4 |
| `fingerprint.py` | 153 | 120 | 33 | 0 | 0 | 78.4 |
| `hook_io.py` | 15 | 0 | 0 | 15 | 0 | 0.0 |
| `log.py` | 77 | 56 | 21 | 0 | 0 | 72.7 |
| `state.py` | 140 | 129 | 11 | 0 | 0 | 92.1 |
| `status.py` | 389 | 267 | 122 | 0 | 1 | 68.6 |
| 合計 | 928 | 704 | 208 | 15 | 1 | 75.9 |

score = killed / 全変異(no tests は未検出扱い、姉妹 PJ と同じ定義)。
mutmut の終了コード→状態: `1/3/-24` = killed、`0` = survived、`5/33` = no tests、`24/152/255` = timeout。

## 生き残りのサンプル(正体)

- `fingerprint._git`: `timeout=GIT_TIMEOUT_SEC` → `timeout=None` が生き残る = **git のハングを防ぐ timeout が
  テストされていない**(本物の穴。git が固まるとゲートが hooks.json の timeout まで止まる)。
- `log._now`: `timezone.utc` → `None`、`%Y-%m-%d` → `%Y-%M-%D` が生き残る = ts の書式・UTC を検査する
  テストが無い(`--status` の表示が `ts[:16]` に依存しているので実害あり)。
- `state.state_dir`: `Path.home() / ".cache"` の変異が生き残る = XDG 無し・HOME のみの経路が未検査。
- `config.load`: `if committed is None` → `is not None` が生き残る = 「作業ツリーが読めず HEAD も無い」
  経路が未検査。`"_error"` キー名の変異も生き残る = 呼び出し側がキー名で分岐しているのに固定されていない。
- `status.py` の 122 件は主に `render` の書式文字列(ラベル幅・区切り)。表示専用なので優先度は低いが、
  `--status` の出力をゴールデンで固定すれば一括で殺せる。

## 第 3 段階本体に持ち越すもの

1. import のルート起点化(上記)。入口ファイルの import 行が変わるので、リリースノートに
   「再起動は不要(ファイルの場所は不変)」を明記して混乱を避ける。
2. `scripts/verify.py` に `mutation` ステージ(姉妹 PJ の `mutation_scores` / `check_mutation_baseline` を
   移植)。baseline は `tests/mutation-baseline.json`(リポジトリ内、ファイル別 score、ラチェット)。
   `all` = quick + mutation。CI では回さない。
3. `hook_io.py` を直接呼ぶ単体テスト(subprocess 経由では mutmut に見えない)。
4. 初回のトリアージ: `fingerprint._git` の timeout、`log._now` の書式、`config.load` の経路、
   `status.render` のゴールデン。目標は各ファイル 85 以上(status は 80)。
5. `mutants/` を `.gitignore` に。ruff / pyright / import-linter の対象から `mutants/` を外す。
