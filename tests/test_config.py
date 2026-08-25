"""config.load: 設定ファイルの読取と検証。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import config  # noqa: E402


def write(tmp_path: Path, body) -> str:
    (tmp_path / ".loop-hooks.json").write_text(
        body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
    )
    return str(tmp_path)


def test_設定が無いrepoではNone(tmp_path):
    assert config.load(str(tmp_path)) is None


def test_cwdがNoneならNone():
    assert config.load(None) is None


def test_正常な設定は既定値とマージされる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok"}})
    cfg = config.load(cwd)
    assert cfg["gate"]["command"] == "echo ok"
    assert cfg["gate"]["timeout_sec"] == 600
    assert cfg["gate"]["watch"] == ["*"]


def test_明示した値は既定値を上書きする(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "timeout_sec": 30, "watch": ["*.py"]}})
    cfg = config.load(cwd)
    assert cfg["gate"]["timeout_sec"] == 30
    assert cfg["gate"]["watch"] == ["*.py"]


def test_壊れたJSONは_errorになる(tmp_path):
    cwd = write(tmp_path, "{not json")
    cfg = config.load(cwd)
    assert "_error" in cfg


def test_commandが無い設定は_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"timeout_sec": 30}})
    cfg = config.load(cwd)
    assert "_error" in cfg


def test_ignoreが文字列だと_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "ignore": "*.md"}})
    cfg = config.load(cwd)
    assert "_error" in cfg


def test_timeout_secが文字列だと_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "timeout_sec": "600"}})
    cfg = config.load(cwd)
    assert "_error" in cfg


def test_timeout_secがboolだと_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "timeout_sec": True}})
    cfg = config.load(cwd)
    assert "_error" in cfg


def test_timeout_secが1未満だと_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "timeout_sec": 0}})
    cfg = config.load(cwd)
    assert "_error" in cfg


def test_watchに非文字列要素があると_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "watch": ["*.ts", 1]}})
    cfg = config.load(cwd)
    assert "_error" in cfg


def test_空のcommandは_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": ""}})
    assert "_error" in config.load(cwd)


def test_空白のみのcommandは_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "   "}})
    assert "_error" in config.load(cwd)


def test_エラーメッセージは英語(tmp_path):
    cwd = write(tmp_path, {"gate": {"timeout_sec": 30}})
    assert config.load(cwd)["_error"].isascii()


def test_onの既定は3イベントすべて(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok"}})
    assert config.load(cwd)["gate"]["on"] == ["stop", "subagent_stop", "teammate_idle"]


def test_onは明示して絞れる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "on": ["stop"]}})
    assert config.load(cwd)["gate"]["on"] == ["stop"]


def test_未知のonは_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "on": ["stop", "on_commit"]}})
    assert "_error" in config.load(cwd)


def test_onが文字列だと_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "on": "stop"}})
    assert "_error" in config.load(cwd)


def test_onが空リストだと_errorになる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "on": []}})
    assert "_error" in config.load(cwd)


def test_timeout_secが上限を超えると_errorになる(tmp_path):
    too_long = config.TIMEOUT_MAX_SEC + 1
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "timeout_sec": too_long}})
    assert "_error" in config.load(cwd)


def test_timeout_secは上限ちょうどまで許す(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok", "timeout_sec": config.TIMEOUT_MAX_SEC}})
    assert "_error" not in config.load(cwd)


# --- 0.2.1: HEAD にコミットされた設定を優先する ---

import subprocess  # noqa: E402


def git(cwd: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=cwd, capture_output=True, check=True)


def repo_with_committed(tmp_path: Path, body: dict) -> str:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    write(tmp_path, body)
    git(tmp_path, "add", ".loop-hooks.json")
    git(tmp_path, "commit", "-qm", "config")
    return str(tmp_path)


def test_作業ツリーで書き換えてもHEADの設定が使われる(tmp_path):
    cwd = repo_with_committed(tmp_path, {"gate": {"command": "committed"}})
    write(tmp_path, {"gate": {"command": "tampered"}})
    cfg = config.load(cwd)
    assert cfg["gate"]["command"] == "committed"
    assert "_notice" in cfg


def test_作業ツリーで壊してもHEADの設定が使われる(tmp_path):
    cwd = repo_with_committed(tmp_path, {"gate": {"command": "committed"}})
    write(tmp_path, "{broken")
    cfg = config.load(cwd)
    assert "_error" not in cfg
    assert cfg["gate"]["command"] == "committed"


def test_作業ツリーで削除してもHEADの設定が使われる(tmp_path):
    cwd = repo_with_committed(tmp_path, {"gate": {"command": "committed"}})
    (tmp_path / ".loop-hooks.json").unlink()
    cfg = config.load(cwd)
    assert cfg["gate"]["command"] == "committed"
    assert "_notice" in cfg


def test_HEADと一致していれば通知は無い(tmp_path):
    cwd = repo_with_committed(tmp_path, {"gate": {"command": "committed"}})
    assert "_notice" not in config.load(cwd)


def test_未コミットの設定は使われるが通知が付く(tmp_path):
    git(tmp_path, "init", "-q")
    cwd = write(tmp_path, {"gate": {"command": "untracked"}})
    cfg = config.load(cwd)
    assert cfg["gate"]["command"] == "untracked"
    assert "not committed" in cfg["_notice"]


def test_gitでないディレクトリでは通知が無い(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "plain"}})
    cfg = config.load(cwd)
    assert cfg["gate"]["command"] == "plain"
    assert "_notice" not in cfg


# --- 0.3.0: 既定値と _source ---

def test_watchの既定は全ファイル(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok"}})
    assert config.load(cwd)["gate"]["watch"] == ["*"]


def test_ignoreの既定に依存ディレクトリとドキュメントが含まれる(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "echo ok"}})
    ignore = config.load(cwd)["gate"]["ignore"]
    for pat in ("node_modules/*", "*/node_modules/*", ".venv/*", "*/.venv/*",
                "dist/*", "build/*", "target/*", ".claude/*", ".loop/*", "*.md"):
        assert pat in ignore, pat


def test_HEADから読んだ設定は_sourceがHEAD(tmp_path):
    cwd = repo_with_committed(tmp_path, {"gate": {"command": "committed"}})
    assert config.load(cwd)["_source"] == "HEAD"


def test_作業ツリーから読んだ設定は_sourceがworking_tree(tmp_path):
    git(tmp_path, "init", "-q")
    cwd = write(tmp_path, {"gate": {"command": "untracked"}})
    assert config.load(cwd)["_source"] == "working-tree"


def test_gitでないディレクトリの設定も_sourceはworking_tree(tmp_path):
    cwd = write(tmp_path, {"gate": {"command": "plain"}})
    assert config.load(cwd)["_source"] == "working-tree"


def test_設定エラーには_sourceが付かない(tmp_path):
    cwd = write(tmp_path, {"gate": {"timeout_sec": 30}})
    assert "_source" not in config.load(cwd)
