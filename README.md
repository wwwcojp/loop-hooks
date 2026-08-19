# loop-hooks

Claude Code 用の [Hooks](https://docs.claude.com/en/docs/claude-code/hooks) プラグイン。
ターンの終了(Stop)で、リポジトリごとに指定した検証コマンド(`verify` など)を強制する
「品質ループ」を実現する。

## 何をするか

- **PostToolUse (`Edit|Write`)** — `hooks/post_tool_use/mark_dirty.py` が発火する。
  編集されたファイルが `.loop-hooks.json` の `watch` パターンに一致し `ignore` に
  一致しなければ、そのリポジトリを "dirty" として `.loop/state.json` に記録する。
  検証はここでは走らせない。
- **Stop** — `hooks/stop/gate.py` が発火する。dirty なリポジトリなら
  `.loop-hooks.json` の `gate.command` を実行する。
  - 成功したら dirty を消して(次の Stop では何もしない)、そのままターンを終える。
  - 失敗したら `decision: block` を返し、ターンを終わらせない(直して終了させる)。
  - block してもなお失敗する場合(`stop_hook_active` が真の再入時)は、
    閉じ込めずに警告(`systemMessage`)だけ出して通す。dirty は残るので、
    次のターンの終了時に再びゲートが掛かる。

`.loop-hooks.json` が無いリポジトリではこのプラグインは何もしない(オプトイン)。

## 導入方法

1. marketplace を登録し、プラグインを有効化する(`~/.claude/settings.json`
   の `extraKnownMarketplaces` / `enabledPlugins` に追記。手元でのローカル
   インストールなら次のように directory ソースを指す):

   ```json
   "extraKnownMarketplaces": {
     "loop-hooks": {
       "source": {"source": "directory", "path": "/path/to/loop-hooks"}
     }
   },
   "enabledPlugins": {
     "loop-hooks@loop-hooks": true
   }
   ```

2. ゲートを掛けたいリポジトリのルートに `.loop-hooks.json` を置く(下記スキーマ)。

このプラグインの唯一の実行時要件は [`uv`](https://docs.astral.sh/uv/) が `PATH`
にあること。各フックは `uv run --script` の自己完結スクリプトなので、追加の
Python 環境構築は不要。

## 設定・状態・出力の契約

**`.loop-hooks.json`**(ゲート対象リポジトリのルートに置く):

```json
{
  "gate": {
    "command": "~/.local/bin/bun run verify quick",
    "timeout_sec": 600,
    "watch": ["*.ts", "*.tsx", "package.json", "tsconfig*.json"],
    "ignore": [".loop/*", "node_modules/*", "*.md"]
  }
}
```

- `gate.command` は必須(文字列)。それ以外(`timeout_sec` / `watch` / `ignore`)
  は省略可で、上記の値が既定になる。
- パターンは `fnmatch`(**`*` は `/` もまたぐ**。`docs/*` は `docs/a/b.md` にも
  一致する)。リポジトリ相対パスに対して照合する。`ignore` は `watch` より優先。
- `.loop-hooks.json` が無いリポジトリでは無効(dirty を記録しない/ゲートも掛けない)。
  ファイルはあるが `gate.command` が無い・JSON が壊れているなど不正な場合は、
  Stop 側が `systemMessage` で警告してゲートは無効のまま進める。

**状態ファイル** `.loop/state.json`(ゲート対象リポジトリ内。セッションを跨いで残る):

```json
{"dirty": true}
```

**evidence** `.loop/evidence.jsonl`(verify ランナー側が1実行1行で追記する。このプラグイン自体は書かない):

```json
{"ts":"2026-08-19T12:34:56.789Z","rev":"64db08b+dirty","stage":"quick","pass":false,"checks":[{"name":"typecheck","ok":true,"ms":4120},{"name":"unit","ok":false,"ms":9876}]}
```

## 手動スモーク

```bash
cd /tmp && mkdir -p loop-smoke && cd loop-smoke
echo '{"gate": {"command": "true"}}' > .loop-hooks.json
echo '{"tool_name":"Edit","cwd":"'$PWD'","tool_input":{"file_path":"'$PWD'/a.ts"}}' \
  | uv run ~/loop-hooks/hooks/post_tool_use/mark_dirty.py
cat .loop/state.json        # {"dirty": true}
echo '{"cwd":"'$PWD'","stop_hook_active":false}' \
  | uv run ~/loop-hooks/hooks/stop/gate.py
cat .loop/state.json        # {"dirty": false}
```

## テスト

```bash
cd ~/loop-hooks && uv run pytest -v
```
