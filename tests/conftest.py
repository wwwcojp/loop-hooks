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


@pytest.fixture(autouse=True)
def 状態ディレクトリを隔離する(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path_factory.mktemp("plugin-data")))
