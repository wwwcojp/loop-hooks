# loop-hooks 第 5 段階 設計書 — アーキテクチャ/契約テスト → 0.7.0

作成日: 2026-08-27
前提: 0.6.0(main = fae76c5)。第 4 段階(PBT、`all` = quick → properties → mutation)完了。
親: `2026-08-26-verification-roadmap-design.md` §6(本書はその子 spec)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-27 チャットで合意: ゴールデンは正規化のうえ辞書ごと完全一致 / 新規 2 ファイル + `tests/contracts/` / 既存テストの担う項目は動かさず参照のみ / 0.7.0) |

## 1. 目的

第 1〜4 段階は「関数が正しいか」を固定した。第 5 段階は「構造と境界が正しいか」を固定する:

- **アーキテクチャ規則**(親 §6.1): 入口ファイルの依存、`gate` と `status` の判定式の一致、
  状態・ログの書込先がリポジトリ外に留まること、`hooks/lib` の公開関数が例外を外に出さないこと。
  これらは今までレビューの記憶と CLAUDE.md の文章でしか守られていない。
- **Claude Code との契約**(親 §6.2): フックの入出力 JSON をゴールデンとして置き、入口の出力が
  それに一致することを検査する。Claude Code 側の仕様を人が追ったとき、ゴールデンの差分がそのまま
  「契約の変更」として残る。

コードの変更は原則しない。不変条件テストが欠陥を見つけたら直し、CHANGELOG の Fixed に載せる。

## 2. 設計

### 2.1 `tests/test_architecture.py`(新規、親 §6.1)

すべて例示テスト(`quick` で走る)。`hooks/lib` は変えないので mutation の対象外。

**(a) 入口ファイルの import 規則(AST)**

- 対象: `hooks/gate.py`、`hooks/session_start.py`。
- `ast.parse` して `Import` / `ImportFrom` を集め、自リポジトリ由来の import(モジュール名が
  `hooks` で始まるもの、および相対 import)が **`hooks.lib` 配下だけ** であることを検査する。
  `from lib import …`(ルート起点でない旧形式)も禁止(CLAUDE.md 6 項、mutmut の変異キー)。
- `hooks.lib.status` を import してよいのは `gate.py` の関数ローカル(`status_main` 内)だけ。
  モジュール先頭での import は禁止(0.3.0 spec §2: ゲート経路で表示モジュールを読まない)。
  検査は「モジュール直下(`ast.Module.body`)の import に `status` が無い」ことで行う。
- 入口ファイルは `sys.path.insert(0, <プラグインルート>)` を持つ(ルート起点 import の前提)。

**(b) `gate.handle` と `status.collect` の判定式の一致**

0.3.0 の裁定「`will_run` は gate と同じ式」を、共有リゾルバを抽出せずにテストで固定する。
同一の tmp リポジトリ(`.loop-hooks.json` をコミット済み、検証コマンドは `true`)を次の 4 状態にし、
各状態で `status.collect(root)` を取ってから `gate.handle({"hook_event_name": "Stop", "cwd": root})`
を呼び、対応を表で検査する:

| 状態 | 作り方 | `collect` | `gate.handle` の記録(`log.tail` の先頭) |
|---|---|---|---|
| 未検証 | watch 対象を編集、`verified` 無し | `will_run=True`, `blocked=False` | `decision="ran"` |
| 検証済み | 直前に gate を通す | `will_run=False` | `decision="skipped"` |
| fp 取得不能 | `fingerprint.compute` を `None` に monkeypatch | `will_run=True`(`None != verified`) | `decision="ran"`, `note="fingerprint unavailable"` |
| blocked 一致 | コマンドを `false` にして 1 回失敗させる | `blocked=True` | 2 回目の失敗は `result="warn"`(再ブロックしない) |

**(c) 書込先の不変条件**

`state._path(root)` / `log._path(root)` について、`root` として次を与えても
(1) 返るパスが `state.state_dir()` 配下(`Path.is_relative_to`、3.9+)で、
(2) 書込(`write_verified` / `append`)の前後でリポジトリ内のファイル集合(`os.walk`)が変わらない:

- 正規のルート、末尾 `/` 付き、`..` を含む相対表現(`<root>/sub/..`)、シンボリックリンク経由の
  パス(`tmp/link -> root`)。
- シンボリックリンク経由と正規のルートは **同じ `key`** に解決される(`realpath`)ことも検査する
  (worktree の独立性は `test_gate.py::test_worktreeは本体と独立に検証状態を持つ` が担う)。

