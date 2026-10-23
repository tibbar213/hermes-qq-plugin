import importlib.util as _qq_import
import sys as _qq_sys
from pathlib import Path as _QQPath
_qqroot=_QQPath(__file__).resolve().parents[2]/"tests/fixtures/qq-plugin"
_qqspec=_qq_import.spec_from_file_location("standalone_web",_qqroot/"__init__.py",submodule_search_locations=[str(_qqroot)])
_qqmod=_qq_import.module_from_spec(_qqspec);_qq_sys.modules[_qqspec.name]=_qqmod;_qqspec.loader.exec_module(_qqmod)
