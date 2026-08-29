"""Claude Code とのフック入出力契約(第 5 段階 spec §2.2)。

tests/contracts/*.json がゴールデン。可変部分(<CWD> / <COMMAND> / <VERSION> / <OUTPUT>)を
正規化したうえで、入口の出力と辞書ごと完全一致することを検査する。

契約が変わったら(Claude Code 側のキー名・形、または入口の文言)、ゴールデンを手で直し、
`checked` を更新する。自動で書き戻す仕組みは意図的に作らない。
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks import gate, session_start  # noqa: E402
from hooks.lib import config, log  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "tests" / "contracts"
PLACEHOLDERS = ("<CWD>", "<COMMAND>", "<VERSION>", "<OUTPUT>")
GATE = {
    "on": ["stop", "subagent_stop", "teammate_idle"],
    "watch": ["*.ts"],
    "ignore": ["*.md"],
    "timeout_sec": 10,
}
# ケース名 → (準備の種類, 検証コマンド, 事前に同じイベントを流す回数)
CASES: dict[str, dict[str, Any]] = {
    "stop-pass": {"setup": "repo", "command": "true", "warmup": 0},
    "stop-fail": {"setup": "repo", "command": "false", "warmup": 0},
    "stop-reentry": {"setup": "repo", "command": "false", "warmup": 0},
    "subagent_stop-fail": {"setup": "repo", "command": "false", "warmup": 0},
    "teammate_idle-fail": {"setup": "repo", "command": "false", "warmup": 0},
    "teammate_idle-repeat": {"setup": "repo", "command": "false", "warmup": 1},
    "session_start-active": {"setup": "repo", "command": "true", "warmup": 0},
    "session_start-disabled": {"setup": "broken", "command": "", "warmup": 0},
    "session_start-not-git": {"setup": "no-git", "command": "true", "warmup": 0},
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=cwd, capture_output=True, check=True)


def prepare(name: str, tmp_path: Path) -> dict[str, str]:
    """ケースの前提(リポジトリ状態)を作り、正規化の文脈(cwd / command / version)を返す。"""
    case = CASES[name]
    ctx = {"cwd": str(tmp_path), "command": case["command"], "version": _version()}
    body = {"gate": {**GATE, "command": case["command"]}}
    if case["setup"] == "broken":
        body = {"gate": {"command": ""}}  # _validate が拒む(空 command)
    if case["setup"] == "no-git":
        (tmp_path / ".loop-hooks.json").write_text(json.dumps(body), encoding="utf-8")
        return ctx
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(body), encoding="utf-8")
    _git(tmp_path, "add", ".loop-hooks.json")
    _git(tmp_path, "commit", "-qm", "config")
    (tmp_path / "main.ts").write_text("source\n", encoding="utf-8")
    return ctx


def _version() -> str:
    v = config.plugin_version()
    assert v, "plugin.json の version が読めない"
    return v


def load_golden(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


def fill(value: Any, ctx: dict[str, str]) -> Any:
    """ゴールデンの input のプレースホルダを実値にする。"""
    if isinstance(value, dict):
        return {k: fill(v, ctx) for k, v in value.items()}
    if isinstance(value, str):
        return value.replace("<CWD>", ctx["cwd"])
    return value


def normalize(value: Any, ctx: dict[str, str]) -> Any:
    """入口の出力の可変部分をプレースホルダに戻す。"""
    if isinstance(value, dict):
        return {k: normalize(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, ctx) for v in value]
    if not isinstance(value, str):
        return value
    s = value.replace(ctx["cwd"], "<CWD>")
    s = s.replace(f"[loop-hooks {ctx['version']}]", "[loop-hooks <VERSION>]")
    if ctx["command"]:
        marker = f"$ {ctx['command']}\n"
        i = s.find(marker)
        if i >= 0:
            s = s[: i + len(marker)] + "<OUTPUT>"
        s = s.replace(f"`{ctx['command']}`", "`<COMMAND>`")
        s = s.replace(f"$ {ctx['command']}\n", "$ <COMMAND>\n")
        s = s.replace(f"gate active: {ctx['command']}", "gate active: <COMMAND>")
    return s


def _handle(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("hook_event_name") == "SessionStart":
        return session_start.handle(event)
    return gate.handle(event)


def run_case(name: str, tmp_path: Path) -> tuple[dict[str, Any] | None, int, str, dict[str, str]]:
    """in-process で入口を呼び、(正規化した出力, exit code, 正規化した stderr, ctx) を返す。"""
    ctx = prepare(name, tmp_path)
    golden = load_golden(name)
    event = fill(golden["input"], ctx)
    for _ in range(CASES[name]["warmup"]):
        _handle(event)
    out = dict(_handle(event) or {})
    exit_code = int(out.pop("_exit_code", 0))
    stderr = str(out.pop("_stderr", ""))
    return normalize(out, ctx) or None, exit_code, normalize(stderr, ctx), ctx


@pytest.mark.parametrize("name", sorted(CASES))
def test_ゴールデンが揃っている(name):
    golden = load_golden(name)
    assert set(golden) == {"reference", "checked", "input", "output", "exit_code", "stderr"}, name
    assert golden["reference"].startswith("https://") and len(golden["checked"]) == 10, name
    assert golden["input"]["cwd"] == "<CWD>"


@pytest.mark.parametrize("name", sorted(CASES))
def test_入口の出力はゴールデンと一致する(name, tmp_path):
    golden = load_golden(name)
    out, exit_code, stderr, _ = run_case(name, tmp_path)
    assert out == golden["output"], name
    assert exit_code == golden["exit_code"], name
    assert stderr == golden["stderr"], name


def test_stop_passは実際にゲートを走らせて通している(tmp_path):
    """output: null は skipped / off でも同じ形になる。ran で pass したことを記録で固定する。"""
    _, _, _, ctx = run_case("stop-pass", tmp_path)
    rec = log.tail(ctx["cwd"], 1)[0]
    assert rec["decision"] == "ran" and rec["result"] == "pass"


def test_ゴールデンのケースは9本で名前が揃っている():
    files = {p.stem for p in CONTRACTS.glob("*.json")}
    assert files == set(CASES), files ^ set(CASES)


# __main__ 経路(exit code と stderr は入口スクリプトを実際に起動しないと観測できない)。
# 9 本すべてだと 3 秒近く増えるので、形の異なる 3 本に絞る(spec §2.2)。
SUBPROCESS_CASES = ("stop-fail", "teammate_idle-fail", "session_start-active")


@pytest.mark.parametrize("name", SUBPROCESS_CASES)
def test_入口スクリプトの標準出力と終了コードはゴールデンと一致する(name, tmp_path):
    golden = load_golden(name)
    ctx = prepare(name, tmp_path)
    event = fill(golden["input"], ctx)
    entry = "session_start.py" if name.startswith("session_start") else "gate.py"
    r = subprocess.run(
        [sys.executable, str(ROOT / "hooks" / entry)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
    )
    out = normalize(json.loads(r.stdout), ctx) if r.stdout.strip() else None
    assert out == golden["output"], name
    assert r.returncode == golden["exit_code"], name
    assert normalize(r.stderr, ctx).rstrip("\n") == golden["stderr"], name
