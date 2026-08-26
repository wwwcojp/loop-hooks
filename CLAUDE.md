# loop-hooks

Claude Code のフックプラグイン。ターン終了時にリポジトリの検証コマンドを実行し、
失敗ならターンを終わらせない。設計の背景は `README.md` の "Where it fits"、
仕様は `docs/superpowers/specs/`。

## 自リポジトリでの作業時の注意(ドッグフーディング)

このリポジトリには loop-hooks 自身のゲートが掛かっている(`.loop-hooks.json` →
`uv run python scripts/verify.py quick`)。想定内の挙動なので、以下で扱う。

1. **セッションで有効なプラグインは GitHub 版(marketplace `source: github`)。**
   作業ツリーの `hooks/` を編集してもゲートの挙動は変わらない。作業ツリーのコードは
   verify ランナー経由の pytest でだけ実行される。`directory` ソースで自インストールして
   動作確認したら、終わったら GitHub 版に戻して Claude Code を再起動する。
2. **入口ファイル(`hooks/gate.py`・`hooks/session_start.py`・`hooks/hooks.json`)を
   動かさない。** フック定義はセッション開始時のスナップショットなので、動かすと稼働中の
   セッションでゲートが無言で消える。動かす場合はリリースノートに「再起動が必要」と書く。
3. **ゲートで止められたらコードを直す。** `.loop-hooks.json` を変えて通さない、
   `disableAllHooks` を使わない。設定は HEAD 版が優先されるので、作業ツリーで書き換えても
   ゲートは変わらない(0.2.1)。
4. **プラグインを更新したら Claude Code を再起動する。** 再起動後の最初のセッションで
   `[loop-hooks] gate active: uv run python scripts/verify.py quick` が出ることが、
   更新が効いた確認。出なければ `/loop-hooks:status`。
5. `quick` は CI と同じ 3 コマンド(leak → ruff → pytest)。CI を変えるときは
   `scripts/verify.py` も変える(`tests/test_verify.py::test_quick_stage_mirrors_ci` が検出する)。

## 開発

- テスト: `uv run pytest -q`(`tests/conftest.py` が状態ディレクトリを tmp に隔離する)
- 検証一式: `uv run python scripts/verify.py quick`
- 状態の確認: `uv run hooks/gate.py --status .` または `/loop-hooks:status`
- 実ホームパスをソース・コミットメッセージに書かない(CI が落ちる)。プレースホルダーは `/home/USER`
