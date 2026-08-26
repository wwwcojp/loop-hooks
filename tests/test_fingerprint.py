"""fingerprint: watch対象の現在状態をgitで観測して要約する。"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from lib import fingerprint  # noqa: E402

GATE = {"watch": ["*.ts", "package.json"], "ignore": [".loop/*", "*.md", "docs/*"]}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git",) + args, cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "main.ts").write_text("original\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def fp(root: Path) -> str:
    return fingerprint.compute(str(root), GATE)


# --- repo_root ---


def test_gitリポジトリでなければNone(tmp_path):
    assert fingerprint.repo_root(str(tmp_path)) is None


def test_サブディレクトリからでもリポジトリルートを返す(tmp_path):
    repo = make_repo(tmp_path)
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert Path(fingerprint.repo_root(str(sub))).resolve() == repo.resolve()


# --- is_watched ---


def test_is_watched_watchに一致すれば対象():
    assert fingerprint.is_watched("server/main.ts", GATE) is True
    assert fingerprint.is_watched("package.json", GATE) is True


def test_is_watched_ignoreがwatchより優先():
    assert fingerprint.is_watched("docs/notes.ts", GATE) is False
    assert fingerprint.is_watched("README.md", GATE) is False


def test_is_watched_どちらにも無ければ対象外():
    assert fingerprint.is_watched("scripts/dev.sh", GATE) is False


# --- compute ---


def test_変更が無ければ同じ値を返す(tmp_path):
    repo = make_repo(tmp_path)
    assert fp(repo) == fp(repo)


def test_watch対象を編集すると値が変わる(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "main.ts").write_text("edited\n", encoding="utf-8")
    assert fp(repo) != before


def test_編集を戻すと元の値に戻る(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "main.ts").write_text("edited\n", encoding="utf-8")
    assert fp(repo) != before
    (repo / "main.ts").write_text("original\n", encoding="utf-8")
    assert fp(repo) == before


def test_同じファイルの別の内容は別の値になる(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "main.ts").write_text("one\n", encoding="utf-8")
    one = fp(repo)
    (repo / "main.ts").write_text("two\n", encoding="utf-8")
    assert fp(repo) != one


def test_watch対象外の編集では値が変わらない(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "notes.txt").write_text("scratch\n", encoding="utf-8")
    assert fp(repo) == before


def test_ignoreに一致する編集では値が変わらない(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.ts").write_text("ignored\n", encoding="utf-8")
    assert fp(repo) == before


def test_新規のwatch対象ファイルを検出する(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "added.ts").write_text("new\n", encoding="utf-8")
    assert fp(repo) != before


def test_watch対象の削除を検出する(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "main.ts").unlink()
    assert fp(repo) != before


def test_ステージ済みの変更も検出する(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "main.ts").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "-A")
    assert fp(repo) != before


def test_コミットするとHEADが変わるので値も変わる(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "main.ts").write_text("committed\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change")
    assert fp(repo) != before


def test_ブランチ切替による内容差を検出する(tmp_path):
    """作業ツリーがきれいでも、HEADが違えば別の値になる。"""
    repo = make_repo(tmp_path)
    clean_on_main = fp(repo)
    git(repo, "checkout", "-qb", "other")
    (repo / "main.ts").write_text("other branch\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "other")
    assert fp(repo) != clean_on_main


def test_空白を含むパスでも壊れない(tmp_path):
    repo = make_repo(tmp_path)
    before = fp(repo)
    (repo / "my file.ts").write_text("spaced\n", encoding="utf-8")
    assert fp(repo) != before


def test_コミットの無いリポジトリでも動く(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    before = fingerprint.compute(str(tmp_path), GATE)
    assert isinstance(before, str)
    (tmp_path / "main.ts").write_text("first\n", encoding="utf-8")
    assert fingerprint.compute(str(tmp_path), GATE) != before
