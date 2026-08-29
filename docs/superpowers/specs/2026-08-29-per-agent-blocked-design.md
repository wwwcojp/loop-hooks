# loop-hooks 0.9.0 設計書 — ブロック記録のエージェント単位化(同一 worktree の並行セッション)

作成日: 2026-08-29
前提: 0.8.0(main = 4524244)。親 spec `2026-08-26-verification-roadmap-design.md` §6.3 の解消候補「同一 worktree での並行セッション」。
関連: 0.3.1 決定論的ゲート spec(「同じ fp を 2 度ブロックしない」規則)、0.8.0 spec(`FP_UNAVAILABLE_KEY`)

## レビュー状況

| 節 | 状態 |
|---|---|
| 全節 | 確認済み(2026-08-29 チャットで合意: スコープはエージェント単位 / `blocked` は dict / pass で全消去 / 64 件上限 / 原子書込 / status は件数表示 / gate.py 変更のため再起動要件を明記) |

## 1. 目的

状態ファイル(`<state_dir>/<key>.json`)は repo ごとに 1 つで、`verified` と `blocked` を持つ。
`verified` の共有は正しい(同じ worktree は同じファイルなので、誰かが通せば検証済み)。
一方 `blocked` は「フィードバックを受けたエージェントが何も直していないなら再ブロックしても
同じ失敗を繰り返すだけ」という根拠の規則(0.3.1)なのに、記録は全セッション・全エージェントで
共有されている。その結果、

- 並行する別セッションが同じ fp で止まると、フィードバックを一度も受けずに `warn` で通る。
- 同一セッション内でも、subagent A がブロックされた後に subagent B が同じ fp で止まると B は
  `warn` で通る(0.8.0 までの運用ログ 748 件中、SubagentStop の `warn` 39 件にこの経路が含まれる)。

`blocked` の記録をエージェント単位のスコープで分け、規則の意図どおり「本人にだけ再ブロックしない」
にする。`hooks/gate.py`(入口)を変更するため **再起動が必要なリリース**(CLAUDE.md 2 項)。

## 2. 設計

### 2.1 スコープキー(`hooks/lib/state.py`)

