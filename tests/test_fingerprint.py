"""fingerprint: watch対象の現在状態をgitで観測して要約する。"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import fingerprint  # noqa: E402

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


# --- _git ---


def test_gitはタイムアウトつきで呼ばれる(monkeypatch, tmp_path):
    """スパイク: timeout=None の変異が生き残っていた。git が固まるとゲートがフックの timeout まで
    止まる。"""
    seen: dict[str, object] = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(fingerprint.subprocess, "run", fake_run)
    fingerprint.repo_root(str(tmp_path))
    assert seen.get("timeout") == fingerprint.GIT_TIMEOUT_SEC
    assert seen.get("capture_output") is True


def test_gitがタイムアウトすればNone(monkeypatch, tmp_path):
    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(fingerprint.subprocess, "run", boom)
    assert fingerprint.repo_root(str(tmp_path)) is None
    assert fingerprint.compute(str(tmp_path), {"watch": ["*"], "ignore": []}) is None


# --- repo_root ---


def test_gitリポジトリでなければNone(tmp_path):
    assert fingerprint.repo_root(str(tmp_path)) is None


def test_サブディレクトリからでもリポジトリルートを返す(tmp_path):
    repo = make_repo(tmp_path)
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert Path(fingerprint.repo_root(str(sub))).resolve() == repo.resolve()


def test_repo_rootは不正なUTF8のパスでも例外を出さない(tmp_path):
    """スパイク: decode の errors ハンドラ名(surrogateescape)や引数省略の変異が生き残っていた。
    パス名が不正なUTF8バイト列だと strict decode や不正なハンドラ名は例外を出す。"""
    weird_name = os.fsdecode(b"repo-\xffdir")
    weird_root = tmp_path / weird_name
    weird_root.mkdir()
    make_repo(weird_root)
    assert fingerprint.repo_root(str(weird_root)) is not None


# --- is_watched ---


def test_is_watched_watchに一致すれば対象():
    assert fingerprint.is_watched("server/main.ts", GATE) is True
    assert fingerprint.is_watched("package.json", GATE) is True


def test_is_watched_ignoreがwatchより優先():
    assert fingerprint.is_watched("docs/notes.ts", GATE) is False
    assert fingerprint.is_watched("README.md", GATE) is False


def test_is_watched_どちらにも無ければ対象外():
    assert fingerprint.is_watched("scripts/dev.sh", GATE) is False


# --- _changed_paths ---


def test__changed_pathsはporcelainとuallとzを指定する(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []

    def fake_git(cwd, *args):
        calls.append(args)
        return b""

    monkeypatch.setattr(fingerprint, "_git", fake_git)
    fingerprint._changed_paths(str(tmp_path))
    assert calls == [("status", "--porcelain=v1", "-uall", "-z")]


def test_リネームでは旧パスを読み飛ばし後続の変更も取りこぼさない(tmp_path):
    """スパイク: リネーム行の後にインデックスを進める処理(i += 1 など)の変異が生き残っていた。
    旧パスを読み飛ばし損なう・多く飛ばしすぎるどちらも、後続の実変更を欠落/汚染させる。"""
    repo = make_repo(tmp_path)
    git(repo, "config", "diff.renames", "true")
    (repo / "zzz.ts").write_text("z1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add zzz")
    git(repo, "mv", "main.ts", "renamed.ts")
    (repo / "zzz.ts").write_text("z2\n", encoding="utf-8")
    git(repo, "add", "-A")
    paths = fingerprint._changed_paths(str(repo))
    assert paths == [b"renamed.ts", b"zzz.ts"]


def test_1文字のパスも取りこぼさない(tmp_path):
    """スパイク: 短いエントリを弾く長さ判定(len(entry) < 4)の境界がずれる変異が生き残っていた。"""
    repo = make_repo(tmp_path)
    (repo / "a").write_text("x", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add a")
    (repo / "a").write_text("y", encoding="utf-8")
    assert fingerprint._changed_paths(str(repo)) == [b"a"]


def test_リネームが最後のエントリでも例外を出さない(monkeypatch, tmp_path):
    """旧パスのフィールドが続かない(出力が途切れた)場合でも読み飛ばしで落ちない。"""
    monkeypatch.setattr(fingerprint, "_git", lambda cwd, *args: b"R  new.ts\0")
    assert fingerprint._changed_paths(str(tmp_path)) == [b"new.ts"]


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


def test_フィンガープリントの形式は固定でHEADプレフィックスと区切りを使う(tmp_path):
    """スパイク: b"HEAD:" プレフィックスや b"\\n"・b":" 区切りを定数へすり替える変異が
    生き残っていた。比較用テストは相対比較しかしないので、生の形式を直接検算して固定する。"""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "main.ts").write_text("x\n", encoding="utf-8")
    result = fingerprint.compute(str(tmp_path), GATE)
    content_hash = hashlib.sha256(b"x\n").hexdigest().encode()
    expected = hashlib.sha256(b"HEAD:\nmain.ts:" + content_hash).hexdigest()
    assert result == expected


def test_削除済みwatch対象はハイフンとしてハッシュ化される(tmp_path):
    """スパイク: 削除・読み取り不可時のプレースホルダー b"-" をすり替える変異が生き残っていた。"""
    repo = make_repo(tmp_path)
    head = fingerprint._git(str(repo), "rev-parse", "HEAD").strip()
    (repo / "main.ts").unlink()
    result = fp(repo)
    expected = hashlib.sha256(b"HEAD:" + head + b"\nmain.ts:-").hexdigest()
    assert result == expected


def test_computeは不正なUTF8のパス名でも例外を出さない(tmp_path):
    """スパイク: compute内のdecodeでもerrorsハンドラ名・引数省略の変異が生き残っていた。"""
    repo = make_repo(tmp_path)
    weird_name = os.fsdecode(b"bad-\xff.ts")
    (repo / weird_name).write_bytes(b"x")
    assert fingerprint.compute(str(repo), GATE) is not None
