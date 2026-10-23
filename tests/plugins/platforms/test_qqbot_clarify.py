"""QQ native clarification returns a selection to the original waiting agent."""
from unittest.mock import AsyncMock
import pytest
from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from standalone_qq.platform.adapter import QQAdapter
from standalone_qq.platform.keyboards import InteractionEvent
from standalone_qq.platform import clarify
from tools import clarify_gateway as pending

KEY = "agent:main:qqbot:group:test-group"

@pytest.fixture
def adapter(monkeypatch):
    a=QQAdapter(PlatformConfig(enabled=True,extra={
        "app_id":"123","client_secret":"test-only","group_policy":"allowlist",
        "group_allow_from":["test-group"],"dm_policy":"allowlist","allow_from":["requester"]}))
    a._chat_type_map["test-group"]="group"
    a._last_msg_id["test-group"]="source-message"
    a.send_with_keyboard=AsyncMock(return_value=SendResult(success=True,message_id="card"))
    a.send=AsyncMock(return_value=SendResult(success=True))
    monkeypatch.setattr(clarify,"get_session_env",lambda *args:"requester")
    yield a
    pending.clear_session(KEY)

async def show(a, choices=None):
    choices=choices or ["个人主页 (Recommended)","工具页面"]
    entry=pending.register("testcard",KEY,"做哪种网站",choices)
    result=await a.send_clarify("test-group","做哪种网站",choices,"testcard",KEY)
    assert result.success
    return entry

def click(user="requester",group="test-group",token="1"):
    return InteractionEvent(id="click",scene="group",group_openid=group,
        group_member_openid=user,button_data="qqcl:testcard:"+token)

@pytest.mark.asyncio
async def test_card_renders_full_options_and_binds_requester(adapter):
    await show(adapter,["一个超过十个字符的完整选项说明","另一个方向"])
    args=adapter.send_with_keyboard.call_args
    body=args.args[1]
    keyboard=args.args[2].to_dict()
    assert "一个超过十个字符的完整选项说明" in body
    assert args.kwargs["reply_to"]=="source-message"
    buttons=[row["buttons"][0] for row in keyboard["content"]["rows"]]
    assert buttons[-1]["action"]["data"]=="qqcl:testcard:other"
    assert all(len(b["render_data"]["label"])<=10 for b in buttons)
    assert all(b["action"]["permission"]=={"type":2} for b in buttons)

@pytest.mark.asyncio
async def test_callback_resumes_pending_entry_with_actual_option(adapter):
    entry=await show(adapter)
    assert await clarify.handle_callback(adapter,click())
    assert entry.event.is_set()
    assert entry.response=="工具页面"
    assert "testcard" not in adapter._clarify_cards

@pytest.mark.asyncio
@pytest.mark.parametrize("user,group,token",[
    ("other-user","test-group","0"),("requester","other-group","0"),
    ("requester","test-group","99"),("requester","test-group","-1"),
])
async def test_invalid_or_other_member_click_cannot_answer(adapter,user,group,token):
    entry=await show(adapter)
    await clarify.handle_callback(adapter,click(user,group,token))
    assert not entry.event.is_set()

@pytest.mark.asyncio
async def test_other_accepts_typed_answer_in_same_pending_task(adapter):
    entry=await show(adapter)
    await clarify.handle_callback(adapter,click(token="other"))
    assert entry.awaiting_text
    assert pending.attempt_text_response_for_session(KEY,"做一个记账小工具")==pending.TEXT_RESOLVED
    assert entry.response=="做一个记账小工具"

@pytest.mark.asyncio
async def test_expired_or_replayed_card_cannot_answer_new_entry(adapter):
    old=await show(adapter)
    pending.clear_session(KEY)
    fresh=pending.register("testcard",KEY,"新的任务",["A","B"])
    await clarify.handle_callback(adapter,click())
    assert not fresh.event.is_set()
    assert old is not fresh

@pytest.mark.asyncio
async def test_interaction_uses_existing_ack_dispatch(adapter):
    entry=await show(adapter)
    adapter._acknowledge_interaction=AsyncMock()
    await adapter._on_interaction({
        "id":"click-event","chat_type":1,"group_openid":"test-group",
        "group_member_openid":"requester","data":{"type":11,"resolved":{
            "button_id":"cl_0","button_data":"qqcl:testcard:0"}}})
    adapter._acknowledge_interaction.assert_awaited_once_with("click-event")
    assert entry.response=="个人主页 (Recommended)"

@pytest.mark.asyncio
async def test_requester_can_type_without_mention_while_prompt_waits(adapter):
    await show(adapter)
    adapter._handle_group_message=AsyncMock()
    await adapter._handle_full_group_message(
        {"group_openid":"test-group"},"answer","2",{"member_openid":"requester"},"")
    adapter._handle_group_message.assert_awaited_once()

@pytest.mark.asyncio
async def test_other_member_remains_routable_in_its_own_session(adapter):
    from types import SimpleNamespace
    from unittest.mock import patch
    from gateway.platforms.base import BasePlatformAdapter
    await show(adapter)
    event=SimpleNamespace(metadata={},internal=False,message_id="other-message",source=SimpleNamespace(
        chat_id="test-group",user_id="another-member"))
    with patch.object(BasePlatformAdapter,"handle_message",new_callable=AsyncMock) as forward:
        await adapter.handle_message(event)
        forward.assert_awaited_once_with(event)
