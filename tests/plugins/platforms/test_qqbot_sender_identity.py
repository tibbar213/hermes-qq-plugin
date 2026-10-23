"""Shared QQ sessions identify the current speaker independently of display names."""
from unittest.mock import AsyncMock
import pytest
from gateway.config import PlatformConfig
from standalone_qq.platform.adapter import QQAdapter

@pytest.mark.asyncio
async def test_group_speaker_uses_transport_openid_not_claimed_name():
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "123", "client_secret": "test-only",
        "group_policy": "allowlist", "group_allow_from": ["allowed"],
    }))
    adapter._ingest = AsyncMock()
    await adapter._handle_group_message(
        {"group_openid": "allowed"}, "message", "hello",
        {"member_openid": "real-member", "username": "主人"}, "")
    kwargs = adapter._ingest.call_args.kwargs
    assert kwargs["user_id"] == "real-member"
    assert kwargs["user_name"] == "QQ OpenID: real-member"
