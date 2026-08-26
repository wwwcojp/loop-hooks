"""log: 判定ログの追記と読出。リポジトリの外に置き、失敗してもゲートを止めない。"""

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import log, state  # noqa: E402

REPO = "/somewhere/my-repo"


def logfile(root: str) -> Path:
    return state.state_dir() / f"{state.key(root)}.log.jsonl"


def test_初期状態では空():
    assert log.tail(REPO) == []


def test_追記した記録がtsつきで読める():
    log.append(REPO, {"event": "Stop", "decision": "skipped"})
    rows = log.tail(REPO)
    assert len(rows) == 1
    assert rows[0]["event"] == "Stop"
    assert rows[0]["ts"].endswith("Z")


def test_tailは新しい順():
    for i in range(3):
        log.append(REPO, {"event": "Stop", "decision": "ran", "i": i})
    assert [r["i"] for r in log.tail(REPO)] == [2, 1, 0]


def test_tailの件数を絞れる():
    for i in range(5):
        log.append(REPO, {"i": i})
    assert [r["i"] for r in log.tail(REPO, 2)] == [4, 3]


def test_リポジトリごとに別ファイル():
    log.append("/a/one", {"i": "one"})
    log.append("/b/two", {"i": "two"})
    assert log.tail("/a/one")[0]["i"] == "one"
    assert log.tail("/b/two")[0]["i"] == "two"


def test_リポジトリ内には何も書かない(tmp_path):
    log.append(str(tmp_path), {"event": "Stop"})
    assert list(tmp_path.iterdir()) == []


def test_壊れた行は飛ばす():
    log.append(REPO, {"i": 1})
    p = logfile(REPO)
    p.write_text(p.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    log.append(REPO, {"i": 2})
    assert [r["i"] for r in log.tail(REPO)] == [2, 1]


def test_不正なUTF8は飛ばす():
    log.append(REPO, {"i": 1})
    p = logfile(REPO)
    p.write_bytes(p.read_bytes() + b"\xff\xfe")
    result = log.tail(REPO)
    assert isinstance(result, list)


def test_上限を超えたら直近だけ残す():
    for i in range(log.MAX_LINES + 1):
        log.append(REPO, {"i": i})
    lines = logfile(REPO).read_text(encoding="utf-8").splitlines()
    assert len(lines) == log.KEEP_LINES
    assert json.loads(lines[-1])["i"] == log.MAX_LINES
    assert json.loads(lines[0])["i"] == log.MAX_LINES + 1 - log.KEEP_LINES


def test_書き込めなくても例外を出さない(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    d = tmp_path / "state"
    d.mkdir()
    d.chmod(stat.S_IRUSR | stat.S_IXUSR)  # 読取専用
    try:
        log.append(REPO, {"event": "Stop"})  # 例外が出なければ合格
        assert log.tail(REPO) == []
    finally:
        d.chmod(stat.S_IRWXU)


def test_切詰めは一時ファイル経由で差し替える(monkeypatch):
    """0.3.1: 途中で落ちてもログが欠けないよう、書いてから os.replace する。"""
    import os

    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(log.os, "replace", spy)
    root = "/home/USER/repo-trim"
    for i in range(log.MAX_LINES + 1):
        log.append(root, {"event": "Stop", "decision": "skipped", "i": i})
    assert replaced, "os.replace が呼ばれていない"
    assert replaced[-1][1] == str(log._path(root))
    assert not log._path(root).with_suffix(".jsonl.tmp").exists()
    recs = log.tail(root, log.KEEP_LINES + 10)
    assert len(recs) == log.KEEP_LINES and recs[0]["i"] == log.MAX_LINES
