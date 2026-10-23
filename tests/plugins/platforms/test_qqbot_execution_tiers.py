"""QQ execution tiers must bind native environments and never mint host authority."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from gateway.session_context import set_session_vars, clear_session_vars
from tools import approval
from tools.terminal_scope import get_terminal_scope, set_terminal_scope, reset_terminal_scope

@pytest.fixture
def plugin(tmp_path, monkeypatch):
    path=Path(__file__).resolve().parents[3] / "tests/fixtures/qq-plugin"
    spec=importlib.util.spec_from_file_location("qq_tiers_test",path/"__init__.py",submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.tasks,"get_hermes_home",lambda:tmp_path)
    tokens=set_session_vars(platform="qqbot",user_id="member",chat_id="group",chat_type="group")
    scope=set_terminal_scope(get_terminal_scope())
    yield module
    clear_session_vars(tokens)
    reset_terminal_scope(scope)

@pytest.mark.parametrize("owner,access,review,human,expected,asks",[
    (True,"server","ESCALATE",False,"server",0),
    (True,"sandbox","ESCALATE",False,"sandbox",0),
    (False,"sandbox","ESCALATE",False,"sandbox",0),
    (False,"read_only","ESCALATE",False,"read_only",0),
    (False,"server","APPROVE",False,"sandbox",0),
    (False,"server","ESCALATE",True,"server",1),
    (False,"server","ESCALATE",False,None,1),
])
def test_task_authority_matrix(plugin,monkeypatch,owner,access,review,human,expected,asks):
    if owner:
        set_session_vars(platform="qqbot",user_id=plugin.tasks.owner_id(),chat_id="group",chat_type="group")
    monkeypatch.setattr(plugin.tasks,"review_task",lambda *a:{"verdict":review,"summary":"review","reviewer":"jev"})
    ask=Mock(return_value={"approved":human})
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    observed=[]
    def child(**kwargs):
        observed.append(plugin.tasks.current().access)
        return '{"status":"completed"}'
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    result=json.loads(plugin.tasks.handle({"goal":"测试任务","context":"既定范围","access":access},parent_agent=SimpleNamespace()))
    assert ask.call_count==asks
    assert observed==([expected] if expected else [])
    if expected:
        assert result["access"]==expected
    else:
        assert "error" in result

def test_missing_native_key_fails_closed(plugin):
    task=plugin.tasks.Task("a","member","group","sandbox","/task")
    token=plugin.tasks.CURRENT.set(task)
    try:
        assert plugin.policy.guard("terminal",{"command":"id"})["action"]=="block"
    finally:
        plugin.tasks.CURRENT.reset(token)
        if task.sandbox_slot:
            plugin.policy.execution.SANDBOX_SLOT.release()

@pytest.mark.parametrize("tool",["execute_code","browser_exec","skill_view","mcp__other__write","send_message"])
def test_sandbox_rejects_host_side_effect_tools(plugin,tool):
    task=plugin.tasks.Task("a","member","group","sandbox","/task")
    token=plugin.tasks.CURRENT.set(task)
    try:
        assert plugin.policy.guard(tool,{})["action"]=="block"
    finally:
        plugin.tasks.CURRENT.reset(token)

def test_scope_binding_and_file_path_mapping(plugin):
    task=plugin.tasks.Task("a","member","group","sandbox","/task",approval_key="qq-task:"+"a"*24)
    token=plugin.tasks.CURRENT.set(task)
    try:
        decision=plugin.policy.guard("write_file",{"path":"/task/demo.txt","content":"x"},task_id="sa-tier")
        assert decision["args"]["path"]=="/workspace/demo.txt"
        assert get_terminal_scope()["TERMINAL_ENV"]=="qq_sandbox"
        assert "sa-tier" in task.env_keys
    finally:
        plugin.tasks.finish_task(task,"failed")
        plugin.tasks.CURRENT.reset(token)

def test_authorization_failure_is_a_block_not_a_hook_exception(plugin,monkeypatch):
    task=plugin.tasks.Task("a","member","group","sandbox","/task")
    token=plugin.tasks.CURRENT.set(task)
    monkeypatch.setattr(plugin.policy.execution,"acquire_sandbox",Mock(side_effect=RuntimeError("transport unavailable")))
    try:
        assert plugin.policy.guard("terminal",{"command":"id"},task_id="sa-tier")["action"]=="block"
    finally:
        plugin.tasks.CURRENT.reset(token)

def test_cleanup_failure_still_finishes_task_and_releases_host_lock(plugin,monkeypatch):
    task=plugin.tasks.Task("a","member","group","server","/task",approval_key="qq-task:a")
    task.lock_held=True
    plugin.tasks.SERVER_LOCK.acquire()
    finished=Mock()
    monkeypatch.setattr(plugin.tasks,"TaskStore",lambda *a:SimpleNamespace(finish=finished))
    monkeypatch.setattr(plugin.policy.execution,"cleanup",Mock(side_effect=RuntimeError("cleanup failed")))
    with pytest.raises(RuntimeError):
        plugin.tasks.finish_task(task,"failed")
    finished.assert_called_once_with("a","failed")
    assert not task.active and not plugin.tasks.SERVER_LOCK.locked()


def test_explicit_host_task_still_needs_owner_even_after_jev_approval(plugin,monkeypatch):
    monkeypatch.setattr(plugin.tasks,"review_task",lambda *a:{"verdict":"APPROVE","summary":"allowed","reviewer":"jev"})
    ask=Mock(return_value={"approved":True})
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    captured=[]
    def child(**kwargs):
        captured.append(plugin.tasks.current().access)
        return '{"status":"completed"}'
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    result=json.loads(plugin.tasks.handle({"goal":"部署服务","context":"既定服务","access":"server","require_host":True},parent_agent=SimpleNamespace()))
    assert result["access"]=="server" and captured==["server"]
    ask.assert_called_once()
    assert "root" in ask.call_args.args[1]

def test_execution_middleware_binds_scope_after_copied_hook_then_restores(plugin):
    from contextvars import copy_context
    from tools import approval_context
    task=plugin.tasks.Task("a","member","group","sandbox","/task",approval_key="qq-task:"+"a"*24)
    token=plugin.tasks.CURRENT.set(task)
    original_scope=get_terminal_scope()
    original_approval=approval_context.get_current_session_key(default="")
    seen=[]
    try:
        copy_context().run(plugin.policy.guard,"terminal",{"command":"id"},task_id="sa-real")
        assert get_terminal_scope()==original_scope
        def actual(args):
            seen.append((get_terminal_scope()["TERMINAL_ENV"],approval_context.get_current_session_key(default="")))
            return "completed"
        assert plugin.policy.execution.wrap_tool("terminal",{"command":"id"},actual,task_id="sa-real")=="completed"
        assert seen==[("qq_sandbox",task.approval_key)]
        assert get_terminal_scope()==original_scope
        assert approval_context.get_current_session_key(default="")==original_approval
    finally:
        plugin.tasks.finish_task(task,"completed")
        plugin.tasks.CURRENT.reset(token)

def test_execution_middleware_rejects_blocked_tool_without_running_callback(plugin):
    task=plugin.tasks.Task("a","member","group","sandbox","/task")
    token=plugin.tasks.CURRENT.set(task)
    called=Mock()
    try:
        result=plugin.policy.execution.wrap_tool("execute_code",{},called,task_id="sa-real")
        assert "沙箱任务" in result
        called.assert_not_called()
    finally:
        plugin.tasks.CURRENT.reset(token)
