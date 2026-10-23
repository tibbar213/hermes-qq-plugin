import importlib.util
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[3]/"tests/fixtures/qq-plugin"
spec=importlib.util.spec_from_file_location("standalone_qq",ROOT/"__init__.py",submodule_search_locations=[str(ROOT)])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
@pytest.fixture(autouse=True)
def operator(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config_readonly",lambda:{"plugins":{"entries":{"qq-group-tools":{"settings":{"owner_id":"test-owner"}}}}})
