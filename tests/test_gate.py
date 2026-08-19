"""gate: dirtyならコマンドを実行し、失敗ならターンを終わらせない。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks" / "stop"))
from lib import state  # noqa: E402
import gate  # noqa: E402


def setup_repo(tmp_path: Path, command: str, dirty: bool = True) -> dict:
    (tmp_path / ".loop-hooks.json").write_text(
        json.dumps({"gate": {"command": command, "timeout_sec": 10}}), encoding="utf-8")
    if dirty:
        state.set_dirty(str(tmp_path), True)
    return {"cwd": str(tmp_path), "stop_hook_active": False}


def test_設定が無いrepoでは何もしない(tmp_path):
    assert gate.handle({"cwd": str(tmp_path)}) is None


def test_dirtyでなければ実行しない(tmp_path):
    # command を「実行されたら痕跡を残す」ものにして、痕跡が無いことを確かめる
    marker = tmp_path / "ran"
    event = setup_repo(tmp_path, f"touch {marker}", dirty=False)
    assert gate.handle(event) is None
    assert not marker.exists()


def test_dirtyで成功したらdirtyが消えて通る(tmp_path):
    event = setup_repo(tmp_path, "true")
    assert gate.handle(event) is None
    assert state.is_dirty(str(tmp_path)) is False


def test_dirtyで失敗したらblockしdirtyは残る(tmp_path):
    event = setup_repo(tmp_path, "false")
    out = gate.handle(event)
    assert out["decision"] == "block"
    assert state.is_dirty(str(tmp_path)) is True


def test_blockのreasonに失敗出力の末尾が入る(tmp_path):
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\necho FAILURE_DETAIL\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    event = setup_repo(tmp_path, str(script))
    out = gate.handle(event)
    assert "FAILURE_DETAIL" in out["reason"]


def test_再入時の失敗はブロックせず警告で通す(tmp_path):
    event = setup_repo(tmp_path, "false")
    event["stop_hook_active"] = True
    out = gate.handle(event)
    assert "decision" not in out
    assert "systemMessage" in out
    assert state.is_dirty(str(tmp_path)) is True  # 未検証のまま=次のターンで再ゲート


def test_壊れた設定は警告を出してゲートしない(tmp_path):
    (tmp_path / ".loop-hooks.json").write_text("{broken", encoding="utf-8")
    state.set_dirty(str(tmp_path), True)
    out = gate.handle({"cwd": str(tmp_path), "stop_hook_active": False})
    assert "systemMessage" in out
    assert "decision" not in out


def test_実行できないコマンドはblockになる(tmp_path):
    event = setup_repo(tmp_path, "/no/such/binary-xyz")
    out = gate.handle(event)
    assert out["decision"] == "block"
