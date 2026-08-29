"""log: 判定ログの追記と読出。リポジトリの外に置き、失敗してもゲートを止めない。"""

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import log, state  # noqa: E402

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


def test_tsはUTCのISO形式で秒まで():
    import re
    from datetime import datetime, timezone

    root = "/home/USER/repo-ts"
    log.append(root, {"event": "Stop", "decision": "skipped"})
    ts = log.tail(root, 1)[0]["ts"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts
    parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60


def test_上限ちょうどでは切り詰めない():
    root = "/home/USER/repo-edge"
    for i in range(log.MAX_LINES):
        log.append(root, {"event": "Stop", "decision": "skipped", "i": i})
    assert len(log.tail(root, log.MAX_LINES + 10)) == log.MAX_LINES


def test_上限を1超えたらKEEP_LINESに切り詰める():
    root = "/home/USER/repo-edge2"
    for i in range(log.MAX_LINES + 1):
        log.append(root, {"event": "Stop", "decision": "skipped", "i": i})
    recs = log.tail(root, log.MAX_LINES + 10)
    assert len(recs) == log.KEEP_LINES and recs[0]["i"] == log.MAX_LINES


def test_日本語の値はエスケープされずに書かれる():
    root = "/home/USER/repo-ja"
    log.append(root, {"msg": "ゲート"})
    raw = logfile(root).read_text(encoding="utf-8")
    assert "ゲート" in raw
    assert "\\u" not in raw


def test_置き場が多段で無くても作る(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "a" / "b"))
    log.append(REPO, {"event": "Stop"})
    assert log.tail(REPO)[0]["event"] == "Stop"


def test_tailの既定件数は5件():
    root = "/home/USER/repo-default5"
    for i in range(6):
        log.append(root, {"i": i})
    assert len(log.tail(root)) == 5


def test_切詰めの一時ファイル名はjsonl_tmpサフィックス(monkeypatch):
    import os

    seen_src: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen_src.append(str(src))
        real_replace(src, dst)

    monkeypatch.setattr(log.os, "replace", spy)
    root = "/home/USER/repo-tmpname"
    for i in range(log.MAX_LINES + 1):
        log.append(root, {"i": i})
    assert seen_src[-1] == str(log._path(root).with_suffix(".jsonl.tmp"))


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


# ---- failure_reason: 検証コマンドの出力から失敗理由を 1 行抽出する ----

RUNNER_OUT = "\n".join(
    [
        "$ uv run python scripts/verify.py quick",
        "[verify] leak: ok",
        "[verify] lint: FAIL",
        "$ uv run ruff check hooks tests scripts",
        "E501 Line too long (101 > 100)",
        "Found 2 errors.",
    ]
)
PYTEST_OUT = "....F..\nFAILED tests/test_x.py::test_y - assert 1 == 2\n1 failed, 3 passed in 0.5s\n"


def test_failure_reasonは最初のFAIL行を採る():
    assert log.failure_reason(RUNNER_OUT) == "[verify] lint: FAIL"


def test_failure_reasonはpytestのFAILED行を採る():
    assert log.failure_reason(PYTEST_OUT) == "FAILED tests/test_x.py::test_y - assert 1 == 2"


def test_failure_reasonは小文字のerrorコロンも採る():
    input_str = "$ make\nbuilding\nerror: link failed\ndone\n"
    assert log.failure_reason(input_str) == "error: link failed"


def test_failure_reasonは一致が無ければ最後の非空行():
    assert log.failure_reason("$ x\nFound 2 errors.\n\n\n") == "Found 2 errors."


def test_failure_reasonはコマンド行を候補にしない():
    # 1 行目の "$ cmd" は候補から外す(コマンド文字列に error: が含まれても拾わない)
    assert log.failure_reason("$ run --on-error: stop\nall good\n") == "all good"
    assert log.failure_reason("$ false\n") == ""


def test_failure_reasonはタイムアウトと実行不能をそのまま返す():
    assert log.failure_reason("$ cmd\ntimed out after 300s") == "timed out after 300s"
    assert log.failure_reason("$ cmd\ncould not run: [Errno 2] No such file") == (
        "could not run: [Errno 2] No such file"
    )


def test_failure_reasonは前後の空白を除き120字で切る():
    long = "FAIL " + "x" * 200
    out = log.failure_reason("  " + long + "  \n")
    assert out == long[: log.REASON_MAX_CHARS] and len(out) == 120


def test_failure_reasonは空出力で空文字():
    assert log.failure_reason("") == ""
    assert log.failure_reason("\n\n") == ""
