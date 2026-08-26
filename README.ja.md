# loop-hooks

[![CI](https://github.com/wwwcojp/loop-hooks/actions/workflows/ci.yml/badge.svg)](https://github.com/wwwcojp/loop-hooks/actions/workflows/ci.yml)

[Claude Code](https://docs.claude.com/en/docs/claude-code/hooks) 用の Hooks プラグイン。
ターンの終了(Stop)で、リポジトリごとに指定した検証コマンド(`verify` など)を強制する。
ただし **前回ゲートを通った時点から実際に変化があったターンだけ** 走らせる。

English: [README.md](README.md)

## なぜ

「Stop フックでテストを走らせる」は Claude Code のフック解説でよく紹介されるパターンだが、
素朴に実装すると**毎ターン走る**。質問に答えるだけのターンでもテストスイートの完了を
待つことになり、実用にならず無効化されがちだ。

loop-hooks はゲートを変更駆動にする。Stop 時に watch 対象のフィンガープリントを計算し、
前回ゲートが通った時点の記録と比較する。同じなら何も実行せずターンを終える。違えば
コマンドを実行し、失敗したらターンを終わらせない。

フィンガープリントは **git から見た作業ツリーの状態** から計算する。Claude がどのツールを
使ったかには依存しないので、`Edit`/`Write` はもちろん、`Bash` 経由の `sed`/ヒアドキュメント
編集、サブエージェントの書き込み、`git checkout`、フォーマッタ、コード生成、lockfile の
更新まで等しく捕捉する。編集を元に戻せばフィンガープリントも元の値に戻るので、壊して
直しただけのターンではゲートが走らない。

## 位置づけ

*Loop engineering* は、エージェントに個別にプロンプトを与える段階から、エージェントを
動かす仕組み(トリガー、スケジュール、worktree、`/goal`、サブエージェント、メモリ)を
設計する段階への移行を指す用語で、[Addy Osmani](https://addyosmani.com/blog/loop-engineering/)
の記事や [Gergely Orosz](https://newsletter.pragmaticengineer.com/p/what-is-loop-engineering)
の調査で整理されている。その範囲の大半はオーケストレーションであり、このプラグインは
そこには関与しない。

このプラグインが担うのは、ループの**終了判定**の一部分だ。Sonar の
[記事](https://www.sonarsource.com/blog/loop-engineering-without-verification-is-just-automation/)
は、ループ設計の中心的な問題を「何がループの終了を判定するのか」と整理している。判定を
作業したモデル自身や、レビューを依頼した別のモデルに委ねると、判定は確率的なままで、
未完了の作業に「完了」を返す早期完了が起きうる。同記事と Osmani はいずれも、その対策として
**決定論的な検証層**を置くことを挙げている。Osmani の整理では、意見を返す検証者ではなく、
作業を失格にできるチェックがそれにあたる。

loop-hooks は Claude Code におけるこの決定論的な層を、最小の構成で提供する。具体的には、
リポジトリが既に持つ検証コマンドをエージェントの停止時に実行し、失敗した場合はその出力を
次の指示としてエージェントに返す。

設計上の前提は次の3点。

- **方向づけと強制は別の層に置く。** `CLAUDE.md` はエージェントに方針を伝えるが、
  コンテキストの要約や判断の過程で無視されうる。フックは実行が保証される。方針は
  `CLAUDE.md` に、省略されては困る検証はフックに置く。
- **プラグインは検証の中身を持たない。** プラグインが行うのはコマンドの実行とターン終了の
  制御だけで、何を検証するか(テスト、型検査、lint、静的解析、契約テストなど)はリポジトリ側の
  `.loop-hooks.json` で決める。この分離により、言語やスタックを問わず同じプラグインを使え、
  検証を厳しくする変更もリポジトリ側で完結する。
- **コミット単位ではなくターン単位で検証する。** pre-commit フックはエージェントがコミット
  しない限り発火せず、CI はループの外で数分後に結果を返す。Stop フックはエージェントがまだ
  同じコンテキストを保持している時点で失敗を返せる。変更検出により、ターンごとの実行コストを
  許容範囲に抑えている。

## 何をするか

セッション開始時には `hooks/session_start.py` が設定を検証し、ゲートを告知する
(コマンドは実行しない)。

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
6. エージェントを閉じ込めない。同じフィンガープリントは続けて2度ブロックしない
   (作業ツリーに変化が無い＝修正を試みていない)。再入時(`stop_hook_active` が真)の
   2度目の失敗は `systemMessage` の警告にして通す。いずれの場合も記録は更新しない
   ので、次に変化があれば再びゲートが掛かる。

## 動作要件

- [`uv`](https://docs.astral.sh/uv/) が `PATH` にあること。各フックは `uv run --script` の
  自己完結スクリプトなので、追加の Python 環境構築は不要。
- **git**。変更検出の土台になっている。git リポジトリでないディレクトリでは
  ゲートは無効のまま、警告だけ出す。

## 導入方法

Claude Code の中から marketplace を登録してプラグインをインストールする:

```
/plugin marketplace add wwwcojp/loop-hooks
/plugin install loop-hooks@loop-hooks
```

`~/.claude/settings.json` に書いても同じ:

```json
"extraKnownMarketplaces": {
  "loop-hooks": {
    "source": {"source": "github", "repo": "wwwcojp/loop-hooks"}
  }
},
"enabledPlugins": {
  "loop-hooks@loop-hooks": true
}
```

そのうえで、ゲートを掛けたいリポジトリのルートに `.loop-hooks.json` を置く
([設定](#設定) を参照)。このファイルが無いリポジトリでは何も起きない。

プラグイン自体を開発する場合は、marketplace をローカルのチェックアウトに向ける:

```json
"extraKnownMarketplaces": {
  "loop-hooks": {
    "source": {"source": "directory", "path": "/path/to/loop-hooks"}
  }
}
```

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
| `gate.timeout_sec` | いいえ | `600` | 1〜3000 の整数。タイムアウト時はプロセスグループごと落とすので、テストランナーが孤児として残らない。 |
| `gate.watch` | いいえ | `["*"]` | ゲートを発火させるパス。`watch` を省略すると全ファイルが対象になる。 |
| `gate.ignore` | いいえ | `["node_modules/*", "*/node_modules/*", ".venv/*", "*/.venv/*", "dist/*", "build/*", "target/*", ".claude/*", ".loop/*", "*.md"]` | `watch` より優先。 |

パターンはリポジトリ相対パスに対する `fnmatch`。**`*` は `/` もまたぐ**ので、
`docs/*` は `docs/a/b.md` にも一致する。

ファイルはあるが不正な場合(JSON が壊れている、`gate.command` が無い・空、型が違う)は、
ターンをブロックせず、Stop フックが理由を `systemMessage` で警告してゲートは無効のまま進む。

**ファイルはコミットすること。** git リポジトリでは、ゲートは `.loop-hooks.json` を作業
ツリーではなく `HEAD` から読む。作業ツリーでの書き換え・破壊・削除(エージェントにも
できる操作)ではゲートは変わらず、無効にもならない。差異は `systemMessage` で通知される。
未コミットのファイルも使えるが、コミットを促す通知が一度出る。

## 組み合わせ

ゲートの効果は `gate.command` の内容で決まる。運用上有効だった組み合わせを挙げる。

### TDD: GREEN を強制し、RED は別のフックに任せる

Stop ゲートは TDD サイクルのうち GREEN(テストが通ること)を強制する。RED(失敗する
テストを先に書くこと)は対象外なので、[tdd-guard](https://github.com/nizos/tdd-guard) の
ような `PreToolUse` ガードと組み合わせる。tdd-guard は失敗するテストの無い実装編集を
拒否し、loop-hooks はテストが失敗した状態でのターン終了を拒否する。

```json
{"gate": {"command": "npm test -- --run", "watch": ["src/**", "test/**"]}}
```

### CI と同じコマンドを使う

`gate.command` に CI と同じコマンドを同じ順序で記述し、両者が一致することをテストで
検査する。これにより「ローカルで通れば CI も通る」を保証できる。CI 側はランナーを経由せず
生のコマンドを書いておくと、一致テストが独立した2つの定義を比較できる。

```json
{"gate": {"command": "uv run ruff check . && uv run pytest -q"}}
```

### 検証を段階に分ける

`gate.command` は watch 対象が変更されたターンごとに実行されるため、Stop で走らせる段階は
短時間で終わるものに限定し、時間のかかる検証は別のステージに分ける。`quick` / `mutation` /
`all` のようなステージを持つ verify ランナーを用意し、`quick`(lint + 単体テスト。実測では
Python リポジトリのテスト 240 件で約1秒)を Stop に、`all` をタスク完了時に実行する構成が
実用的だった。

```json
{"gate": {"command": "uv run python scripts/verify.py quick", "timeout_sec": 120}}
```

### Mutation testing とラチェット

ゲートはテストが通ることを保証するが、テストが十分かは保証しない。アサーションを弱めれば
テストは通るためだ。この点は mutation testing で補う。[mutmut](https://mutmut.readthedocs.io/)
(Stryker、cargo-mutants 等でも同様)を時間のかかる側のステージで実行し、ファイル別スコアの
baseline をリポジトリにコミットして、baseline を下回った場合にステージを失敗させる
(ラチェット)。実測では、セキュリティガードのコードベースで 803 変異が約11秒で完了し、
生き残った変異が実際に未テストだった境界条件を示した。毎ターン実行するには時間がかかる
ため、タスク完了の条件として使う。

### フォーマッタや生成器をゲートに含める

成功時に記録するフィンガープリントはコマンド実行**後**に取得するため、`ruff format` や
`prettier --write`、コード生成器を `gate.command` に含めても、それらが書き換えたファイルに
よって次のターンでゲートが再実行されることはない。

```json
{"gate": {"command": "ruff format . && ruff check . && pytest -q"}}
```

### マルチエージェント

`gate.on` を既定(3イベント全て)のままにすると、ゲートが失敗している間はサブエージェントは
完了できず、teammate は idle になれない。メインエージェントに未検証の成果物が渡ることを
防げる。ゲートが重複しても、サブエージェントの実行でツリーが検証済みになっていれば
メインの Stop ではフィンガープリントが一致し、コマンドは実行されない。

### 実行前ガードとの併用

`PreToolUse` の deny/ask ガードと Stop ゲートは役割が異なる。ガードは破壊的コマンドや
秘密情報への書き込み、トークンを含む外部送信などを実行前に止める。ゲートはターン終了前に
結果を検証する。互いの範囲は重ならないため、併用する。

## 適用範囲外

- **CI の代替ではない。** 最終的な判定は CI が行う。このプラグインは、CI が検出する問題の
  多くをエージェントが修正可能な時点で検出するためのもの。
- **テストの質は判定しない。** ゲートの成功はコマンドの終了コードが 0 だったことのみを
  意味する。テストの質は上記の mutation testing で補う。
- **LLM によるレビューではない。** 決定論的な層として動作する。意図や仕様との一致
  (「依頼どおりか」)の判断は `/goal` やレビュー用サブエージェントと組み合わせる。
- **無限ループにはならない。** 意図的にフェイルオープンとして設計している。同じ
  フィンガープリントは2回続けてブロックしない(変更が無い＝修正を試みていない)。再入時の
  2回目の失敗は警告に変わり、Claude Code 側でも連続ブロックは8回で打ち切られる。
  いずれの場合もフィンガープリントは記録されないので、次に変更があればゲートは再び
  実行される。

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

`verified` は最後にゲートを通った時点のフィンガープリント。`blocked` は最後にブロック
した時点のもので、同じ状態を2度ブロックしないために使う。削除すれば次のターンで必ず
ゲートが走る。

## トラブルシューティング

**このリポジトリでゲートは有効か:** Claude Code の中で `/loop-hooks:status` を実行するか、
ターミナルから `uv run /path/to/loop-hooks/hooks/gate.py --status [repo]` を実行する。設定が
どこから読まれたか、ゲートが何を実行するか、次の stop で走るか、直近5件の判定を表示する。

**セッション開始時に `[loop-hooks <version>] gate active:` の行が出ない**(`.loop-hooks.json` は
あるのに): このセッションではプラグインが読み込まれていない。フック定義はセッション
開始時にスナップショットされるため、プラグインを更新した後は Claude Code を再起動する。

**判定ログ:** `$CLAUDE_PLUGIN_DATA/state/<key>.log.jsonl`。1判定につき1行の JSON、最新が
最後。`--status` は読んだ置き場を `records` 行に表示する。ターミナルから(`CLAUDE_PLUGIN_DATA`
無しで)実行した場合は `~/.claude/plugins/data/loop-hooks-*/` を探し、無ければ
`~/.cache/loop-hooks/state/` を読む。

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

このリポジトリはこのプラグイン自身をゲートしている。`uv run python scripts/verify.py quick` は CI の `test` ジョブと同じチェックを実行する(ホームパスリーク検査、ruff check/format、import-linter、pyright、pytest)。

## 制限

- git リポジトリであることが必要。
- フィンガープリントの記録はリポジトリごとに1つなので、同じ worktree で並行する
  複数セッションは記録を共有する。
- `Stop` の `hookSpecificOutput.additionalContext` は比較的新しい Claude Code を
  必要とする。古い版ではフィードバックが解釈されず、未検証のままターンが終わる。

## ライセンス

MIT — [LICENSE](LICENSE) を参照。
