"""hooks.json が実在するスクリプトを指しているかの検査。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_hooks_jsonのコマンドが実在するスクリプトを指す():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    events = data["hooks"]
    assert set(events) == {"PostToolUse", "Stop"}
    for entries in events.values():
        for entry in entries:
            for hook in entry["hooks"]:
                cmd = hook["command"]
                rel = cmd.split("${CLAUDE_PLUGIN_ROOT}/")[1].rstrip('"')
                assert (ROOT / rel).is_file(), f"{rel} が無い"


def test_plugin_jsonとmarketplace_jsonが読める():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "loop-hooks"
    assert market["plugins"][0]["name"] == "loop-hooks"