**(d) `hooks/lib` の公開関数は例外を外に出さない(表駆動)**

| モジュール / 関数 | 状況 | 期待 |
|---|---|---|
| `state.write_verified/blocked/noticed` | `CLAUDE_PLUGIN_DATA` が書込不能(ディレクトリを `chmod 0o500`; root 実行時は skip) | 例外なし、その後の `read_*` は `None` |
| `state.read_verified/blocked/noticed` | 状態ファイルがディレクトリ | 例外なし、`None` |
| `log.append` | 同上の書込不能 | 例外なし |
| `log.tail` | ログファイルがディレクトリ | 例外なし、`[]` |
| `fingerprint.repo_root` / `compute` / `head_file` | `PATH` に git が無い(`monkeypatch.setenv("PATH", <空ディレクトリ>)`) | 例外なし、`None` |
| `fingerprint.repo_root` | `cwd` が存在しないディレクトリ | 例外なし、`None` |
| `config.load` | `root` が存在しないディレクトリ / git が無い | 例外なし(`None` または `_error`) |
| `config.plugin_version` | `PLUGIN_JSON` を壊れた JSON に monkeypatch | 例外なし、`None` |
| `status.collect` | 上のどの状況でも | 例外なし、`info` の全キーが揃う |

「壊れた入力」(任意 JSON / 任意バイト列)は第 4 段階の P1 / P4 / P6b が担うので表に載せず、
ファイル冒頭のコメントで参照する。

**(e) 既存テストが担う不変条件(移動しない、参照のみ)**

- 作業ツリーの `.loop-hooks.json` は HEAD より優先されない →
  `test_gate.py::test_作業ツリーでcommandを緩めてもHEADの設定でブロックされる`
- `timeout` 時にプロセスグループが残らない → `test_gate.py::test_タイムアウトで孫プロセスも止まる`

### 2.2 `tests/contracts/` + `tests/test_contracts.py`(新規、親 §6.2)

**ゴールデンの形式**(1 ケース 1 ファイル、`tests/contracts/<event>-<case>.json`):

```json
{
  "reference": "https://code.claude.com/docs/en/hooks",
  "checked": "2026-08-27",
  "input": { "hook_event_name": "Stop", "cwd": "<CWD>", "stop_hook_active": false },
  "output": { "hookSpecificOutput": { "hookEventName": "Stop", "additionalContext": "<FEEDBACK>" } },
  "exit_code": 0,
  "stderr": ""
}
```

- `input` は Claude Code が送る形(公式リファレンスのキー名)。入口が読むキーだけを載せる:
  `hook_event_name`、`cwd`、`stop_hook_active`(Stop / SubagentStop)、`source`(SessionStart)。
- `output` は入口が stdout に書く JSON そのもの(`_exit_code` / `_stderr` の内部キーは含めない)。
  `exit_code` と `stderr` は `__main__` 経路の観測値。

**ケース(9 本)**

| ファイル | 準備 | 期待 |
|---|---|---|
| `stop-pass.json` | 未検証、コマンド `true` | 出力なし(`output: null`)、exit 0 |
| `stop-fail.json` | 未検証、コマンド `false` | `hookSpecificOutput.additionalContext` = FEEDBACK + 詳細、exit 0 |
| `stop-reentry.json` | `stop_hook_active: true`、コマンド `false` | `systemMessage` = WARN + 詳細、exit 0 |
| `subagent_stop-fail.json` | 同 fail | `hookEventName: "SubagentStop"` |
| `teammate_idle-fail.json` | 同 fail | 出力なし、exit 2、stderr = FEEDBACK + 詳細 |
| `teammate_idle-repeat.json` | 同じ状態で 2 回目 | `systemMessage` = WARN + 詳細、exit 0 |
| `session_start-active.json` | 設定コミット済み | `hookSpecificOutput.additionalContext` = 告知文、`systemMessage` = `[loop-hooks <VERSION>] gate active: <COMMAND>` |
| `session_start-disabled.json` | 設定が壊れている | `systemMessage` = DISABLED_PREFIX + 理由、`hookSpecificOutput` なし |
| `session_start-not-git.json` | git でないディレクトリ | `systemMessage` = DISABLED_PREFIX + NOT_GIT_MESSAGE |

