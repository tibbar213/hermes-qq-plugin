"""Sandbox isolation replaces automated review; host tasks require owner consent."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace, ModuleType
from unittest.mock import Mock
import pytest
from gateway.session_context import set_session_vars, clear_session_vars
from tools import approval, approval_context

@pytest.fixture
def plugin(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[3] / "tests/fixtures/qq-plugin"
    spec=importlib.util.spec_from_file_location("qq_task_approval_test",path/"__init__.py",submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    reviewer=ModuleType(spec.name+".review")
    reviewer.review_task=Mock(side_effect=AssertionError("Automated review must not be called"))
    reviewer.call_jev=Mock(side_effect=AssertionError("JEV must not be called"))
    reviewer.review_model=Mock(side_effect=AssertionError("Model review must not be called"))
    monkeypatch.setitem(sys.modules,reviewer.__name__,reviewer)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.tasks,"get_hermes_home",lambda:tmp_path)
    monkeypatch.setattr(module.tasks,"owner_id",lambda:"owner")
    monkeypatch.setattr(approval,"_yolo_active",lambda:False)
    monkeypatch.setattr(approval_context,"_get_approval_mode",lambda:"manual")
    tokens=set_session_vars(platform="qqbot",user_id="member",chat_id="group",chat_type="group",session_key="qq-approval-test")
    yield module
    reviewer.review_task.assert_not_called()
    reviewer.call_jev.assert_not_called()
    reviewer.review_model.assert_not_called()
    clear_session_vars(tokens)

@pytest.mark.parametrize("user,access,expected_asks",[
    ("member","read_only",0),("member","sandbox",0),("owner","server",1),
    ("member","server",1),
])
@pytest.mark.parametrize("legacy_flag",[None,False,True])
def test_authority_depends_only_on_access_and_verified_owner(plugin,monkeypatch,user,access,expected_asks,legacy_flag):
    set_session_vars(platform="qqbot",user_id=user,chat_id="group",chat_type="group")
    ask=Mock(return_value={"approved":True})
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    seen=[]
    def child(**kwargs):
        task=plugin.tasks.current()
        seen.append((task.access,task.approval_key))
        if access=="server":
            assert approval.is_session_yolo_enabled(task.approval_key)
        return '{"status":"completed"}'
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    args={"goal":"完成整个任务","context":"明确操作范围","access":access}
    if legacy_flag is not None:
        args["require_host"]=legacy_flag
    result=json.loads(plugin.tasks.handle(args,parent_agent=SimpleNamespace()))
    assert result["access"]==access and seen[0][0]==access
    assert ask.call_count==expected_asks
    assert result["approved_by"]==("owner" if expected_asks else "requester")
    if expected_asks:
        assert ask.call_args.args[1].startswith("QQ_TASK")
        assert "root" in ask.call_args.args[1] and "任务初审" not in ask.call_args.args[1]
    assert not approval.is_session_yolo_enabled(seen[0][1])

def test_denied_server_task_never_dispatches_or_downgrades(plugin,monkeypatch):
    monkeypatch.setattr(approval,"request_tool_approval",Mock(return_value={"approved":False}))
    spawn=Mock()
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    result=json.loads(plugin.tasks.handle({"goal":"操作服务器","context":"目标服务","access":"server"},parent_agent=SimpleNamespace()))
    assert "error" in result
    spawn.assert_not_called()

def test_approval_exception_never_dispatches(plugin,monkeypatch):
    monkeypatch.setattr(approval,"request_tool_approval",Mock(side_effect=RuntimeError("unavailable")))
    spawn=Mock()
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    with pytest.raises(RuntimeError):
        plugin.tasks.handle({"goal":"操作服务器","context":"目标服务","access":"server"},parent_agent=SimpleNamespace())
    spawn.assert_not_called()

@pytest.mark.parametrize("decision",["once","deny"])
def test_real_gateway_human_roundtrip_skips_smart_guardian(plugin,monkeypatch,decision):
    monkeypatch.setattr(approval,"_presence",lambda callback=None:(None,False,True,False))
    smart=Mock(side_effect=AssertionError("Smart guardian must not be called"))
    monkeypatch.setattr(approval,"_smart_verdict",smart)
    route="qq-approval-native-"+decision
    token=approval_context.set_current_session_key(route)
    notified=threading.Event()
    pending=[]
    def notify(data):
        pending.append(data)
        notified.set()
    approval.register_gateway_notify(route,notify)
    try:
        context=copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(context.run,plugin.tasks.authorize,"部署服务","仅指定服务","server","member","group","a"*24)
            try:
                assert notified.wait(timeout=5)
                assert not future.done()
                request=approval.get_pending_gateway_approval(route)
                assert request and "QQ_TASK" in request["description"]
                assert approval.resolve_gateway_approval(route,decision,request_id=request["request_id"])==1
            finally:
                approval.unregister_gateway_notify(route)
            assert future.result(timeout=5)==("owner" if decision=="once" else "")
        smart.assert_not_called()
    finally:
        approval.unregister_gateway_notify(route)
        approval_context.reset_current_session_key(token)

def test_global_bypass_cannot_grant_other_member_host_access(plugin,monkeypatch):
    monkeypatch.setattr(approval,"_yolo_active",lambda:True)
    ask=Mock()
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    assert plugin.tasks.authorize("任务","范围","server","member","group","b"*24)==""
    ask.assert_not_called()

