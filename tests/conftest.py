"""テストが実利用者の状態ディレクトリを汚さないようにする。

hypothesis のプロファイルもここで選ぶ。
"""

import os

import pytest
from hypothesis import settings

# spec §2.2: quick/CI は default(25 例)、verify.py all の properties ステージは
# thorough(300 例)、mutmut が各変異でテストを回すときは MUTANT_UNDER_TEST が付くので
# mutation(5 例)に自動で絞る。
settings.register_profile("default", max_examples=25, deadline=None)
settings.register_profile("thorough", max_examples=300, deadline=None)
settings.register_profile("mutation", max_examples=5, deadline=None)
settings.load_profile(
    "mutation"
    if os.environ.get("MUTANT_UNDER_TEST")
    else os.environ.get("HYPOTHESIS_PROFILE", "default")
)

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
