"""status: ゲートの状態を集めて人間向けに整形する。コマンドは実行しない。"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import fingerprint, log, state, status  # noqa: E402

GATE = {"command": "touch SHOULD_NOT_RUN", "watch": ["*.ts"], "ignore": ["*.md"]}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=cwd, capture_output=True, check=True)


def repo(tmp_path: Path, commit_config: bool = True) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".loop-hooks.json").write_text(json.dumps({"gate": GATE}), encoding="utf-8")
    if commit_config:
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-qm", "config")
    return tmp_path


def test_設定が無いリポジトリ(tmp_path):
    git(tmp_path, "init", "-q")
    info = status.collect(str(tmp_path))
    assert Path(info["root"]).resolve() == tmp_path.resolve()
    assert info["command"] is None and info["config_error"] is None
    assert "no .loop-hooks.json" in status.render(info)


def test_gitでないディレクトリ(tmp_path):
    (tmp_path / ".loop-hooks.json").write_text(json.dumps({"gate": GATE}), encoding="utf-8")
    info = status.collect(str(tmp_path))
    assert info["root"] is None
    assert "not a git repository" in status.render(info)


def test_設定エラー(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".loop-hooks.json").write_text("{broken", encoding="utf-8")
    info = status.collect(str(tmp_path))
    assert "cannot read" in info["config_error"]
    assert "gate disabled" in status.render(info)


def test_有効で未検証ならwill_run(tmp_path):
    r = repo(tmp_path)
    (r / "a.ts").write_text("x\n", encoding="utf-8")
    info = status.collect(str(r))
    assert info["config_source"] == "HEAD"
    assert info["command"] == GATE["command"]
    assert info["will_run"] is True
    assert "gate will run" in status.render(info)


def test_有効で検証済みならwill_runでない(tmp_path):
    r = repo(tmp_path)
    state.write_verified(str(r), fingerprint.compute(str(r), GATE))
    info = status.collect(str(r))
    assert info["will_run"] is False
    assert "unchanged since last pass" in status.render(info)


def test_blockedは現在の指紋と一致するときだけ真(tmp_path):
    r = repo(tmp_path)
    fp = fingerprint.compute(str(r), GATE)
    state.write_blocked(str(r), fp)
    assert status.collect(str(r))["blocked"] is True
    (r / "b.ts").write_text("y\n", encoding="utf-8")
    assert status.collect(str(r))["blocked"] is False


def test_未コミット設定の通知が載る(tmp_path):
    r = repo(tmp_path, commit_config=False)
    info = status.collect(str(r))
    assert info["config_source"] == "working-tree"
    assert "not committed" in info["notice"]
    assert "not committed" in status.render(info)


def test_直近のログが新しい順に載る(tmp_path):
    r = repo(tmp_path)
    log.append(str(r), {"event": "Stop", "decision": "ran", "result": "fail", "ms": 2300})
    log.append(str(r), {"event": "Stop", "decision": "skipped"})
    info = status.collect(str(r))
    assert [x["decision"] for x in info["recent"]] == ["skipped", "ran"]
    out = status.render(info)
    assert out.index("skipped") < out.index("fail")


def test_コマンドを実行しない(tmp_path):
    r = repo(tmp_path)
    (r / "a.ts").write_text("x\n", encoding="utf-8")
    status.render(status.collect(str(r)))
    assert not (r / "SHOULD_NOT_RUN").exists()


def test_壊れたログ記録でも例外にならない(tmp_path):
    r = repo(tmp_path)
    log.append(str(r), {"event": None, "decision": "ran", "result": None})
    info = status.collect(str(r))
    out = status.render(info)
    assert isinstance(out, str)


def test_fingerprintがNoneならverifiedと比較してwill_run(tmp_path, monkeypatch):
    monkeypatch.setattr(status.fingerprint, "compute", lambda *a, **k: None)

    with_verified_dir = tmp_path / "with_verified"
    with_verified_dir.mkdir()
    with_verified = repo(with_verified_dir)
    state.write_verified(str(with_verified), "abc")
    assert status.collect(str(with_verified))["will_run"] is True

    without_verified_dir = tmp_path / "without_verified"
    without_verified_dir.mkdir()
    without_verified = repo(without_verified_dir)
    assert status.collect(str(without_verified))["will_run"] is False


def test_recentには最新のran記録が必ず含まれる(tmp_path):
    """0.3.1: skipped が 5 件続いても、最後に走った結果が status から消えない。"""
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "fail", "ms": 1200})
    for _ in range(8):
        log.append(root, {"event": "Stop", "decision": "skipped"})
    recent = status.collect(root)["recent"]
    assert len(recent) == status.RECENT + 1
    assert [r["decision"] for r in recent[:status.RECENT]] == ["skipped"] * status.RECENT
    assert recent[-1]["decision"] == "ran" and recent[-1]["result"] == "fail"


def test_recentに既にranがあれば重複して足さない(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "pass", "ms": 10})
    log.append(root, {"event": "Stop", "decision": "skipped"})
    recent = status.collect(root)["recent"]
    assert [r["decision"] for r in recent] == ["skipped", "ran"]
