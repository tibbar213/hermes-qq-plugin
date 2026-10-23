"""QQ native command confirmation cards preserve requester, expiry and single-use semantics."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time
import pytest
from agent.i18n import t
from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult, EphemeralReply
from gateway.run_busy import GatewayBusySessionMixin
from standalone_qq.platform.adapter import QQAdapter
from standalone_qq.platform.keyboards import InteractionEvent
OWNER="test-owner"
from tools import slash_confirm

@pytest.fixture
def adapter():
    a=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture",
        "group_policy":"allowlist","group_allow_from":["group"]}))
    a._guess_chat_type=lambda chat:"group" if chat=="group" else "c2c"
    a._is_dm_intake_allowed=lambda user:True
    a.send_with_keyboard=AsyncMock(return_value=SendResult(success=True))
    a.send=AsyncMock(return_value=SendResult(success=True))
    yield a
    for card in getattr(a,"_slash_confirm_cards",{}).values():
        slash_confirm.clear(card.session)


async def card(a,user="member",chat="group",confirm="1"):
    kind="group" if chat=="group" else "dm"
    key=f"agent:main:qqbot:{kind}:{chat}"+(":"+user if kind=="group" else "")
    handler=AsyncMock(return_value=EphemeralReply(t("gateway.reset.header_default")))
    slash_confirm.register(key,confirm,"new",handler)
    result=await a.send_slash_confirm(chat,"Confirm /new","English fallback",key,confirm,{"message_id":"input"})
    assert result.success
    payload=a.send_with_keyboard.call_args.args[2].to_dict()
    buttons=payload["content"]["rows"][0]["buttons"]
    return key,handler,buttons


def click(data,user="member",chat="group"):
    return InteractionEvent(scene="group" if chat=="group" else "c2c",
        group_openid=chat if chat=="group" else "",
        group_member_openid=user if chat=="group" else "",
        user_openid=user if chat!="group" else "",button_data=data)


@pytest.mark.asyncio
async def test_gateway_uses_buttons_instead_of_english_fallback(adapter):
    key="agent:main:qqbot:group:group:member"
    runner=SimpleNamespace(_session_key_for_source=lambda s:key,
        _delivery_adapter_for=lambda s:adapter,_thread_metadata_for_source=lambda *a:{"message_id":"input"},
        _reply_anchor_for_event=lambda e:"input")
    handler=AsyncMock(return_value="done")
    result=await GatewayBusySessionMixin._request_slash_confirm(runner,event=SimpleNamespace(source=SimpleNamespace(chat_id="group")),
        command="new",title="Confirm /new",message="English fallback",handler=handler)
    assert result is None
    args=adapter.send_with_keyboard.call_args
    assert "新对话" in args.args[1] and "English" not in args.args[1]
    assert args.kwargs["reply_to"]=="input"
    for button in args.args[2].to_dict()["content"]["rows"][0]["buttons"]:
        assert button["action"]["permission"]["type"]==2


@pytest.mark.asyncio
async def test_native_confirmation_executes_once_and_replies_in_chinese(adapter):
    key,handler,buttons=await card(adapter)
    event=click(buttons[0]["action"]["data"])
    await adapter._default_interaction_dispatch(event)
    await adapter._default_interaction_dispatch(event)
    handler.assert_awaited_once_with("once")
    assert slash_confirm.get_pending(key) is None
    assert adapter.send.call_args.args[1]=="新对话开好了，说吧"


@pytest.mark.asyncio
async def test_other_member_or_group_cannot_reset_requester_session(adapter):
    _,handler,buttons=await card(adapter)
    data=buttons[0]["action"]["data"]
    await adapter._default_interaction_dispatch(click(data,user="other"))
    await adapter._default_interaction_dispatch(click(data,chat="different"))
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_card_cannot_confirm_replacement_even_with_reused_id(adapter):
    key,handler,buttons=await card(adapter)
    replacement=AsyncMock(return_value="new")
    slash_confirm.register(key,"1","new",replacement)
    await adapter._default_interaction_dispatch(click(buttons[0]["action"]["data"]))
    handler.assert_not_awaited()
    replacement.assert_not_awaited()
    slash_confirm.clear(key)


@pytest.mark.asyncio
async def test_expired_card_does_not_reset_session(adapter):
    key,handler,buttons=await card(adapter)
    with slash_confirm._lock:
        slash_confirm._pending[key]["created_at"]=time.time()-301
    await adapter._default_interaction_dispatch(click(buttons[0]["action"]["data"]))
    handler.assert_not_awaited()
    slash_confirm.clear(key)


@pytest.mark.asyncio
async def test_owner_mobile_dm_can_confirm_and_nonowner_cannot_disable_prompts(adapter):
    _,handler,buttons=await card(adapter,user=OWNER,chat=OWNER)
    assert len(buttons)==3
    await adapter._default_interaction_dispatch(click(buttons[0]["action"]["data"],user=OWNER,chat=OWNER))
    handler.assert_awaited_once_with("once")
    _,member_handler,member_buttons=await card(adapter)
    assert len(member_buttons)==2
    forged=member_buttons[0]["action"]["data"].rsplit(":",1)[0]+":always"
    await adapter._default_interaction_dispatch(click(forged))
    member_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_preserves_native_cancel_semantics(adapter):
    _key,handler,buttons=await card(adapter)
    handler.return_value=t("gateway.confirm.cancelled",command="new")
    await adapter._default_interaction_dispatch(click(buttons[-1]["action"]["data"]))
    handler.assert_awaited_once_with("cancel")
    assert adapter.send.call_args.args[1]=="取消了，继续刚才的聊天"


@pytest.mark.asyncio
async def test_failed_send_leaves_no_live_button_mapping(adapter):
    adapter.send_with_keyboard.return_value=SendResult(success=False,error="fixture")
    key="agent:main:qqbot:group:group:member"
    slash_confirm.register(key,"1","new",AsyncMock())
    result=await adapter.send_slash_confirm("group","","",key,"1")
    assert not result.success and not adapter._slash_confirm_cards
    slash_confirm.clear(key)