**正規化**: 比較前に、実際の出力の中で次を置換してからゴールデンと **辞書ごと `==`**:
`<CWD>`(tmp リポジトリの実パス)、`<COMMAND>`(検証コマンド文字列)、`<VERSION>`(`config.plugin_version()`)、
`<OUTPUT>`(失敗時の `$ cmd\n…` 以降の本文 — 出力本文は環境依存なので、先頭の `$ <COMMAND>\n` 行だけ
残して残りを `<OUTPUT>` に畳む)。置換は文字列の完全一致ではなく `str.replace` で行い、
プレースホルダは `tests/test_contracts.py` の 1 か所で定義する。

**検査**: 各ゴールデンをパラメータ化し、(1) `input` の `<CWD>` を実パスに差し込み `handle()` を
呼んで `output` と一致、(2) `uv run hooks/<entry>.py` をサブプロセスで実行して stdout の JSON・
`exit_code`・`stderr` が一致(TeammateIdle の exit 2 と stderr はここでしか観測できない)。
(2) は 9 本 × 約 0.3 秒 = 3 秒弱かかるので、**サブプロセス経路は `stop-fail` / `teammate_idle-fail` /
`session_start-active` の 3 本だけ** に絞る。

**更新の運用**: 自動で書き戻す仕組み(`UPDATE_GOLDEN=1` など)は作らない。契約が変わったら
`tests/contracts/` を手で直し、`checked` を更新する。README(Tests 節)に一文で書く。

### 2.3 予算

- `quick` の増分 ≤ 3 秒(0.6.0 実測 13.3 秒 → 16.3 秒以内)。CI も同じ。
- `properties` / `mutation` は変えない(`hooks/lib` 無変更なら baseline は動かない)。

### 2.4 対象外

- 親 §6.3(同一 worktree の並行セッション、`TaskCompleted`)。
- 公式リファレンスの変更検知の自動化(手動で追う。`reference` / `checked` で辿れる)。
- 共有リゾルバの抽出(gate と status の判定式は別々のまま、テストで一致を固定する)。

## 3. 受け入れ条件

- `tests/test_architecture.py` の (a)〜(d) がすべて緑、(e) の参照先テスト名が実在する
  (`pytest --collect-only -q` で確認する packaging テストを 1 本足す)。
- `tests/contracts/*.json` 9 本、`tests/test_contracts.py` が緑。各ゴールデンに `reference` と
  `checked` がある(packaging テストで固定)。
- `uv run python scripts/verify.py all` exit 0、`quick` の増分 ≤ 3 秒。
- CI(3.10 / 3.14 / security)緑。
- 0.7.0(`pyproject.toml` / `plugin.json` / `uv.lock`)、CHANGELOG(入口無変更、再起動不要)。
- 不変条件テストで欠陥が見つかった場合は修正し、CHANGELOG の Fixed に記載。

確認済み(2026-08-27): quick 13.99 秒・14.29 秒(0.6.0 の 13.3 秒比 +0.69 秒・+0.99 秒、予算 +3 秒・上限 16.3 秒以内)/
all 102.8 秒(exit 0)。mutation 内訳: `config.py` 93.5(144/154)、`fingerprint.py` 95.2(140/147)、
`hook_io.py` 86.7(13/15)、`log.py` 84.4(65/77)、`state.py` 92.9(130/140)、`status.py` 99.5(389/391、
total 389→391 で再基準化。§2.3 の想定どおり `hooks/lib` 無変更なら動かないはずだったが、`status.py` は本フェーズで
変更済みのため再基準化された)。発見した欠陥: `hooks/lib/status.py` の `will_run` と `blocked` が、指紋が
計算できないときに gate(`hooks/gate.py`)と異なる判定をしていた。`will_run` は本来 gate が常に実行する
ケースなのに偽を返し、`blocked` は gate が使う固定キー `fp-unavailable` ではなく別の扱いをしていた
(41dc3da, 9412e9e で修正)。

## 4. リスク

| リスク | 対処 |
|---|---|
| `chmod` による書込不能が root や一部 CI で効かない | `os.access` で書けてしまう環境では `pytest.skip`(理由を明記) |
| サブプロセス経路のゴールデンが遅い | 3 本に絞る(§2.2)。増分 3 秒を超えたら 1 本に |
| `git` を `PATH` から消すと `uv` 自体が困る | テスト内では `subprocess` を直接呼ぶ関数だけを対象にし、`uv` は呼ばない |
| ゴールデンの `<OUTPUT>` 畳み込みが強すぎて失敗詳細の回帰を見逃す | 詳細の切詰め・先頭末尾保持は `test_gate.py` が担う(§2.1(e) と同じ方針) |
