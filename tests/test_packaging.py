"""配布物の検査: hooks.json が実在するスクリプトを指し、公開に必要な文書が揃っているか。"""

import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

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

    sys.path.insert(0, str(ROOT))
    from hooks.lib import config

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
    assert 'gate.py" --status' in skill
    assert "${CLAUDE_PLUGIN_ROOT}" in skill and "${CLAUDE_PROJECT_DIR}" in skill


def test_自リポジトリのゲート設定が有効で検証ランナーを指す():
    """spec §2.2: loop-hooks 自身にゲートを掛ける(ドッグフーディング)。"""
    import sys

    sys.path.insert(0, str(ROOT))
    from hooks.lib import config

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


def test_ruffのSルールが有効でtestsだけS101とS603を除外する():
    """spec §3.2: hooks/scripts は行単位 noqa のみ、tests は per-file-ignores。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lint = cfg["tool"]["ruff"]["lint"]
    assert "S" in lint["select"]
    assert lint["per-file-ignores"] == {"tests/*": ["S101", "S603"]}


def test_dependabotがActionsを週次で追う():
    """spec §3.6: SHA ピン留めした Actions の更新は Dependabot に任せる。"""
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "github-actions"' in text
    assert 'interval: "weekly"' in text


def test_mutmutの設定とmutantsの除外():
    """spec §2.2: 対象は hooks/lib 6 本。mutants/ は git・ruff・pyright・ゲートの対象外。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mut = cfg["tool"]["mutmut"]
    assert mut["source_paths"] == ["hooks"]
    assert sorted(mut["only_mutate"]) == sorted(
        f"hooks/lib/{n}.py" for n in ("config", "fingerprint", "hook_io", "log", "state", "status")
    )
    assert "scripts" in mut["also_copy"] and ".loop-hooks.json" in mut["also_copy"]
    assert "mutants" in cfg["tool"]["ruff"]["extend-exclude"]
    assert "mutants" in cfg["tool"]["pyright"]["exclude"]
    assert "mutants/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    gate = json.loads((ROOT / ".loop-hooks.json").read_text(encoding="utf-8"))["gate"]
    assert "mutants/*" in gate["ignore"]


def test_hypothesisの除外設定():
    """spec §2.4: .hypothesis/(例のデータベース)は git・ruff・pyright・ゲートの対象外。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert ".hypothesis" in cfg["tool"]["ruff"]["extend-exclude"]
    assert ".hypothesis" in cfg["tool"]["pyright"]["exclude"]
    assert ".hypothesis/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    gate = json.loads((ROOT / ".loop-hooks.json").read_text(encoding="utf-8"))["gate"]
    assert ".hypothesis/*" in gate["ignore"]
    assert any(d.startswith("hypothesis") for d in cfg["dependency-groups"]["dev"])


def test_hypothesisのプロファイルが登録されている():
    from hypothesis import settings

    for name, n in (("default", 25), ("thorough", 300), ("mutation", 5)):
        prof = settings.get_profile(name)
        assert prof.max_examples == n and prof.deadline is None, name
