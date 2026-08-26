"""session_start: セッション開始時に設定を検証し、ゲートの有効/無効を告知する。"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import session_start  # noqa: E402
from lib import log  # noqa: E402

GATE = {"command": "touch SHOULD_NOT_RUN", "watch": ["*.py"], "ignore": ["*.md"]}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=cwd, capture_output=True, check=True)


def repo(tmp_path: Path, body=None, commit: bool = True) -> dict:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    if body is not None:
        (tmp_path / ".loop-hooks.json").write_text(
            body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
        )
        if commit:
            git(tmp_path, "add", "-A")
            git(tmp_path, "commit", "-qm", "config")
    return {"cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "startup"}


def context(out) -> str | None:
    return (out or {}).get("hookSpecificOutput", {}).get("additionalContext")


def test_設定が無ければ何も出さない(tmp_path):
    assert session_start.handle(repo(tmp_path)) is None


def test_有効なら告知と1行のsystemMessage(tmp_path):
    out = session_start.handle(repo(tmp_path, {"gate": GATE}))
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "loop-hooks is active" in context(out)
    assert GATE["command"] in context(out)
    from lib import config

    assert out["systemMessage"] == (
        f"[loop-hooks {config.plugin_version()}] gate active: {GATE['command']}"
    )


def test_告知には対象イベントとwatchとignoreが入る(tmp_path):
    ctx = context(session_start.handle(repo(tmp_path, {"gate": GATE})))
    assert "stop, subagent_stop, teammate_idle" in ctx
    assert "*.py" in ctx and "*.md" in ctx
    assert "committed .loop-hooks.json" in ctx


def test_告知は事実文で命令形を含まない(tmp_path):
    ctx = context(session_start.handle(repo(tmp_path, {"gate": GATE})))
    for word in ("You must", "Do not", "Always", "Never"):
        assert word not in ctx


def test_未コミットなら告知に加えて通知の行が付く(tmp_path):
    out = session_start.handle(repo(tmp_path, {"gate": GATE}, commit=False))
    assert context(out)
    assert "gate active" in out["systemMessage"]
    assert "not committed" in out["systemMessage"]


def test_設定エラーは警告だけで告知しない(tmp_path):
    out = session_start.handle(repo(tmp_path, "{broken"))
    assert context(out) is None
    assert "gate disabled" in out["systemMessage"]


def test_gitでなければ警告だけで告知しない(tmp_path):
    (tmp_path / ".loop-hooks.json").write_text(json.dumps({"gate": GATE}), encoding="utf-8")
    out = session_start.handle(
        {"cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "startup"}
    )
    assert context(out) is None
    assert "not a git repository" in out["systemMessage"]


def test_compactでも告知する(tmp_path):
    event = {**repo(tmp_path, {"gate": GATE}), "source": "compact"}
    assert context(session_start.handle(event))


def test_コマンドは実行しない(tmp_path):
    event = repo(tmp_path, {"gate": GATE})
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    session_start.handle(event)
    assert not (tmp_path / "SHOULD_NOT_RUN").exists()


def test_ログにannouncedとsourceが残る(tmp_path):
    event = {**repo(tmp_path, {"gate": GATE}), "source": "resume"}
    session_start.handle(event)
    rec = log.tail(str(tmp_path), 1)[0]
    assert (rec["event"], rec["decision"], rec["source"]) == ("SessionStart", "announced", "resume")


def test_設定エラーはdisabledと記録される(tmp_path):
    session_start.handle(repo(tmp_path, "{broken"))
    assert log.tail(str(tmp_path), 1)[0]["decision"] == "disabled"


def test_SessionStartでは通知の重複排除を消費しない(tmp_path):
    from lib import state

    session_start.handle(repo(tmp_path, {"gate": GATE}, commit=False))
    assert state.read_noticed(str(tmp_path)) is None


def test_スクリプトは常に0で終わる(tmp_path):
    script = Path(__file__).resolve().parent.parent / "hooks" / "session_start.py"
    valid_stdin = json.dumps(repo(tmp_path, {"gate": GATE}))
    for stdin in (valid_stdin, "not json", ""):
        r = subprocess.run(
            [sys.executable, str(script)], input=stdin, capture_output=True, text=True
        )
        assert r.returncode == 0, stdin
        if stdin is valid_stdin:
            out = json.loads(r.stdout)
            assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
            assert out["systemMessage"].startswith("[loop-hooks ")
            assert "] gate active:" in out["systemMessage"]


def test_告知にプラグインのバージョンを出す(tmp_path, monkeypatch):
    """0.3.2: 設定は新しいがコードは古い、という状態を告知から判別できるようにする。"""
    from lib import config

    monkeypatch.setattr(config, "plugin_version", lambda: "9.9.9")
    out = session_start.handle(repo(tmp_path, {"gate": GATE}))
    assert out["systemMessage"] == f"[loop-hooks 9.9.9] gate active: {GATE['command']}"
