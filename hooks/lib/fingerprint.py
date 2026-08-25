"""watch対象ファイルの現在状態を、gitで観測してフィンガープリントに要約する。

「前回グリーンだった時点から変わったか」を、編集の経路(Edit/Write/Bash/git操作/
フォーマッタ)に依存せず判定するための土台。
"""
import fnmatch
import hashlib
import subprocess
from pathlib import Path

GIT_TIMEOUT_SEC = 30


def _git(cwd: str, *args: str) -> bytes | None:
    """gitの標準出力を返す。失敗したら None。"""
    try:
        r = subprocess.run(("git",) + args, cwd=cwd, capture_output=True,
                           timeout=GIT_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def repo_root(cwd: str | None) -> str | None:
    """cwdを含むgitリポジトリのルート。gitリポジトリでなければ None。"""
    if not cwd:
        return None
    out = _git(cwd, "rev-parse", "--show-toplevel")
    return out.decode("utf-8", "surrogateescape").strip() if out is not None else None


def head_file(root: str, rel: str) -> bytes | None:
    """HEAD にコミットされている rel の内容。HEAD に無い(未追跡・コミット無し)なら None。"""
    return _git(root, "show", f"HEAD:{rel}")


def is_watched(rel: str, gate_cfg: dict) -> bool:
    """リポジトリ相対パスがゲート対象か。ignore は watch より優先。"""
    if any(fnmatch.fnmatch(rel, p) for p in gate_cfg["ignore"]):
        return False
    return any(fnmatch.fnmatch(rel, p) for p in gate_cfg["watch"])


def _changed_paths(root: str) -> list[bytes] | None:
    """HEAD と一致しないパス(ステージ済み・未ステージ・未追跡)。"""
    out = _git(root, "status", "--porcelain=v1", "-uall", "-z")
    if out is None:
        return None
    fields = out.split(b"\0")
    paths: list[bytes] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if b"R" in status or b"C" in status:
            i += 1  # リネーム/コピー元のパスは次のフィールドに続く
    return paths


def _content_hash(path: Path) -> bytes:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest().encode()
    except OSError:
        return b"-"  # 削除済み、あるいは読めない


def compute(root: str, gate_cfg: dict) -> str | None:
    """watch対象の現在状態のフィンガープリント。gitリポジトリでなければ None。"""
    paths = _changed_paths(root)
    if paths is None:
        return None
    head = _git(root, "rev-parse", "HEAD") or b""  # コミットが無ければ空
    parts = [b"HEAD:" + head.strip()]
    for path in sorted(paths):
        rel = path.decode("utf-8", "surrogateescape")
        if is_watched(rel, gate_cfg):
            parts.append(path + b":" + _content_hash(Path(root) / rel))
    return hashlib.sha256(b"\n".join(parts)).hexdigest()
