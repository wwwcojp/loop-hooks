"""テストが実利用者の状態ディレクトリを汚さないようにする。"""
import pytest


@pytest.fixture(autouse=True)
def 状態ディレクトリを隔離する(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path_factory.mktemp("plugin-data")))
