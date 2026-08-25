"""gate: 前回グリーンから変化していれば検証を実行し、失敗ならターンを終わらせない。"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import gate  # noqa: E402
from lib import fingerprint, state  # noqa: E402

WATCH = ["*.ts"]
IGNORE = [".loop/*", "*.md"]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=cwd, capture_output=True, check=True)


def setup_repo(tmp_path: Path, command: str, timeout_sec: int = 10) -> dict:
    """watch対象に未検証の変更があるgitリポジトリを作り、Stopイベントを返す。"""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(
        {"gate": {"command": command, "timeout_sec": timeout_sec,
                  "watch": WATCH, "ignore": IGNORE}}), encoding="utf-8")
    (tmp_path / "main.ts").write_text("source\n", encoding="utf-8")
    return {"cwd": str(tmp_path), "stop_hook_active": False}


def blocked(out) -> str | None:
    """ブロックしているならフィードバック文面。していなければ None。

    Stop/SubagentStop は additionalContext、TeammateIdle は exit 2 + stderr。
    """
    if not out:
        return None
    hso = out.get("hookSpecificOutput")
    if hso:
        return hso.get("additionalContext")
    if out.get("_exit_code") == 2:
        return out.get("_stderr")
    return None


def mark_verified(tmp_path: Path) -> None:
    cfg = {"watch": WATCH, "ignore": IGNORE}
    state.write_verified(str(tmp_path), fingerprint.compute(str(tmp_path), cfg))


# --- ゲートを掛けない条件 ---

def test_設定が無いrepoでは何もしない(tmp_path):
    git(tmp_path, "init", "-q")
    assert gate.handle({"cwd": str(tmp_path)}) is None


def test_gitリポジトリでなければ警告してゲートしない(tmp_path):
    marker = tmp_path / "ran"
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(
        {"gate": {"command": f"touch {marker}"}}), encoding="utf-8")
    out = gate.handle({"cwd": str(tmp_path), "stop_hook_active": False})
    assert "systemMessage" in out
    assert blocked(out) is None
    assert not marker.exists()


def test_壊れた設定は警告を出してゲートしない(tmp_path):
    (tmp_path / ".loop-hooks.json").write_text("{broken", encoding="utf-8")
    out = gate.handle({"cwd": str(tmp_path), "stop_hook_active": False})
    assert "systemMessage" in out
    assert blocked(out) is None


def test_空のコマンドは警告になる(tmp_path):
    event = setup_repo(tmp_path, "")
    out = gate.handle(event)
    assert "systemMessage" in out
    assert blocked(out) is None


def test_検証済みの状態では実行しない(tmp_path):
    marker = tmp_path / "ran"
    event = setup_repo(tmp_path, f"touch {marker}")
    mark_verified(tmp_path)
    assert gate.handle(event) is None
    assert not marker.exists()


def test_watch対象外の変更では実行しない(tmp_path):
    marker = tmp_path / "ran"
    event = setup_repo(tmp_path, f"touch {marker}")
    mark_verified(tmp_path)
    (tmp_path / "notes.md").write_text("doc\n", encoding="utf-8")
    assert gate.handle(event) is None
    assert not marker.exists()


# --- ゲートを掛ける条件 ---

def test_未検証の変更があれば実行して通る(tmp_path):
    marker = tmp_path / "ran"
    event = setup_repo(tmp_path, f"touch {marker}")
    assert gate.handle(event) is None
    assert marker.exists()


def test_成功したら次のStopでは実行しない(tmp_path):
    counter = tmp_path / "runs"
    event = setup_repo(tmp_path, f"echo x >> {counter}")
    gate.handle(event)
    gate.handle(event)
    assert counter.read_text().count("x") == 1


def test_ゲートが対象ファイルを書き換えても再実行にならない(tmp_path):
    """検証コマンド自身がwatch対象を変更しても(フォーマッタ等)、無限に再実行しない。"""
    counter = tmp_path / "runs"
    event = setup_repo(tmp_path, f"echo x >> {counter}; echo formatted >> main.ts")
    gate.handle(event)
    gate.handle(event)
    assert counter.read_text().count("x") == 1


def test_成功後に再び編集すると実行する(tmp_path):
    counter = tmp_path / "runs"
    event = setup_repo(tmp_path, f"echo x >> {counter}")
    gate.handle(event)
    (tmp_path / "main.ts").write_text("edited again\n", encoding="utf-8")
    gate.handle(event)
    assert counter.read_text().count("x") == 2


def test_git経由の変更でも実行する(tmp_path):
    """Edit/Writeツールを通らない変更(ここではcommit)も検出する。"""
    counter = tmp_path / "runs"
    event = setup_repo(tmp_path, f"echo x >> {counter}")
    gate.handle(event)
    git(tmp_path, "add", "main.ts")
    git(tmp_path, "commit", "-qm", "wip")
    gate.handle(event)
    assert counter.read_text().count("x") == 2


# --- 失敗時の挙動 ---

def test_失敗したらblockし検証済みにならない(tmp_path):
    event = setup_repo(tmp_path, "false")
    out = gate.handle(event)
    assert blocked(out)
    assert state.read_verified(str(tmp_path)) is None


def test_blockのreasonに失敗出力の末尾が入る(tmp_path):
    event = setup_repo(tmp_path, "echo FAILURE_DETAIL; exit 1")
    assert "FAILURE_DETAIL" in blocked(gate.handle(event))


def test_標準エラー出力もreasonに入る(tmp_path):
    event = setup_repo(tmp_path, "echo ONLY_ON_STDERR >&2; exit 1")
    assert "ONLY_ON_STDERR" in blocked(gate.handle(event))


def test_再入時の失敗はブロックせず警告で通す(tmp_path):
    event = setup_repo(tmp_path, "false")
    event["stop_hook_active"] = True
    out = gate.handle(event)
    assert blocked(out) is None
    assert "systemMessage" in out
    assert state.read_verified(str(tmp_path)) is None  # 次のターンで再ゲート


def test_実行できないコマンドはblockになる(tmp_path):
    event = setup_repo(tmp_path, "/no/such/binary-xyz")
    assert blocked(gate.handle(event))


def test_閉じない引用符のコマンドはblockになる(tmp_path):
    event = setup_repo(tmp_path, '"broken')
    assert blocked(gate.handle(event))


def test_フィードバックは英語(tmp_path):
    event = setup_repo(tmp_path, "false")
    assert blocked(gate.handle(event)).splitlines()[0].isascii()


# --- シェル機能 ---

def test_andでコマンドを連結できる(tmp_path):
    assert gate.handle(setup_repo(tmp_path, "true && true")) is None


def test_andの後段が失敗したらblockになる(tmp_path):
    assert blocked(gate.handle(setup_repo(tmp_path, "true && false")))


def test_パイプが使える(tmp_path):
    assert gate.handle(setup_repo(tmp_path, "echo hi | grep -q hi")) is None


def test_環境変数が展開される(tmp_path):
    assert gate.handle(setup_repo(tmp_path, 'test -n "$HOME"')) is None


def test_チルダが展開される(tmp_path):
    assert gate.handle(setup_repo(tmp_path, "test -d ~")) is None


def test_カレントディレクトリはリポジトリルート(tmp_path):
    assert gate.handle(setup_repo(tmp_path, "test -f main.ts")) is None


def test_サブディレクトリから起動してもゲートが掛かる(tmp_path):
    marker = tmp_path / "ran"
    event = setup_repo(tmp_path, f"touch {marker}")
    sub = tmp_path / "src" / "deep"
    sub.mkdir(parents=True)
    event["cwd"] = str(sub)
    assert gate.handle(event) is None
    assert marker.exists()


# --- タイムアウト ---

def test_タイムアウトはblockになる(tmp_path):
    event = setup_repo(tmp_path, "sleep 30", timeout_sec=1)
    assert "timed out" in blocked(gate.handle(event))


def test_タイムアウトで孫プロセスも止まる(tmp_path):
    """シェル経由なので、タイムアウト時はプロセスグループごと落とす必要がある。"""
    marker = tmp_path / "grandchild-survived"
    event = setup_repo(tmp_path, f"sh -c 'sleep 3; touch {marker}' & wait", timeout_sec=1)
    assert blocked(gate.handle(event))
    time.sleep(4)
    assert not marker.exists()


# --- worktree ---

def test_worktreeは本体と独立に検証状態を持つ(tmp_path):
    """worktreeでは cwd がそのworktreeのルートになる。記録もそこに置かれる。"""
    main = tmp_path / "main"
    main.mkdir()
    event = setup_repo(main, "true")
    git(main, "add", "-A")
    git(main, "commit", "-qm", "init")
    assert gate.handle(event) is None
    main_verified = state.read_verified(str(main))
    assert main_verified is not None

    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", str(wt), "-b", "feature")
    assert state.read_verified(str(wt)) is None  # 記録は引き継がない
    (wt / "main.ts").write_text("worktree edit\n", encoding="utf-8")
    assert gate.handle({"cwd": str(wt), "stop_hook_active": False}) is None

    assert state.read_verified(str(wt)) != main_verified
    assert state.read_verified(str(main)) == main_verified  # 本体の記録は不変


# --- 出力形式(D-2: additionalContext) ---

def test_失敗時はadditionalContextで返す(tmp_path):
    out = gate.handle(setup_repo(tmp_path, "false"))
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "Stop"
    assert "verification gate failed" in hso["additionalContext"]


def test_hook_event_nameが無ければStopとして扱う(tmp_path):
    event = setup_repo(tmp_path, "false")
    event.pop("hook_event_name", None)
    assert gate.handle(event)["hookSpecificOutput"]["hookEventName"] == "Stop"


# --- SubagentStop ---

def subagent(event: dict) -> dict:
    return {**event, "hook_event_name": "SubagentStop"}


def test_SubagentStopでもゲートが掛かる(tmp_path):
    marker = tmp_path / "ran"
    event = subagent(setup_repo(tmp_path, f"touch {marker}"))
    assert gate.handle(event) is None
    assert marker.exists()


def test_SubagentStopの失敗はhookEventNameがSubagentStop(tmp_path):
    out = gate.handle(subagent(setup_repo(tmp_path, "false")))
    assert out["hookSpecificOutput"]["hookEventName"] == "SubagentStop"


def test_SubagentStopの再入は警告で通す(tmp_path):
    event = subagent(setup_repo(tmp_path, "false"))
    event["stop_hook_active"] = True
    out = gate.handle(event)
    assert blocked(out) is None
    assert "systemMessage" in out


# --- TeammateIdle ---

def teammate(event: dict) -> dict:
    return {**event, "hook_event_name": "TeammateIdle", "teammate_name": "worker"}


def test_TeammateIdleでもゲートが掛かる(tmp_path):
    marker = tmp_path / "ran"
    event = teammate(setup_repo(tmp_path, f"touch {marker}"))
    assert gate.handle(event) is None
    assert marker.exists()


def test_TeammateIdleの失敗は終了コード2とstderrで返す(tmp_path):
    out = gate.handle(teammate(setup_repo(tmp_path, "echo NOPE; exit 1")))
    assert out["_exit_code"] == 2
    assert "NOPE" in out["_stderr"]
    assert "hookSpecificOutput" not in out  # teammate は JSON では止められない


def test_TeammateIdleは同じ状態を二度ブロックしない(tmp_path):
    """stop_hook_active が無いイベントなので、閉じ込めないためのガードが要る。"""
    event = teammate(setup_repo(tmp_path, "false"))
    assert blocked(gate.handle(event))
    out = gate.handle(event)
    assert blocked(out) is None
    assert "systemMessage" in out


def test_TeammateIdleは状態が変わればまたブロックする(tmp_path):
    event = teammate(setup_repo(tmp_path, "false"))
    assert blocked(gate.handle(event))
    (tmp_path / "main.ts").write_text("different\n", encoding="utf-8")
    assert blocked(gate.handle(event))


# --- gate.on による絞り込み ---

def test_onに含まれないイベントではゲートしない(tmp_path):
    marker = tmp_path / "ran"
    event = setup_repo(tmp_path, f"touch {marker}")
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(
        {"gate": {"command": f"touch {marker}", "watch": WATCH, "ignore": IGNORE,
                  "on": ["stop"]}}), encoding="utf-8")
    assert gate.handle(subagent(event)) is None
    assert not marker.exists()
    assert gate.handle(teammate(event)) is None
    assert not marker.exists()
    assert gate.handle(event) is None  # stop は掛かる
    assert marker.exists()


# --- スクリプトとしての終了コード ---

def test_スクリプトはTeammateIdleの失敗で終了コード2を返す(tmp_path):
    event = teammate(setup_repo(tmp_path, "echo NOPE >&2; exit 1"))
    script = Path(__file__).resolve().parent.parent / "hooks" / "gate.py"
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(event),
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "NOPE" in r.stderr


def test_スクリプトはStopの失敗でも終了コード0を返す(tmp_path):
    event = setup_repo(tmp_path, "exit 1")
    script = Path(__file__).resolve().parent.parent / "hooks" / "gate.py"
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(event),
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "Stop"


def test_ゲートが通ればブロック記録は無効になる(tmp_path):
    event = teammate(setup_repo(tmp_path, "false"))
    assert blocked(gate.handle(event))
    assert state.read_blocked(str(tmp_path))
    (tmp_path / ".loop-hooks.json").write_text(json.dumps(
        {"gate": {"command": "true", "watch": WATCH, "ignore": IGNORE}}), encoding="utf-8")
    assert gate.handle(event) is None
    assert not state.read_blocked(str(tmp_path))
