"""Failure notices must not reach a QQ chat; only real content is sent."""
import asyncio

import pytest

from gateway.config import PlatformConfig
from standalone_qq.platform.adapter import QQAdapter, _looks_like_error_notice
OWNER="test-owner"


def make_adapter(silent_errors=True):
    return QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "123",
        "client_secret": "test-only",
        "silent_errors": silent_errors,
    }))


@pytest.mark.parametrize("text", [
    "⚠️ 出了点问题，我无法完成这条回复。使用 /retry 重试。",
    "❌ The background task couldn't start because no AI model sign-in is configured.",
    "⏱️ AI 模型服务正在限流。请稍等片刻，然后使用 /retry。",
    "Error code: 429 - {'error': {'message': '您已达到请求数限制：1分钟内最多请求10次'}}",
    "HTTP 500: upstream unavailable",
    "Traceback (most recent call last):\n  File \"x.py\"",
    "您已达到请求数限制：1分钟内最多请求10次",
])
def test_error_notices_are_recognised(text):
    assert _looks_like_error_notice(text)


@pytest.mark.parametrize("text", [
    "今天疯狂星期四，V我50吗？",
    "我用 /retry 重试了一次就成功了，挺快的",
    "",
    "   ",
    ("⚠️ 这个符号在正文里，但这条是长篇正常回复，" * 40),
])
def test_normal_content_is_not_suppressed(text):
    assert not _looks_like_error_notice(text)


def test_send_redirects_error_notice_only_to_owner():
    adapter = make_adapter()
    adapter._ensure_connected = lambda: asyncio.sleep(0, result=True)
    sent = []

    async def fake_send_chunk(chat_id, content, reply_to=None):
        sent.append(chat_id)
        from gateway.platforms.base import SendResult
        return SendResult(success=True)

    adapter._send_chunk = fake_send_chunk

    result = asyncio.run(adapter.send("group-1", "⚠️ 出了点问题，我无法完成这条回复。"))
    assert result.success
    assert sent == [OWNER]


def test_send_passes_normal_content_through():
    adapter = make_adapter()
    adapter._ensure_connected = lambda: asyncio.sleep(0, result=True)
    sent = []

    async def fake_send_chunk(chat_id, content, reply_to=None):
        sent.append(content)
        from gateway.platforms.base import SendResult
        return SendResult(success=True)

    adapter._send_chunk = fake_send_chunk

    result = asyncio.run(adapter.send("group-1", "在的，你说"))
    assert result.success
    assert sent == ["在的，你说"]


def test_opt_out_sends_error_notices():
    adapter = make_adapter(silent_errors=False)
    adapter._ensure_connected = lambda: asyncio.sleep(0, result=True)
    sent = []

    async def fake_send_chunk(chat_id, content, reply_to=None):
        sent.append(content)
        from gateway.platforms.base import SendResult
        return SendResult(success=True)

    adapter._send_chunk = fake_send_chunk

    asyncio.run(adapter.send("group-1", "⚠️ 出了点问题"))
    assert sent == ["⚠️ 出了点问题"]
