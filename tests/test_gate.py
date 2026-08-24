"""gate: 前回グリーンから変化していれば検証を実行し、失敗ならターンを終わらせない。"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks" / "stop"))
from lib import fingerprint, state  # noqa: E402
import gate  # noqa: E402

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
    assert "decision" not in out
    assert not marker.exists()


def test_壊れた設定は警告を出してゲートしない(tmp_path):
    (tmp_path / ".loop-hooks.json").write_text("{broken", encoding="utf-8")
    out = gate.handle({"cwd": str(tmp_path), "stop_hook_active": False})
    assert "systemMessage" in out
    assert "decision" not in out


def test_空のコマンドは警告になる(tmp_path):
    event = setup_repo(tmp_path, "")
    out = gate.handle(event)
    assert "systemMessage" in out
    assert "decision" not in out


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
    assert out["decision"] == "block"
    assert state.read_verified(str(tmp_path)) is None


def test_blockのreasonに失敗出力の末尾が入る(tmp_path):
    event = setup_repo(tmp_path, "echo FAILURE_DETAIL; exit 1")
    out = gate.handle(event)
    assert "FAILURE_DETAIL" in out["reason"]


def test_標準エラー出力もreasonに入る(tmp_path):
    event = setup_repo(tmp_path, "echo ONLY_ON_STDERR >&2; exit 1")
    assert "ONLY_ON_STDERR" in gate.handle(event)["reason"]


def test_再入時の失敗はブロックせず警告で通す(tmp_path):
    event = setup_repo(tmp_path, "false")
    event["stop_hook_active"] = True
    out = gate.handle(event)
    assert "decision" not in out
    assert "systemMessage" in out
    assert state.read_verified(str(tmp_path)) is None  # 次のターンで再ゲート


def test_実行できないコマンドはblockになる(tmp_path):
    event = setup_repo(tmp_path, "/no/such/binary-xyz")
    assert gate.handle(event)["decision"] == "block"


def test_閉じない引用符のコマンドはblockになる(tmp_path):
    event = setup_repo(tmp_path, '"broken')
    assert gate.handle(event)["decision"] == "block"


def test_reasonは英語(tmp_path):
    event = setup_repo(tmp_path, "false")
    reason = gate.handle(event)["reason"]
    assert reason.splitlines()[0].isascii()


# --- シェル機能 ---

def test_andでコマンドを連結できる(tmp_path):
    assert gate.handle(setup_repo(tmp_path, "true && true")) is None


def test_andの後段が失敗したらblockになる(tmp_path):
    assert gate.handle(setup_repo(tmp_path, "true && false"))["decision"] == "block"


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
    out = gate.handle(event)
    assert out["decision"] == "block"
    assert "timed out" in out["reason"]


def test_タイムアウトで孫プロセスも止まる(tmp_path):
    """シェル経由なので、タイムアウト時はプロセスグループごと落とす必要がある。"""
    marker = tmp_path / "grandchild-survived"
    event = setup_repo(tmp_path, f"sh -c 'sleep 3; touch {marker}' & wait", timeout_sec=1)
    assert gate.handle(event)["decision"] == "block"
    time.sleep(4)
    assert not marker.exists()
