"""QQ notices use one owner-only private path; gateway fallback follows the unmodified Hermes contract."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.run_notifications import GatewayNotificationsMixin
from gateway.session import SessionSource
from standalone_qq.platform.adapter import QQAdapter
OWNER="test-owner"
from agent.turn_failure_copy import FAILED_TURN_NOTICE

@pytest.fixture
def adapter():
    a=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture","notice_delivery":"private"}))
    a._ensure_connected=AsyncMock(return_value=True)
    a._send_chunk=AsyncMock(return_value=SendResult(success=True,message_id="private"))
    a._last_msg_id[OWNER]="owner-anchor"
    return a

def runner(a):
    return SimpleNamespace(config=None,_delivery_adapter_for=lambda source:a,
        _thread_metadata_for_source=lambda source:None)

def source(user="member"):
    return SessionSource(platform=Platform.QQBOT,chat_id="group",chat_type="group",user_id=user)

@pytest.mark.asyncio
async def test_generic_notice_uses_owner_and_private_anchor_only(adapter):
    result=await adapter.send_private_notice("group","member","后台维护完成",reply_to="group-anchor")
    assert result.success
    call=adapter._send_chunk.call_args
    assert call.args[0]==OWNER and call.args[2]=="owner-anchor"
    assert "后台维护完成" in call.args[1] and "出了点问题" not in call.args[1]

@pytest.mark.asyncio
async def test_gateway_uses_native_fallback_when_private_send_fails(adapter):
    adapter._send_chunk.return_value=SendResult(success=False,error="forbidden")
    adapter.send=AsyncMock()
    await GatewayNotificationsMixin._deliver_platform_notice(runner(adapter),source(),"private operational details")
    adapter.send.assert_awaited_once()
    assert all(call.args[0]==OWNER for call in adapter._send_chunk.call_args_list)

@pytest.mark.asyncio
async def test_gateway_uses_native_fallback_when_private_send_raises(adapter):
    adapter.send_private_notice=AsyncMock(side_effect=RuntimeError("network failed"))
    adapter.send=AsyncMock()
    await GatewayNotificationsMixin._deliver_platform_notice(runner(adapter),source(),"private operational details")
    adapter.send.assert_awaited_once()

@pytest.mark.asyncio
async def test_missing_requester_follows_native_public_notice_contract(adapter):
    adapter.send=AsyncMock()
    await GatewayNotificationsMixin._deliver_platform_notice(runner(adapter),source(None),"system notice")
    adapter.send.assert_awaited_once()
    adapter._send_chunk.assert_not_awaited()

@pytest.mark.asyncio
async def test_existing_failure_surface_reuses_public_private_notice_contract(adapter):
    original=adapter.send_private_notice
    adapter.send_private_notice=AsyncMock(wraps=original)
    result=await adapter.send("group",FAILED_TURN_NOTICE)
    assert result.success
    adapter.send_private_notice.assert_awaited_once()
    assert "没处理成功" in adapter._send_chunk.call_args.args[1]

@pytest.mark.asyncio
async def test_group_anchor_never_used_even_when_owner_is_requester(adapter):
    await adapter.send_private_notice("group",OWNER,"notice",reply_to="group-anchor")
    assert adapter._send_chunk.call_args.args[2]=="owner-anchor"

@pytest.mark.asyncio
async def test_ordinary_group_conversation_is_unchanged(adapter):
    await adapter.send("group","我在呢",reply_to="group-anchor")
    assert adapter._send_chunk.call_args.args==("group","我在呢","group-anchor")

@pytest.mark.asyncio
async def test_other_adapters_keep_legacy_public_fallback():
    a=SimpleNamespace(config=PlatformConfig(enabled=True,extra={"notice_delivery":"private"}),
        send_private_notice=AsyncMock(return_value=SendResult(success=False,error="unsupported")),
        send=AsyncMock(return_value=SendResult(success=True)))
    await GatewayNotificationsMixin._deliver_platform_notice(runner(a),source(),"legacy")
    a.send.assert_awaited_once()
