"""テストが実利用者の状態ディレクトリを汚さないようにする。

hypothesis のプロファイルもここで選ぶ。
"""

import os

import pytest
from hypothesis import settings

# spec §2.2: quick/CI は default(25 例)、verify.py all の properties ステージは
# thorough(300 例)、mutmut が各変異でテストを回すときは MUTANT_UNDER_TEST が付くので
# mutation(5 例)に自動で絞る(下の pytest_runtest_setup が実行時に差し替える)。
#
# ここでの読み込みは HYPOTHESIS_PROFILE だけを見る。MUTANT_UNDER_TEST では選ばない: mutmut は
# 1 つの永続プロセスを使い回し、この conftest は最初の import 時にしか評価されない。その最初の
# import が mutmut 自身の内部フェーズ(stats 収集など、MUTANT_UNDER_TEST="stats" のように
# 実際の変異キーではない値が立っている)の最中に起きると、"mutation" が永続プロセスの既定
# プロファイルとして固定されてしまい、以降の stats/clean 実行や実行時に生成した hypothesis
# 関数まで既定 25 例のはずが 5 例になる(mutmut 初回実行で判明、tests/test_packaging.py::
# test_MUTANT_UNDER_TESTがあればhypothesisの例数が実行時に5へ絞られる が検出した)。
# 変異ごとの絞り込みは pytest_runtest_setup が MUTANT_UNDER_TEST を都度読んで行うので、
# ここは常に HYPOTHESIS_PROFILE(既定 "default")だけに従えばよい。
settings.register_profile("default", max_examples=25, deadline=None)
settings.register_profile("thorough", max_examples=300, deadline=None)
settings.register_profile("mutation", max_examples=5, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

MUTATION_MAX_EXAMPLES = 5


def pytest_runtest_setup(item):
    """mutmut は fork した同一プロセスで pytest を再実行するので、変異実行時の例数はここで絞る。

    conftest は import 時にキャッシュされ、`settings.load_profile` は再評価されない。
    hypothesis は `@given` 済み関数の `_hypothesis_internal_use_settings` を呼出時に読むので、
    MUTANT_UNDER_TEST が付いていればそこを差し替える(私用属性だが、下のテストが検出する)。
    """
    if not os.environ.get("MUTANT_UNDER_TEST"):
        return
    fn = getattr(item, "obj", None)
    fn = getattr(fn, "__func__", fn)  # bound method → 元の関数(属性代入は関数側にしかできない)
    current = getattr(fn, "_hypothesis_internal_use_settings", None)
    if current is not None:
        fn._hypothesis_internal_use_settings = settings(current, max_examples=MUTATION_MAX_EXAMPLES)


@pytest.fixture(autouse=True)
def 状態ディレクトリを隔離する(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path_factory.mktemp("plugin-data")))
