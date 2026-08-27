"""status: ゲートの状態を集めて人間向けに整形する。コマンドは実行しない。"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import config, fingerprint, log, state, status  # noqa: E402

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


def test_fingerprintがNoneならverifiedの有無によらずwill_run(tmp_path, monkeypatch):
    """fingerprintが取れなければ、verifiedの有無によらず常にwill_run は True(gate と同じ判定式)。"""
    monkeypatch.setattr(status.fingerprint, "compute", lambda *a, **k: None)

    with_verified_dir = tmp_path / "with_verified"
    with_verified_dir.mkdir()
    with_verified = repo(with_verified_dir)
    state.write_verified(str(with_verified), "abc")
    assert status.collect(str(with_verified))["will_run"] is True

    without_verified_dir = tmp_path / "without_verified"
    without_verified_dir.mkdir()
    without_verified = repo(without_verified_dir)
    assert status.collect(str(without_verified))["will_run"] is True


def test_recentには最新のran記録が必ず含まれる(tmp_path):
    """0.3.1: skipped が 5 件続いても、最後に走った結果が status から消えない。"""
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "fail", "ms": 1200})
    for _ in range(8):
        log.append(root, {"event": "Stop", "decision": "skipped"})
    recent = status.collect(root)["recent"]
    assert len(recent) == status.RECENT + 1
    assert [r["decision"] for r in recent[: status.RECENT]] == ["skipped"] * status.RECENT
    assert recent[-1]["decision"] == "ran" and recent[-1]["result"] == "fail"


def test_recentに既にranがあれば重複して足さない(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "pass", "ms": 10})
    log.append(root, {"event": "Stop", "decision": "skipped"})
    recent = status.collect(root)["recent"]
    assert [r["decision"] for r in recent] == ["skipped", "ran"]


def test_stateに読んだ置き場を表示する(tmp_path, monkeypatch):
    """0.3.2: どこの記録を読んだかを常に見せる(静かな失敗を無くす)。"""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    info = status.collect(str(repo(tmp_path)))
    assert info["state_dir"] == str(tmp_path / "data" / "state")
    out = status.render(info)
    assert f"records   {tmp_path / 'data' / 'state'}" in out


def test_見出しにプラグインのバージョンを出す(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plugin_version", lambda: "9.9.9")
    out = status.render(status.collect(str(repo(tmp_path))))
    assert out.splitlines()[0] == "loop-hooks status (9.9.9)"


def test_バージョンが取れなくても見出しは出る(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plugin_version", lambda: None)
    out = status.render(status.collect(str(repo(tmp_path))))
    assert out.splitlines()[0] == "loop-hooks status"


def test_renderのゴールデン_有効で未検証(tmp_path, monkeypatch):
    """render の書式(ラベル幅・区切り・文言)を固定する。mutation で書式の変異を一括で殺す。"""
    monkeypatch.setattr(config, "plugin_version", lambda: "9.9.9")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "pass", "ms": 1234})
    info = status.collect(root)
    info["fingerprint"] = "f" * 64
    out = status.render(info)
    ts = info["recent"][0]["ts"][:16].replace("T", " ")
    expected = "\n".join(
        [
            "loop-hooks status (9.9.9)",
            f"  repo      {root}",
            "  config    HEAD (.loop-hooks.json)",
            f"  command   {GATE['command']}",
            "  on        stop, subagent_stop, teammate_idle",
            "  watch     *.ts",
            "  ignore    *.md",
            "  timeout   600s",
            "  state     changed since last pass -> gate will run at next stop",
            "  blocked   no",
            f"  records   {tmp_path / 'data' / 'state'}",
            f"  recent    {ts:<16} Stop          ran       pass  1.2s",
        ]
    )
    assert out == expected


def test_renderのゴールデン_設定なし(tmp_path):
    git(tmp_path, "init", "-q")
    out = status.render(status.collect(str(tmp_path)))
    assert out.splitlines()[1:] == [
        f"  repo      {tmp_path}",
        "  config    no .loop-hooks.json -> gate inactive in this repository",
    ]


def test_fingerprintとverifiedの値がcollectに残る(tmp_path):
    r = repo(tmp_path)
    fp = fingerprint.compute(str(r), GATE)
    state.write_verified(str(r), fp)
    info = status.collect(str(r))
    assert info["fingerprint"] == fp
    assert info["verified"] == fp


def test_recentの先頭5件にranがあれば古いranは足されない(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "old", "ms": 1})
    for _ in range(4):
        log.append(root, {"event": "Stop", "decision": "skipped"})
    log.append(root, {"event": "Stop", "decision": "ran", "result": "new", "ms": 2})
    for _ in range(4):
        log.append(root, {"event": "Stop", "decision": "skipped"})
    recent = status.collect(root)["recent"]
    assert len(recent) == status.RECENT
    assert any(r.get("decision") == "ran" and r.get("result") == "new" for r in recent)
    assert not any(r.get("result") == "old" for r in recent)


def test_format_recentは各項目を幅つきで並べる():
    r = {"ts": "2026-08-27T01:02:03Z", "event": "SubagentStop", "decision": "skipped"}
    assert status._format_recent(r) == "2026-08-27 01:02 SubagentStop  skipped"
    r2 = {
        "ts": "2026-08-27T01:02:03Z",
        "event": "Stop",
        "decision": "ran",
        "result": "fail",
        "ms": 10811,
        "note": "fingerprint unavailable",
    }
    assert (
        status._format_recent(r2)
        == "2026-08-27 01:02 Stop          ran       fail  10.8s fingerprint unavailable"
    )


def test_format_recentは項目が無ければ空文字で埋める():
    assert status._format_recent({}) == ""


def test_format_recentのms換算は1000で割る():
    r = {"ts": "2026-08-27T01:02:03Z", "event": "Stop", "decision": "ran", "ms": 100100}
    assert status._format_recent(r) == "2026-08-27 01:02 Stop          ran       100.1s"


def test_gitでないディレクトリでもstate行の文言が固定(tmp_path):
    (tmp_path / ".loop-hooks.json").write_text(json.dumps({"gate": GATE}), encoding="utf-8")
    info = status.collect(str(tmp_path))
    out = status.render(info)
    assert "  state     gate disabled: not a git repository" in out.splitlines()


def test_検証済みのstate行の文言が固定(tmp_path):
    r = repo(tmp_path)
    state.write_verified(str(r), fingerprint.compute(str(r), GATE))
    out = status.render(status.collect(str(r)))
    assert "  state     unchanged since last pass -> gate will not run" in out.splitlines()


def test_blockedがyesのときの文言が固定(tmp_path):
    r = repo(tmp_path)
    fp = fingerprint.compute(str(r), GATE)
    state.write_blocked(str(r), fp)
    out = status.render(status.collect(str(r)))
    assert "  blocked   yes (this state was already blocked once)" in out.splitlines()


def test_設定エラーの行が丸ごと固定(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".loop-hooks.json").write_text("{broken", encoding="utf-8")
    info = status.collect(str(tmp_path))
    out = status.render(info)
    assert out.splitlines()[1:] == [
        f"  repo      {tmp_path}",
        f"  config    gate disabled: {info['config_error']}",
    ]
    assert len(out.splitlines()) == 3


def test_通知行が丸ごと固定(tmp_path):
    r = repo(tmp_path, commit_config=False)
    info = status.collect(str(r))
    out = status.render(info)
    assert f"  notice    {info['notice']}" in out.splitlines()


def test_watchとignoreが複数ならカンマ区切りで並ぶ(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    gate = {"command": "true", "watch": ["*.ts", "*.js"], "ignore": ["*.md", "*.txt"]}
    (tmp_path / ".loop-hooks.json").write_text(json.dumps({"gate": gate}), encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "config")
    out = status.render(status.collect(str(tmp_path)))
    lines = out.splitlines()
    assert "  watch     *.ts, *.js" in lines
    assert "  ignore    *.md, *.txt" in lines


def test_recentが複数件なら2件目以降はラベルなしで並ぶ(tmp_path):
    root = str(repo(tmp_path))
    log.append(root, {"event": "Stop", "decision": "ran", "result": "pass", "ms": 1})
    log.append(root, {"event": "Stop", "decision": "skipped"})
    info = status.collect(root)
    out = status.render(info)
    lines = out.splitlines()
    idx = next(i for i, line in enumerate(lines) if line.startswith("  recent"))
    # 2 行目以降(2件目)は "  recent" ラベルを繰り返さず、空ラベル(_row("", ...))で揃う。
    expected = status._row("", status._safe_format_recent(info["recent"][1]))
    assert lines[idx + 1] == expected


def test_recentが無ければその旨を表示する(tmp_path):
    r = repo(tmp_path)
    out = status.render(status.collect(str(r)))
    assert "  recent    (no runs recorded)" in out.splitlines()


def test_configが無くてもinfoの全キーが揃う(tmp_path):
    """collect() の初期辞書のキー名を固定する(未使用のプレースホルダでも形は契約)。"""
    git(tmp_path, "init", "-q")
    info = status.collect(str(tmp_path))
    assert set(info.keys()) == {
        "cwd",
        "root",
        "config_source",
        "config_error",
        "notice",
        "command",
        "on",
        "watch",
        "ignore",
        "timeout_sec",
        "fingerprint",
        "verified",
        "will_run",
        "blocked",
        "recent",
        "state_dir",
    }