`scope(event: dict) -> str` を純関数として置く。Claude Code の hook 入力
(https://code.claude.com/docs/en/hooks、2026-08-29 確認)に基づく。

| 条件 | 返す値 |
|---|---|
| `session_id` が無い/文字列でない | `"manual"`(手動実行・古い Claude Code) |
| `hook_event_name == "SubagentStop"` かつ `agent_id` あり | `f"{session_id}/{agent_id}"` |
| `hook_event_name == "TeammateIdle"` かつ `teammate_name` あり | `f"{session_id}/{teammate_name}"` |
| それ以外(Stop、`agent_id` / `teammate_name` 無し) | `session_id` |

スコープ文字列は状態ファイル内にだけ現れる。判定ログ(`.log.jsonl`)や `additionalContext` /
`systemMessage` には出さない。

### 2.2 記録形式(`state.json`)

- `blocked` を `str` から `dict[str, str]`(scope → fp)に変える。API:
  - `read_blocked(root, scope) -> str | None`
  - `write_blocked(root, scope, fingerprint) -> None`
  - `clear_blocked(root) -> None`(現在の `write_blocked(root, "")` の置き換え)
- **上限 64 件**(`BLOCKED_MAX_SCOPES = 64`)。超えたら挿入順で古いものから落とす
  (dict の挿入順で十分。同じ scope の再書込は削除→末尾追加で最新扱い)。
- **pass 時は `clear_blocked`**: fp が verified に変わるので、全 scope の記録が無意味になる。
  実運用では pass の間隔で dict が空になるため、64 件に達するのは pass なしに 65 体以上が
  失敗した場合だけ。
- **旧形式との互換**: `blocked` が `str`(0.8.0 以前)の場合は照合に使わず `None` 扱い。
  次の `write_blocked` で dict に置き換わる。逆に旧版のプラグイン(更新前のセッション)が
  dict を読むと `_read_str` の型チェックで `None` になり、壊れずに「未ブロック」に倒れる
  (最悪もう 1 回ブロックするだけ)。
- **原子書込**: `_write` を同一ディレクトリの一時ファイル + `os.replace` にする。並行フックの
  同時書込で torn file(途中まで書けた JSON)になるのを防ぐ。read-modify-write の競合で
  1 件失われることは許容(結果は「最悪もう 1 回ブロック」)。失敗時は従来どおり握り、
  一時ファイルは残さない(`finally` で unlink を試みる)。

### 2.3 `hooks/gate.py`

`_refuse` の照合・記録を scope 付きにする。変更は 3 行:

```python
    key = current if current is not None else state.FP_UNAVAILABLE_KEY
    scope = state.scope(event)
    if key == state.read_blocked(root, scope):
        return {"systemMessage": WARN + detail}
    state.write_blocked(root, scope, key)
```

pass 経路の `state.write_blocked(root, "")` は `state.clear_blocked(root)` に置き換える。
docstring の「同じフィンガープリントは 2 度ブロックしない」に「同じエージェントに対して」を足す。

### 2.4 `hooks/lib/status.py`

`--status` は hook 入力を持たないので scope を知らない。`collect` の `blocked` を
`bool | None` から `int | None`(現在の fp(または `FP_UNAVAILABLE_KEY`)でブロック済みの
scope 数)にする。`state.read_blocked_scopes(root, fingerprint) -> int` を state に足す
(dict を走査して値が一致する件数)。表示:

```
  blocked   no
  blocked   yes (2 agents already blocked at this state)
```

`INFO_KEYS`・golden・`tests/test_architecture.py` の判定パリティテスト
(`test_判定式_blockedは現在の指紋と一致するときだけで再ブロックしない` と
`test_判定式_指紋が取れないときのblockedはgateの固定キーと同じ`)を更新する。

### 2.5 contract golden(`tests/contracts/`)

- 全 9 本の `input` に `"session_id": "<SESSION>"` を足す(正規化トークンとして扱う。
  比較には影響しないが、入力が実際の Claude Code と同じ形であることを golden が示す)。
  `subagent_stop-fail` には `agent_id`、`teammate_idle-*` には `teammate_name` / `team_name` を足す。
- `teammate_idle-repeat`(同じ状態の 2 回目 → warn)は同じ `teammate_name` で 2 回止まる形に
  なっていることを確認する(scope が違えば warn にならないため、golden の意味が変わる)。
- 出力(`expected`)は無変更のはず。変わった場合は契約変更として記録する。
- `checked` を 2026-08-29 に更新。docs の Stop 節にある「8 回連続 block で Claude Code が
  ターンを打ち切る」を README の Limitations に 1 行足す(ゲートは fp 不変なら 2 回目で
  warn に倒すので、この上限には通常届かない)。

### 2.6 変更しないもの

- `verified` / `noticed` の扱い、判定ログの形式、`.loop-hooks.json` schema、
  `hooks/hooks.json`、`hooks/session_start.py`、`additionalContext` / `systemMessage` の文言。

## 3. 受け入れ条件

- `state.scope` の表駆動テスト(§2.1 の 4 行 + `session_id` が非文字列)。
- `state`: scope 別の読み書き、`clear_blocked` で全消去、65 件目で最古が落ちる、同 scope の
  再書込で順序が更新される、旧形式 `str` は `None`、原子書込(書込後に一時ファイルが残らない、
  失敗時も残らない)。
- `gate`: A がブロックされた後、B は同じ fp でもブロックされる(`additionalContext`)/ A は warn。
  SubagentStop は `agent_id` が違えば別 scope、Stop は同じ session なら同 scope。pass で
  全 scope が消える。`TeammateIdle` の既存 2 テストは `teammate_name` を持たせて維持。
- `status`: `blocked` 行の件数表示 golden(0 / 2 件)、`INFO_KEYS` 更新。
- PBT(`tests/test_properties.py`)に 1 本: 任意の scope 列と fp で「他 scope の書込は自 scope の
  読取値を変えない(上限内)」。
- architecture テストのパリティ 2 本が scope 版で緑。contract golden 9 本が(入力更新後)緑で
  `expected` は無変更。
- `uv run python scripts/verify.py all` exit 0。`quick` 増分 ≤ 1 秒。mutation baseline は
  `state` / `status` の total 変化で再基準化(runner が書く)。
- 0.9.0(`pyproject.toml` / `plugin.json` / `uv.lock`)、CHANGELOG の Upgrading に
  「`hooks/gate.py` が変わった。プラグイン更新後に Claude Code を再起動する」と、
  「状態ファイルの `blocked` が dict になる(自動移行、手作業なし)」を明記。
- README / README.ja の Limitations から「同じ worktree で並行する複数セッションは記録を共有する」
  を外し、「`verified` は worktree 単位で共有(意図どおり)、`blocked` はエージェント単位」に書き換える。

## 4. リスク

| リスク | 対処 |
|---|---|
| 他エージェントの未コミット編集が原因の失敗でも、各 subagent が 1 回ずつブロックされる | 規則の意図どおり(本人が 1 回はフィードバックを見る)。2 回目は `stop_hook_active` または同 scope の記録で warn に倒れるので閉じ込めない |
| 並行書込で記録が 1 件失われる | 影響は「最悪もう 1 回ブロック」。原子書込で torn file は防ぐ |
| 旧版プラグインとの混在 | 旧版は dict を `None` と読み、新版は str を `None` と読む。どちらも「未ブロック」に倒れるだけ |
| gate.py 変更の再起動忘れ | CHANGELOG と SessionStart の version 表示で確認(CLAUDE.md 4 項) |
