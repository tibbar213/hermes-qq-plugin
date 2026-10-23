"""QQ localizes fixed system boundaries while preserving requested English content."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from agent.turn_failure_copy import FAILED_TURN_NOTICE, PARTIAL_FAILED_TURN_NOTICE, AUTOMATION_INTERRUPTED_NOTICE
from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from standalone_qq.platform.adapter import QQAdapter
OWNER="test-owner"

@pytest.mark.parametrize("notice,meaning", [
    (FAILED_TURN_NOTICE,"没处理成功"), (PARTIAL_FAILED_TURN_NOTICE,"可能已经"),
    (AUTOMATION_INTERRUPTED_NOTICE,"没有自动重跑")])
def test_fixed_system_notices_are_chinese_even_on_recovery(notice,meaning):
    adapter=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture"}))
    adapter._ensure_connected=AsyncMock(return_value=True)
    sent=[]
    async def send(chat,content,reply_to=None):
        sent.append((chat,content,reply_to))
        return SendResult(success=True)
    adapter._send_chunk=send
    result=asyncio.run(adapter.send("group",notice,reply_to="anchor"))
    assert result.success and meaning in "".join(row[1] for row in sent)
    assert all(row[0]==OWNER for row in sent)
    assert notice not in "".join(row[1] for row in sent)
    assert all(row[2]!="anchor" for row in sent)


def test_actual_english_content_is_preserved():
    adapter=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture"}))
    adapter._ensure_connected=AsyncMock(return_value=True)
    adapter._send_chunk=AsyncMock(return_value=SendResult(success=True))
    text="Translate this: Your request was not processed."
    asyncio.run(adapter.send("group",text))
    assert adapter._send_chunk.call_args.args[1]==text


def test_expired_qq_anchor_is_not_retried():
    adapter=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture"}))
    sender=AsyncMock(side_effect=RuntimeError("QQ Bot API error [400]: 回复消息msg_id已过期"))
    adapter._text_sender=lambda *a,**k:sender
    adapter._guess_chat_type=lambda chat:"group"
    result=asyncio.run(adapter._send_chunk("group","内容","expired"))
    assert not result.success and result.retryable is False
    sender.assert_awaited_once()


def test_failed_private_delivery_is_not_falsely_acknowledged_or_sent_to_group():
    adapter=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture"}))
    adapter._ensure_connected=AsyncMock(return_value=True)
    adapter._send_chunk=AsyncMock(return_value=SendResult(success=False,error="forbidden"))
    result=asyncio.run(adapter.send("group",FAILED_TURN_NOTICE))
    assert not result.success
    assert all(call.args[0]==OWNER for call in adapter._send_chunk.call_args_list)


def test_owner_anchor_is_used_and_expired_anchor_falls_back_only_in_private():
    adapter=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture"}))
    adapter._ensure_connected=AsyncMock(return_value=True)
    adapter._last_msg_id[OWNER]="owner-private-anchor"
    adapter._send_chunk=AsyncMock(side_effect=[
        SendResult(success=False,error="回复消息msg_id已过期"),SendResult(success=True)])
    result=asyncio.run(adapter.send("group",FAILED_TURN_NOTICE,reply_to="group-anchor"))
    assert result.success
    calls=adapter._send_chunk.call_args_list
    assert [c.args[0] for c in calls]==[OWNER,OWNER]
    assert [c.args[2] for c in calls]==["owner-private-anchor",None]


@pytest.mark.parametrize("text",["⚠️ 明天有暴雨，记得带伞","❌ 这个方案不适合你"])
def test_normal_warning_is_not_redirected(text):
    adapter=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture"}))
    adapter._ensure_connected=AsyncMock(return_value=True)
    adapter._send_chunk=AsyncMock(return_value=SendResult(success=True))
    asyncio.run(adapter.send("group",text))
    assert all(call.args[0]=="group" for call in adapter._send_chunk.call_args_list)
