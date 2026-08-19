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
    assert cfg["gate"]["watch"] == ["*.ts", "*.tsx", "package.json", "*tsconfig*.json"]


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
