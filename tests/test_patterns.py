"""patterns: watch / ignore の gitignore 風マッチ(0.11.0)。

表は https://git-scm.com/docs/gitignore の規則から。対象は「ファイルのパス列」なので、
パターンが途中のディレクトリに一致したら配下すべてに一致する(git がディレクトリを除外すると
配下を見ないのと同じ)。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import patterns  # noqa: E402


@pytest.mark.parametrize(
    ("pattern", "rel", "expected"),
    [
        # スラッシュ無し = 任意の深さの basename
        ("*.md", "README.md", True),
        ("*.md", "docs/a/b.md", True),
        ("*.py", "a.pyc", False),
        ("node_modules", "node_modules", True),
        ("node_modules", "a/b/node_modules/x.js", True),  # ディレクトリ一致の伝播
        # スラッシュあり = ルート基準
        ("docs/*", "docs/a.md", True),
        ("docs/*", "docs/a/b.md", True),  # docs/a に一致し配下へ伝播(0.10.0 と同じ結果)
        ("docs/*", "src/docs/a.md", False),
        ("/docs", "docs/x", True),
        ("/docs", "a/docs/x", False),
        # * は / を跨がない
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/a/b.py", False),
        ("a*c", "a/c", False),
        ("*", "a/b", True),  # "*" は a に一致し配下へ伝播
        # **
        ("src/**/*.py", "src/a/b.py", True),
        ("src/**/*.py", "src/a.py", True),
        ("**/foo", "a/b/foo", True),
        ("**/foo", "foo", True),
        ("abc/**", "abc/x/y", True),
        ("abc/**", "abc", False),
        ("a/**/b", "a/b", True),
        ("a/**/b", "a/x/y/b", True),
        ("a**b", "axyb", True),  # セグメント途中の ** は * と同じ
        ("a**b", "a/b", False),
        ("a/**b", "a/xb", True),  # /** の直後に文字が続けば末尾扱いしない
        ("a/**b", "a/x/b", False),
        ("x**/y", "x/y", True),  # git と同じく **/ は直前を問わず跨ぐ
        ("x**/y", "x/a/y", True),
        ("a**/b", "a/b", True),
        ("x**", "x/a/b", True),
        ("[ab]**/y", "ay", False),  # ** より前に glob 文字があると跨がない(git と同じ)
        ("[ab]**/y", "a/x/y", False),
        ("*/b**/c", "a/b/x/c", False),
        ("[ab]**", "a/q", True),  # [ab]** は a に一致し配下へ伝播
        ("**/**/x", "a/b/x", True),
        ("x**y", "x/y", False),
        ("***/y", "a/b/y", True),  # 3 連以上の * も 1 つの ** として扱う(git と同じ)
        ("a/***/b", "a/b", True),
        ("x***", "x/a/b", True),
        ("****/*", "a", True),
        # 末尾 / = ディレクトリ指定(配下が必要)
        ("node_modules/", "a/b/node_modules/x.js", True),
        ("node_modules/", "node_modules", False),
        ("dist/", "dist/x", True),
        # ? と文字クラス
        ("?.py", "a.py", True),
        ("?.py", "ab.py", False),
        ("[ab].py", "a.py", True),
        ("[!ab].py", "a.py", False),
        ("[!ab].py", "c.py", True),
        ("a[!x]bc", "a/bc", False),  # 否定クラスは / に一致しない(git と同じ)
        ("[!]", "[!]", True),  # 閉じない [! はリテラル
        ("[!]]", "a", True),  # ] を含む否定クラス
        ("[z-a]", "[z-a]", True),  # 不正な範囲は re.error → リテラル
        ("[z-a]", "a", False),
        ("[[a]", "[[a]", True),  # FutureWarning(nested set)もリテラル扱い
        ("a***b", "axb", True),
        ("a***b", "a/b", False),
        ("[", "[", True),  # 閉じない [ はリテラル
        ("[", "x", False),
        # エスケープ・空
        ("\\!x", "!x", True),
        ("a\\*b", "a*b", True),  # \\ で glob 文字をリテラルに
        ("a\\*b", "axb", False),
        ("a\\?", "a?", True),
        ("a\\?", "ab", False),
        ("a\\", "a\\", True),  # 末尾の \\ 単体はリテラル
        ("*tsconfig*.json", "tsconfig.build.json", True),
        ("", "a", False),
        ("/", "a", False),
    ],
)
def test_単一パターンの一致(pattern, rel, expected):
    assert patterns.matches(rel, [pattern]) is expected


def test_否定は後勝ち():
    assert patterns.matches("a.md", ["*.md", "!a.md"]) is False
    assert patterns.matches("b.md", ["*.md", "!a.md"]) is True
    assert patterns.matches("a.md", ["!a.md", "*.md"]) is True
    assert patterns.matches("a.md", ["!a.md"]) is False


def test_連続する星でバックトラックが爆発しない():
    import time

    t = time.perf_counter()
    assert patterns.matches("a" + "x" * 40, ["a" + "*" * 16 + "b"]) is False
    assert patterns.matches("a/b/c/d/e/f/zx", ["**/" * 20 + "zz"]) is False
    assert time.perf_counter() - t < 0.1


def test_警告を出す文字クラスでも例外にも警告にもならない():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert patterns.matches("[[a]", ["[[a]"]) is True


def test_空のリストは何にも一致しない():
    assert patterns.matches("a", []) is False


def test_不正なパターンでも例外を出さない():
    for bad in ("[", "[a", "**", "!", "", "\\", "a[", "[]", "[!]"):
        assert isinstance(patterns.matches("a/b", [bad]), bool), bad


def test_既定のignoreは依存ディレクトリとドキュメントを除きソースを残す():
    from hooks.lib import config

    ignore = config.GATE_DEFAULTS["ignore"]
    for rel in ("a/b/node_modules/x.js", "dist/x", "docs/a.md", ".venv/lib/x.py", "target/debug/x"):
        assert patterns.matches(rel, ignore) is True, rel
    for rel in ("src/a.py", "README.py", "main.ts", "dist"):
        assert patterns.matches(rel, ignore) is False, rel


def test_コンパイル結果はキャッシュされる():
    key = ("*.md", "!a.md")
    assert patterns._compiled(key) is patterns._compiled(key)
