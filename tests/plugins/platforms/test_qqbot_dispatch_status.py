"""Admission failures must not become successful tasks."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from gateway.session_context import set_session_vars, clear_session_vars
from standalone_qq.platform.task_workspaces import TaskStore
from tools import approval

@pytest.fixture
def plugin(tmp_path, monkeypatch):
    path=Path(__file__).resolve().parents[3] / "tests/fixtures/qq-plugin"
    spec=importlib.util.spec_from_file_location("qq_dispatch_status_test",path/"__init__.py",submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.tasks,"get_hermes_home",lambda:tmp_path)
    monkeypatch.setattr(module.tasks,"acknowledge",lambda *a:None)
    tokens=set_session_vars(platform="qqbot",user_id="member-a",chat_id="group")
    yield module,tmp_path
    clear_session_vars(tokens)

@pytest.mark.parametrize("cause",["capacity","no-delivery","native-error"])
def test_real_dispatch_refusal_is_failed_and_revokes_grant(plugin,monkeypatch,cause):
    module,home=plugin
    monkeypatch.setattr(module.tasks,"authorize",lambda *a,**kw:"model")
    monkeypatch.setattr("tools.async_delegation.active_count",lambda:999 if cause=="capacity" else 0)
    monkeypatch.setattr("gateway.session_context.async_delivery_supported",lambda:cause!="no-delivery")
    spawn=Mock(return_value=json.dumps({"error":"native dispatcher rejected this task"}))
    monkeypatch.setattr(module.tasks,"delegate_task",spawn)
    result=json.loads(module.tasks.handle({"goal":"test","context":"only workspace","access":"server"},parent_agent=SimpleNamespace()))
    row=TaskStore(home).get(result["task_id"],"group","member-a")
    assert row["status"]=="failed"
    assert result["status"]=="failed" and result["error"]
    assert not approval.is_session_yolo_enabled("qq-task:"+result["task_id"])
    assert spawn.call_count==int(cause=="native-error")
    # Failure remains eligible for a user-requested follow-up, rather than being locked as running.
    TaskStore(home).begin(result["task_id"],"group","member-a","retry","",resume=True)

@pytest.mark.parametrize("payload,expected",[
    ({"status":"completed"},"completed"),
    ({"results":[{"status":"completed","summary":"ok"}]},"completed"),
    ({"results":[{"status":"failed","error":"not done"}]},"failed"),
    ({"results":[{"status":"interrupted"}]},"interrupted"),
    ({},"failed"),
])
def test_inline_fallback_uses_actual_result(plugin,monkeypatch,payload,expected):
    module,home=plugin
    monkeypatch.setattr(module.tasks,"execute",lambda *a:json.dumps(payload))
    result=json.loads(module.tasks.handle({"goal":"test","access":"read_only"},parent_agent=SimpleNamespace()))
    assert TaskStore(home).get(result["task_id"],"group","member-a")["status"]==expected
