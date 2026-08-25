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

## 位置づけ

*Loop engineering* — [Addy Osmani](https://addyosmani.com/blog/loop-engineering/) が
長文で論じ、[Gergely Orosz](https://newsletter.pragmaticengineer.com/p/what-is-loop-engineering)
が実例を集め、Boris Cherny が「いまの自分の仕事はループを書くことだ」と言い切った
その言葉は、エージェントにプロンプトを打つ段階から、プロンプトを打つ仕組みそのものを
設計する段階への移行を指す。トリガー、スケジュール、worktree、`/goal`、サブエージェント、
メモリ。その大半はオーケストレーションであり、このプラグインはそのどれもやらない。

このプラグインがやるのは、その絵の中の狭い一点だ。どんなループも、Sonar の
[記事](https://www.sonarsource.com/blog/loop-engineering-without-verification-is-just-automation/)
が中心に据える問いに答えなければならない — *誰が、あるいは何が、ループの終了を宣言して
よいのか?* 答えが「作業をしたモデル自身」や「レビューを頼まれた別のモデル」なら、楽観主義者が
二人で頷き合っているだけで、行き着く先は早期完了ループ、つまり終わっていない作業に
「完了」を宣言する事故だ。その記事と Osmani が揃って辿り着くのが**決定論的な層**である —
Osmani の言い方を借りれば、意見を持つ検証者ではなく、作業を失格にできるチェック。
loop-hooks は Claude Code におけるその層を、最小の形で提供する。リポジトリが既に持っている
コマンドを、エージェントが手を止めようとするその瞬間に走らせ、結果を報告書ではなく
次の指示として突き返す。

この役割を真面目に引き受けると、設計上の選択が3つ決まる。

- **方向づけと強制は別の層。** `CLAUDE.md` は「良い状態とは何か」をエージェントに伝えるが、
  忘れられ、要約で削られ、理屈をつけて迂回される。フックはそうならない。方針は
  `CLAUDE.md` に、飛ばしてはならないものはフックに置く。
- **プラグインは「検証済み」の意味を知らない。** 知っているのはコマンドを走らせる方法と、
  ターンを終わらせない方法だけ。コマンドが何を検査するか — テスト、型、lint、静的解析、
  契約テスト — はリポジトリ側が `.loop-hooks.json` で決める。だから同じプラグインが
  TypeScript のモノレポにも Python の CLI にも効き、プラグインを変えずにゲートを厳しく
  できる。
- **コミット単位ではなくターン単位。** pre-commit フックはエージェントがコミットした時しか
  発火しない。CI は数分後、ループの外で、バグを生んだ文脈が消えた後に発火する。Stop フックは
  失敗がまだエージェント自身の問題であるうちに発火する。変更検出が、ターン単位を
  現実的なコストにしている。

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

## 組み合わせ

ゲートの価値は、その裏で走るコマンドの価値で決まる。日々の運用で効いている組み合わせを挙げる。

### TDD: GREEN を強制し、RED は委ねる

Stop ゲートは「終わる前にテストが通ることを確認せよ」を、指示から契約に変える —
サイクルの GREEN を強制する。RED については何も言わない。そこは
[tdd-guard](https://github.com/nizos/tdd-guard) のような `PreToolUse` ガードと組む。
あちらは失敗するテストの無い実装編集を拒み、こちらは動かないコードのまま止まることを拒む。

```json
{"gate": {"command": "npm test -- --run", "watch": ["src/**", "test/**"]}}
```

### CI をミラーする

`gate.command` に CI と同じコマンドを同じ順序で書き、両者のリストが一致することを
テストで固定する。「ローカルで通れば CI も通る」が願望ではなく検査された性質になる。
CI 側はランナーを呼ばず生のコマンドのままにしておくと、一致テストが比較する
独立した二つの出所を保てる。

```json
{"gate": {"command": "uv run ruff check . && uv run pytest -q"}}
```

### 検証を段階に分ける

Stop の経路に乗せたものは、watch 対象が変わったターンごとに必ず支払う。この層は速く保ち、
遅い層は明示的なステージの後ろに置く。`quick` / `mutation` / `all` のステージを持つ
verify ランナーが実際に機能している形で、`quick` は lint と単体テスト(ある Python
リポジトリの実測で 240 件が約1秒)、`all` はタスクを「完了」と呼ぶ前に走らせるもの。

```json
{"gate": {"command": "uv run python scripts/verify.py quick", "timeout_sec": 120}}
```

### Mutation testing とラチェット

ゲートはテストが通ることを証明する。テストに意味があるかは証明しない — アサーションを
弱めれば、エージェントはスイートを緑にできる。その穴を塞ぐのが mutation testing だ。
[mutmut](https://mutmut.readthedocs.io/)(Stryker や cargo-mutants でもよい)を遅い層で
走らせ、ファイル別スコアの baseline をコミットし、どれか一つでも下回ったらステージを
失敗させる。スコアは上がるしかない。あるセキュリティガードのコードベースでは 803 変異が
11秒で、生き残った変異はノイズではなく本物の未テスト境界を指していた。毎ターンには重く、
「完了」の条件にはちょうどいい。

### フォーマッタと生成器をゲートに入れる

`ruff format`、`prettier --write`、コード生成器をゲートに入れても安全だ。成功時に記録する
フィンガープリントは、コマンド実行の**後**に取るからだ。書き換えられたファイルは検証済み
状態の一部になり、次のターンでフォーマッタ自身の変更にゲートが反応することはない。

```json
{"gate": {"command": "ruff format . && ruff check . && pytest -q"}}
```

### マルチエージェント: 未検証の引き渡しを無くす

`on` を既定のままにしておけば、ゲートが赤いうちはサブエージェントは戻れず、teammate は
idle になれない。メインエージェントが検証されていない成果物を受け取ることがなくなる。
重複したゲートは無料だ。サブエージェントの実行でツリーが検証済みなら、メインの Stop は
一致するフィンガープリントを見つけて何も走らせない。

### 事前ガードと層を分ける

`PreToolUse` の deny/ask ガードと Stop ゲートは、別の問いに答える。ガードは行動が起きる前に
止める — 破壊的コマンド、秘密情報への書き込み、トークンを載せた外向きリクエスト。ゲートは
ターンが終わる前に結果を検査する。どちらも相手の領分は守れず、どちらも決定論的で、
どちらもフックだ。両方使う。

## これは何でないか

- **CI ではない。** 最終判定者は依然として CI。これはループを短くして、CI が捕まえるはずの
  ものの大半を、エージェントがまだ直せるうちに捕まえる。
- **テストの質の判定者ではない。** ゲートが緑なのは、コマンドが 0 で終了したという意味
  でしかない。上の mutation の項を見ること。
- **LLM レビュアーではない。** これは決定論的な層だ。意図や意味 — 「これは頼まれたもの
  か?」 — は `/goal` やレビュアーのサブエージェントと組み、このゲートは説得で判定を
  覆されない側でいさせる。
- **檻ではない。** 意図してフェイルオープンに作ってある。再入時の2度目の失敗は警告になり、
  Claude Code は連続ブロックを8回で打ち切り、`TeammateIdle` は同じ状態で2度ブロック
  されない。ただし忘れることは決してない — フィンガープリントは記録されないままなので、
  次の変化でゲートは戻ってくる。

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
