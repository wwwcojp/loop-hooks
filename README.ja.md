# loop-hooks

[Claude Code](https://docs.claude.com/en/docs/claude-code/hooks) 用の Hooks プラグイン。
ターンの終了(Stop)で、リポジトリごとに指定した検証コマンド(`verify` など)を強制する。
ただし **前回ゲートを通った時点から実際に変化があったターンだけ** 走らせる。

English: [README.md](README.md)

## なぜ

「Stop フックでテストを走らせる」は Claude Code のフック解説の定番だが、素朴に実装すると
**毎ターン走る**。質問して答えが返るだけのターンでもテストスイートを60秒待たされ、結局
みんな切ってしまう。

loop-hooks はゲートを変更駆動にする。Stop 時に watch 対象のフィンガープリントを計算し、
前回ゲートが通った時点の記録と比較する。同じなら何も実行せずターンを終える。違えば
コマンドを実行し、失敗したらターンを終わらせない。

フィンガープリントは **git から見た作業ツリーの状態** から計算する。Claude がどのツールを
使ったかには依存しないので、`Edit`/`Write` はもちろん、`Bash` 経由の `sed`/ヒアドキュメント
編集、サブエージェントの書き込み、`git checkout`、フォーマッタ、コード生成、lockfile の
更新まで等しく捕捉する。編集を元に戻せばフィンガープリントも元の値に戻るので、壊して
直しただけのターンではゲートが走らない。

## 何をするか

スクリプト 1 つ `hooks/gate.py` が、エージェントが手を止めようとする3つの
イベント — **Stop** / **SubagentStop** / **TeammateIdle** — で走る
(`gate.on` でどれを掛けるか選べる)：

1. セッションの `cwd` からリポジトリルートを解決する(`git rev-parse --show-toplevel`)。
   サブディレクトリで起動していても効く。git worktree ではそのworktree自身のルートに
   解決されるので、worktreeごとに独立してゲートが掛かり、記録も別々になる。
2. そのルートの `.loop-hooks.json` を読む。**設定ファイルが無ければ何もしない**
   (オプトイン)。
3. 現在のフィンガープリントを計算する。`HEAD` の sha と、`HEAD` と一致しないパスのうち
   `watch` に一致し `ignore` に一致しないものの内容ハッシュ。
4. 記録済みの値と一致すれば、そのまま何もせず終える。
5. 違えば `gate.command` を実行する。成功したら **コマンド実行後に取り直した**
   フィンガープリントを記録する(フォーマッタ等がファイルを書き換えても再実行が
   繰り返されないようにするため)。失敗したら出力の末尾をフィードバックとして返し、
   修復するまで終わらせない。`Stop` と `SubagentStop` は
   `hookSpecificOutput.additionalContext`、`TeammateIdle` は形式が違うので
   終了コード2と stderr で同じ文面を返す。
6. エージェントを閉じ込めない。再入時(`stop_hook_active` が真)の2度目の失敗は
   `systemMessage` の警告にして通す。`TeammateIdle` には再入フラグが無いため、
   代わりに同じフィンガープリントを続けて2度ブロックしない。いずれの場合も記録は
   更新しないので、次に変化があれば再びゲートが掛かる。

## 動作要件

- [`uv`](https://docs.astral.sh/uv/) が `PATH` にあること。各フックは `uv run --script` の
  自己完結スクリプトなので、追加の Python 環境構築は不要。
- **git**。変更検出の土台になっている。git リポジトリでないディレクトリでは
  ゲートは無効のまま、警告だけ出す。

## 導入方法

1. marketplace を登録し、プラグインを有効化する(`~/.claude/settings.json`。
   手元のローカルインストールなら directory ソースを指す):

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

2. ゲートを掛けたいリポジトリのルートに `.loop-hooks.json` を置く。

## 設定

`.loop-hooks.json`(ゲート対象リポジトリのルート):

```json
{
  "gate": {
    "command": "bun run verify quick",
    "timeout_sec": 600,
    "watch": ["*.ts", "*.tsx", "package.json", "*tsconfig*.json"],
    "ignore": [".loop/*", "node_modules/*", "*.md"]
  }
}
```

| 項目 | 必須 | 既定値 | 備考 |
| --- | --- | --- | --- |
| `gate.command` | はい | — | シェル経由で実行する。`&&`、パイプ、`$VAR`、glob、`~` が使える。 |
| `gate.on` | いいえ | 3つとも | ゲートするイベント: `stop` / `subagent_stop` / `teammate_idle`。 |
| `gate.timeout_sec` | いいえ | `600` | 1以上の整数。タイムアウト時はプロセスグループごと落とすので、テストランナーが孤児として残らない。 |
| `gate.watch` | いいえ | 上記 | ゲートを発火させるパス。 |
| `gate.ignore` | いいえ | 上記 | `watch` より優先。 |

パターンはリポジトリ相対パスに対する `fnmatch`。**`*` は `/` もまたぐ**ので、
`docs/*` は `docs/a/b.md` にも一致する。

ファイルはあるが不正な場合(JSON が壊れている、`gate.command` が無い・空、型が違う)は、
ターンをブロックせず、Stop フックが理由を `systemMessage` で警告してゲートは無効のまま進む。

## 状態ファイル

**loop-hooks は利用者のリポジトリに一切書かない**ので、`.gitignore` に追記する
必要はない。状態はプラグインの永続データ領域、それが無い環境(手動実行など)では
XDG のキャッシュ配下に置く:

```
$CLAUDE_PLUGIN_DATA/state/<リポジトリパスのsha16>.json
~/.cache/loop-hooks/state/<リポジトリパスのsha16>.json
```

```json
{"root": "/home/alice/my-project", "verified": "9f2c…", "blocked": ""}
```

`verified` は最後にゲートを通った時点のフィンガープリント。`blocked` は
`TeammateIdle` 用の再入ガード。削除すれば次のターンで必ずゲートが走る。

## evidence(機能ではなく取り決め)

`.loop/evidence.jsonl` は、verify ランナー側が1実行1行で追記することを想定した場所。
**このプラグインは書きも読みもしない**。ランナーとゲートが形を揃えるための記述:

```json
{"ts":"2026-08-19T12:34:56.789Z","rev":"64db08b+dirty","stage":"quick","pass":false,"checks":[{"name":"typecheck","ok":true,"ms":4120},{"name":"unit","ok":false,"ms":9876}]}
```

## 手動スモーク

```bash
cd /tmp && rm -rf loop-smoke && mkdir loop-smoke && cd loop-smoke
git init -q && git commit -q --allow-empty -m init
echo '{"gate": {"command": "true", "watch": ["*.ts"]}}' > .loop-hooks.json
echo 'export const a = 1' > a.ts

echo '{"cwd":"'$PWD'","stop_hook_active":false}' | uv run ~/loop-hooks/hooks/gate.py
                        # 出力なし: ゲートが走って通った

echo '{"cwd":"'$PWD'","stop_hook_active":false}' | uv run ~/loop-hooks/hooks/gate.py
                        # 出力なし: 変化が無いのでゲートを飛ばした
```

## テスト

```bash
uv run pytest -v
```

## 制限

- git リポジトリであることが必要。
- フィンガープリントの記録はリポジトリごとに1つなので、同じ worktree で並行する
  複数セッションは記録を共有する。
- `Stop` の `hookSpecificOutput.additionalContext` は比較的新しい Claude Code を
  必要とする。古い版ではフィードバックが解釈されず、未検証のままターンが終わる。

## ライセンス

MIT — [LICENSE](LICENSE) を参照。
