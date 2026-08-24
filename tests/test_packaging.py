"""配布物の検査: hooks.json が実在するスクリプトを指し、公開に必要な文書が揃っているか。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_hooks_jsonはStopフックだけを登録する():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert set(data["hooks"]) == {"Stop"}


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
