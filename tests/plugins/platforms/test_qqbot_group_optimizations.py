"""Member/topic routing, private owner approval, conversational splitting."""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import AsyncMock
import pytest
from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource, build_session_key
from gateway.platforms.base import SendResult
from standalone_qq.platform.adapter import QQAdapter
from standalone_qq.platform.group_sessions import TopicStore,short_chunks,thread_id
from standalone_qq.platform.owner_approval import private_owner_authorized
OWNER="test-owner"
from standalone_qq.platform.keyboards import InteractionEvent,ApprovalRequest

@pytest.fixture
def plugin(monkeypatch):
    path=Path(__file__).resolve().parents[3] / "tests/fixtures/qq-plugin"
    spec=importlib.util.spec_from_file_location("qq_group_tools_test",path/"__init__.py",submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    env={"HERMES_SESSION_PLATFORM":"qqbot","HERMES_SESSION_USER_ID":"member-a",
         "HERMES_SESSION_CHAT_ID":"group","HERMES_SESSION_CHAT_TYPE":"group"}
    read=lambda key,default="":env.get(key,default)
    monkeypatch.setattr(module.policy,"get_session_env",read)
    monkeypatch.setattr(module.tasks,"get_session_env",read)
    return module,env

def make_adapter():
    return QQAdapter(PlatformConfig(enabled=True,extra={
        "app_id":"123","client_secret":"test-only","group_policy":"allowlist",
        "group_allow_from":["group"],"group_keywords":["大肥鱼"]}))

def test_members_and_named_topics_build_different_gateway_keys():
    def key(user,label):
        source=SessionSource(platform=Platform.QQBOT,chat_type="group",chat_id="group",
            user_id=user,thread_id=thread_id(label))
        return build_session_key(source,group_sessions_per_user=True,thread_sessions_per_user=True)
    assert len({key("a","默认"),key("b","默认"),key("a","网站"),key("a","服务器")})==4
    assert key("a","网站")==key("a","网站")

def test_quote_restores_own_topic_after_restart_but_not_another_members(tmp_path):
    path=tmp_path/"topics.db"
    store=TopicStore(path)
    store.select("group","a","网站")
    store.bind_incoming("input","group","a","网站")
    store.remember_reply("/v2/groups/group/messages",{"msg_id":"input"},{"id":"out","ext_info":{"ref_idx":"ref"}})
    store.select("group","a","服务器")
    reopened=TopicStore(path)
    quoted={"message_scene":{"ext":["ref_msg_idx=ref"]}}
    assert reopened.current("group","a",quoted)=="网站"
    assert reopened.current("group","a")=="服务器"
    assert reopened.current("group","b",quoted)=="默认"

@pytest.mark.asyncio
async def test_group_routes_member_without_manual_topic_switching():
    a=make_adapter()
    a._ingest=AsyncMock()
    await a._handle_group_message({"group_openid":"group"},"message","大肥鱼 做个网站",{"member_openid":"a"},"")
    assert a._ingest.call_args.kwargs["thread_id"] is None
    assert a._ingest.call_args.kwargs["user_id"]=="a"

@pytest.mark.parametrize("text",["第一句\n第二句\n第三句","好啊。现在开始！"])
def test_short_conversation_splits(text):
    chunks=short_chunks(text)
    assert 2<=len(chunks)<=3
    assert all(chunks)

@pytest.mark.parametrize("text",["代码\n```python\nprint(1)\n```",
    "这是链接\nhttps://example.com","结果\n| A | B |","介绍\n- 第一点\n- 第二点"])
def test_structured_responses_are_preserved(text):
    assert short_chunks(text)==[text]

@pytest.mark.asyncio
async def test_short_bubbles_keep_reply_anchor_for_sequences_and_topic_routing():
    a=make_adapter()
    a._ensure_connected=AsyncMock(return_value=True)
    a._send_chunk=AsyncMock(return_value=SendResult(success=True))
    result=await a.send("group","在\n说吧","input")
    assert result.success
    assert [call.args[1] for call in a._send_chunk.await_args_list]==["在","说吧"]
    assert all(call.args[2]=="input" for call in a._send_chunk.await_args_list)

def test_only_private_owner_can_approve_someone_elses_operation():
    a=make_adapter()
    key="agent:main:qqbot:group:group:qq-topic-x:member-a"
    assert private_owner_authorized(a,InteractionEvent(scene="c2c",user_openid=OWNER),key)
    assert not private_owner_authorized(a,InteractionEvent(scene="group",group_openid="group",group_member_openid=OWNER),key)
    assert not private_owner_authorized(a,InteractionEvent(scene="c2c",user_openid="member-a"),key)

@pytest.mark.asyncio
async def test_approval_is_sent_privately_with_only_once_or_deny(monkeypatch):
    from standalone_qq.platform import owner_approval
    monkeypatch.setattr(owner_approval,"get_pending_gateway_approval",lambda key:{"request_id":"request-1"})
    a=make_adapter()
    a.send_with_keyboard=AsyncMock(return_value=SendResult(success=True))
    await a.send_approval_request("group",ApprovalRequest(
        session_key="agent:main:qqbot:group:group:member-a",title="operation",command_preview="touch /tmp/demo"))
    call=a.send_with_keyboard.call_args
    assert call.args[0]==OWNER
    buttons=call.args[2].to_dict()["content"]["rows"][0]["buttons"]
    assert len(buttons)==2
    assert all(b["action"]["permission"]=={"type":2} for b in buttons)
    assert all(not b["action"]["data"].endswith(":allow-always") for b in buttons)

@pytest.mark.asyncio
async def test_old_owner_card_cannot_approve_new_operation(monkeypatch):
    from standalone_qq.platform import owner_approval
    a=make_adapter()
    a.send_with_keyboard=AsyncMock(return_value=SendResult(success=True))
    live={"request_id":"first"}
    monkeypatch.setattr(owner_approval,"get_pending_gateway_approval",lambda key:dict(live))
    resolved=[]
    monkeypatch.setattr(owner_approval,"resolve_gateway_approval",lambda *args,**kwargs:resolved.append((args,kwargs)) or 1)
    key="agent:main:qqbot:group:group:member-a"
    await a.send_approval_request("group",ApprovalRequest(session_key=key,title="test"))
    data=a.send_with_keyboard.call_args.args[2].to_dict()["content"]["rows"][0]["buttons"][0]["action"]["data"]
    event=InteractionEvent(scene="c2c",user_openid=OWNER,button_data=data)
    live["request_id"]="second"
    await owner_approval.handle_callback(a,event)
    assert resolved==[]
    await a.send_approval_request("group",ApprovalRequest(session_key=key,title="second"))
    newdata=a.send_with_keyboard.call_args.args[2].to_dict()["content"]["rows"][0]["buttons"][0]["action"]["data"]
    event.button_data=newdata
    await owner_approval.handle_callback(a,event)
    assert resolved[0][1]["request_id"]=="second"
    await owner_approval.handle_callback(a,event)
    assert len(resolved)==1

@pytest.mark.asyncio
@pytest.mark.parametrize("user,command,allowed",[
    ("member-a","/yolo",False),("member-a","/config",False),
    ("member-a","/stop",True),("member-a","你好",True),(OWNER,"/config",True)])
async def test_gateway_control_is_role_limited(user,command,allowed):
    from unittest.mock import patch
    from gateway.platforms.base import BasePlatformAdapter
    a=make_adapter()
    with patch.object(BasePlatformAdapter,"handle_message",new_callable=AsyncMock) as forward:
        await a._ingest({}, "input", command, [], "", chat_id="group",
            qq_chat_type="group", user_id=user, chat_type="group")
        assert forward.call_args.args[0].allow_gateway_control is allowed

def test_browser_session_is_scoped_to_conversation(plugin):
    module,env=plugin
    first=module.policy.isolate_browser("browser_exec",{})["args"]["session"]
    env["HERMES_SESSION_USER_ID"]="member-b"
    assert module.policy.isolate_browser("browser_exec",{})["args"]["session"]!=first
    env["HERMES_SESSION_USER_ID"]="member-a"
    env["HERMES_SESSION_THREAD_ID"]="new-topic"
    assert module.policy.isolate_browser("browser_exec",{})["args"]["session"]!=first
