"""配布物の検査: hooks.json が実在するスクリプトを指し、公開に必要な文書が揃っているか。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_hooks_jsonはSessionStartと3つのターン終了イベントを登録する():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert set(data["hooks"]) == {"SessionStart", "Stop", "SubagentStop", "TeammateIdle"}


def test_hooks_jsonのコマンドが実在するスクリプトを指す():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for entries in data["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                rel = hook["command"].split("${CLAUDE_PLUGIN_ROOT}/")[1].rstrip('"')
                assert (ROOT / rel).is_file(), f"{rel} が無い"


def test_plugin_jsonとmarketplace_jsonが読める():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "loop-hooks"
    assert market["plugins"][0]["name"] == "loop-hooks"


def test_LICENSEがある():
    assert (ROOT / "LICENSE").is_file()


def test_READMEは英語版と日本語版がある():
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "README.ja.md").is_file()


def test_英語READMEが日本語版へ導線を持つ():
    assert "README.ja.md" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_pyprojectとplugin_jsonのバージョンが一致する():
    import re
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == declared


def _hook_entries():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    return [h for entries in data["hooks"].values() for e in entries for h in e["hooks"]]


def test_ゲートフックのtimeoutはgate_timeout_secの上限より長い():
    """Claude Code 側が先にフックを殺すと、プロセスグループの後始末が走らない。"""
    import sys
    sys.path.insert(0, str(ROOT / "hooks"))
    from lib import config
    for hook in _hook_entries():
        if "gate.py" in hook["command"]:
            assert hook["timeout"] > config.TIMEOUT_MAX_SEC
    assert sum("gate.py" in h["command"] for h in _hook_entries()) == 3


def test_全フックにtimeoutがある():
    for hook in _hook_entries():
        timeout = hook.get("timeout")
        assert isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0, hook


def test_全フックにstatusMessageがある():
    for hook in _hook_entries():
        assert hook.get("statusMessage"), hook


def test_statusスキルが存在しnameがstatus():
    skill = (ROOT / "skills" / "status" / "SKILL.md").read_text(encoding="utf-8")
    head = skill.split("---")[1]
    assert "name: status" in head
    assert "gate.py\" --status" in skill
    assert "${CLAUDE_PLUGIN_ROOT}" in skill and "${CLAUDE_PROJECT_DIR}" in skill


def test_自リポジトリのゲート設定が有効で検証ランナーを指す():
    """spec §2.2: loop-hooks 自身にゲートを掛ける(ドッグフーディング)。"""
    import sys
    sys.path.insert(0, str(ROOT / "hooks"))
    from lib import config
    cfg = config.load(str(ROOT))
    assert cfg is not None and "_error" not in cfg, cfg
    gate = cfg["gate"]
    assert gate["command"] == "uv run python scripts/verify.py quick"
    assert "*.py" in gate["watch"] and "skills/**/*.md" in gate["watch"]
    assert ".github/**/*.yml" in gate["watch"]
    assert "docs/*" in gate["ignore"]


def test_statusスキルはCLAUDE_PLUGIN_DATAをコマンドに渡す():
    """0.3.2: `!` コマンドの環境にはこの変数が無いので、置換で明示的に渡す。"""
    skill = (ROOT / "skills" / "status" / "SKILL.md").read_text(encoding="utf-8")
    assert 'CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run' in skill
    head = skill.split("---")[1]
    assert 'CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" uv run' in head, "allowed-tools も同じ形に"
