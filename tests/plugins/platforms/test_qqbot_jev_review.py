"""Whole-task Jev decisions auto-approve only APPROVE; all other results ask the owner."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from gateway.session_context import set_session_vars,clear_session_vars
from tools import approval

@pytest.fixture
def plugin(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[3] / "tests/fixtures/qq-plugin"
    spec=importlib.util.spec_from_file_location("qq_jev_test",path/"__init__.py",submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.tasks,"get_hermes_home",lambda:tmp_path)
    review=sys.modules[module.tasks.__package__+".review"]
    module._real_call_model=review.call_model
    monkeypatch.setattr(review,"call_model",Mock(return_value="ESCALATE"))
    tokens=set_session_vars(platform="qqbot",user_id="member-a",chat_id="group",chat_type="group",
        session_key="agent:main:qqbot:group:group:member-a")
    yield module
    clear_session_vars(tokens)

def test_auto_approved_task_skips_human_and_runs_whole_child(plugin,monkeypatch):
    reviewer=Mock(return_value={"verdict":"APPROVE","reviewer":"jev","summary":"passed"})
    ask=Mock()
    monkeypatch.setattr(plugin.tasks,"review_task",reviewer)
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    def run(**kwargs):
        assert plugin.tasks.current().active
        for command in ["touch a.txt","rm a.txt"]:
            assert approval.check_all_command_guards(command,"local")["approved"]
        return "{}"
    monkeypatch.setattr(plugin.tasks,"delegate_task",run)
    result=json.loads(plugin.tasks.handle({"goal":"创建并清理演示文件","context":"仅任务目录","access":"server"},parent_agent=SimpleNamespace()))
    assert result["approved_by"]=="jev"
    reviewer.assert_called_once()
    ask.assert_not_called()
    assert plugin.tasks.current() is None
    assert not approval.is_session_yolo_enabled("qq-task:"+result["task_id"])

@pytest.mark.parametrize("verdict",["DENY","ESCALATE"])
@pytest.mark.parametrize("allowed",[True,False])
def test_non_approve_verdict_is_always_sent_to_owner(plugin,monkeypatch,verdict,allowed):
    monkeypatch.setattr(plugin.tasks,"review_task",lambda *a:{"verdict":verdict,"summary":"review summary"})
    ask=Mock(return_value={"approved":allowed})
    spawn=Mock(return_value="{}")
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    result=json.loads(plugin.tasks.handle({"goal":"任务","context":"范围","access":"server"},parent_agent=SimpleNamespace()))
    ask.assert_called_once()
    assert "任务初审" in ask.call_args.args[1]
    assert spawn.call_count==int(allowed)
    assert (result.get("approved_by")=="owner") is allowed

@pytest.mark.parametrize("answer,verdict",[("APPROVE","APPROVE"),("DENY","DENY"),("ESCALATE","ESCALATE"),("","ESCALATE"),("unknown","ESCALATE")])
def test_reviewer_validates_response(plugin,monkeypatch,answer,verdict):
    review=sys.modules[plugin.tasks.__package__+".review"]
    monkeypatch.setattr(review,"load_config_readonly",lambda:{"plugins":{"entries":{"qq-group-tools":{"settings":{"jev_auto_approve":True}}}}})
    fake=Mock(return_value=answer)
    monkeypatch.setattr(review,"call_jev",fake)
    assert review.review_task("普通任务","仅任务目录")["verdict"]==verdict
    fake.assert_called_once()
    assert "普通任务" in fake.call_args.args[0]

def test_outage_and_oversize_tasks_fall_back_to_owner(plugin,monkeypatch):
    review=sys.modules[plugin.tasks.__package__+".review"]
    monkeypatch.setattr(review,"load_config_readonly",lambda:{"plugins":{"entries":{"qq-group-tools":{"settings":{"jev_auto_approve":True}}}}})
    fail=Mock(side_effect=TimeoutError())
    monkeypatch.setattr(review,"call_jev",fail)
    assert review.review_task("普通任务","范围")["verdict"]=="ESCALATE"
    assert "不可用" in review.review_task("普通任务","范围")["summary"]
    fail.reset_mock()
    assert review.review_task("x"*4000,"范围")["verdict"]=="ESCALATE"
    fail.assert_not_called()

@pytest.mark.parametrize("missing",[None,"reads_secrets","blast_radius"])
def test_typed_provider_answer_validation(plugin,monkeypatch,missing):
    path=Path(__file__).resolve().parents[3] / "tests/fixtures/jev-approvals"
    if not (path/"__init__.py").exists():
        pytest.skip("Optional JEV provider not redistributed")
    spec=importlib.util.spec_from_file_location("qq_jev_provider_test",path/"__init__.py",submodule_search_locations=[str(path)])
    backend=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=backend
    spec.loader.exec_module(backend)
    review=sys.modules[plugin.tasks.__package__+".review"]
    monkeypatch.setattr(review,"get_provider_profile",lambda name:SimpleNamespace(create_client=lambda **kw:backend.JevClient()))
    answers={"verdict":{"choice":"APPROVE","confidence":0.9},"blast_radius":{"score":0.1},
        "self_advocating":{"noul":0.01},"policy_allows":{"noul":0.8},
        "reads_secrets":{"noul":0.01},"sends_outbound":{"noul":0.01}}
    if missing:
        answers.pop(missing)
    monkeypatch.setattr(backend,"_post",lambda *a:{"answers":answers})
    monkeypatch.setattr(backend,"_record",lambda row:None)
    if missing:
        with pytest.raises(RuntimeError):
            review.call_jev('{"goal":"create file","scope":"task directory"}')
    else:
        assert review.call_jev('{"goal":"create file","scope":"task directory"}')=="APPROVE"

@pytest.mark.parametrize("configured",[True,False])
def test_jev_unavailable_or_unconfigured_uses_model(plugin,monkeypatch,configured):
    review=sys.modules[plugin.tasks.__package__+".review"]
    monkeypatch.setattr(review,"load_config_readonly",lambda:{"plugins":{"entries":{"qq-group-tools":{"settings":{"jev_auto_approve":configured}}}}})
    jev=Mock(side_effect=TimeoutError())
    model=Mock(return_value="APPROVE")
    monkeypatch.setattr(review,"call_jev",jev)
    monkeypatch.setattr(review,"call_model",model)
    ask=Mock()
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    monkeypatch.setattr(plugin.tasks,"delegate_task",lambda **kw:"{}")
    result=json.loads(plugin.tasks.handle({"goal":"创建文件","context":"仅任务目录","access":"server"},parent_agent=SimpleNamespace()))
    assert result["approved_by"]=="model"
    assert jev.call_count==int(configured)
    model.assert_called_once()
    ask.assert_not_called()

@pytest.mark.parametrize("decision",["DENY","ESCALATE"])
def test_real_jev_nonapproval_never_gets_overruled_by_model(plugin,monkeypatch,decision):
    review=sys.modules[plugin.tasks.__package__+".review"]
    monkeypatch.setattr(review,"load_config_readonly",lambda:{"plugins":{"entries":{"qq-group-tools":{"settings":{"jev_auto_approve":True}}}}})
    monkeypatch.setattr(review,"call_jev",lambda *a:decision)
    model=Mock(return_value="APPROVE")
    monkeypatch.setattr(review,"call_model",model)
    result=review.review_task("任务","范围")
    assert result["verdict"]==decision and result["reviewer"]=="jev"
    model.assert_not_called()

@pytest.mark.parametrize("failure",[TimeoutError(),"not-a-verdict","DENY","ESCALATE"])
def test_failed_or_nonapproving_fallback_asks_owner(plugin,monkeypatch,failure):
    review=sys.modules[plugin.tasks.__package__+".review"]
    monkeypatch.setattr(review,"load_config_readonly",dict)
    fake=Mock(side_effect=failure) if isinstance(failure,Exception) else Mock(return_value=failure)
    monkeypatch.setattr(review,"call_model",fake)
    ask=Mock(return_value={"approved":False})
    spawn=Mock()
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    result=json.loads(plugin.tasks.handle({"goal":"任务","context":"范围","access":"server"},parent_agent=SimpleNamespace()))
    assert "error" in result
    ask.assert_called_once()
    spawn.assert_not_called()

def test_fallback_calls_normal_model_not_jev_auxiliary(plugin,monkeypatch):
    review=sys.modules[plugin.tasks.__package__+".review"]
    monkeypatch.setattr(review,"call_model",plugin._real_call_model)
    from agent import auxiliary_client
    fake=Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="APPROVE"))]))
    monkeypatch.setattr(auxiliary_client,"call_llm",fake)
    assert review.call_model('{"goal":"ordinary file","scope":"task directory"}')=="APPROVE"
    assert fake.call_args.kwargs["task"] is None
