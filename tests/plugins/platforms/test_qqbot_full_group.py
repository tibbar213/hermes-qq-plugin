"""Full QQ group events must reach the existing group pipeline only when mentioning this bot."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from standalone_qq.platform.adapter import QQAdapter


def make_adapter():
    return QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "123",
        "client_secret": "test-only",
        "group_policy": "allowlist",
        "group_allow_from": ["allowed-group"],
        "group_keywords": ["大肥鱼"],
    }))


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type,content,mentions", [
    ("GROUP_AT_MESSAGE_CREATE", "hello", []),
    ("GROUP_MESSAGE_CREATE", "<@!bot-member> hello", [{"member_openid": "bot-member", "is_you": True}]),
    ("GROUP_MESSAGE_CREATE", "<@123> hello", []),
    ("GROUP_MESSAGE_CREATE", "<@!123> hello", []),
])
async def test_addressed_group_events_reach_existing_pipeline(event_type, content, mentions):
    adapter = make_adapter()
    received = asyncio.Event()
    calls = []

    async def ingest(*args, **kwargs):
        calls.append((args, kwargs))
        received.set()

    adapter._ingest = ingest
    adapter._dispatch_payload({"op": 0, "t": event_type, "s": 1, "d": {
        "id": "message",
        "content": content,
        "mentions": mentions,
        "group_openid": "allowed-group",
        "author": {"member_openid": "human"},
    }})
    await asyncio.wait_for(received.wait(), timeout=2)
    args, kwargs = calls[0]
    assert args[2] == "hello"
    assert kwargs["chat_id"] == "allowed-group"
    assert kwargs["user_id"] == "human"
    assert kwargs["chat_type"] == "group"


@pytest.mark.asyncio
@pytest.mark.parametrize("group,content,mentions,author", [
    ("allowed-group", "ordinary conversation", [], {"member_openid": "human"}),
    ("allowed-group", "<@other> hello", [{"id": "other", "is_you": False}], {"member_openid": "human"}),
    ("different-group", "<@123> hello", [], {"member_openid": "human"}),
    ("allowed-group", "<@123> hello", [], {"member_openid": "robot", "bot": True}),
])
async def test_unaddressed_unauthorized_or_robot_messages_are_ignored(group, content, mentions, author):
    adapter = make_adapter()
    adapter._ingest = AsyncMock()
    await adapter._on_message("GROUP_MESSAGE_CREATE", {
        "id": "message",
        "content": content,
        "mentions": mentions,
        "group_openid": group,
        "author": author,
    })
    adapter._ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyword_invokes_group_agent():
    adapter = make_adapter()
    adapter._ingest = AsyncMock()
    await adapter._on_message("GROUP_MESSAGE_CREATE", {
        "id": "keyword-message",
        "content": "大肥鱼，查看服务器时间",
        "group_openid": "allowed-group",
        "author": {"member_openid": "human"},
    })
    adapter._ingest.assert_awaited_once()
    assert adapter._ingest.call_args.args[2] == "大肥鱼，查看服务器时间"


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_payload", [
    {"message_scene": {"ext": ["ref_msg_idx=sent-reference=="]}},
    {"message_type": 103, "msg_elements": [{"msg_idx": "sent-reference==", "content": "bot answer"}]},
])
async def test_reply_to_bot_survives_adapter_restart(tmp_path, reference_payload):
    from standalone_qq.platform.group_mentions import GroupReplyIndex

    index_path = tmp_path / "reply-index.sqlite3"
    first = make_adapter()
    first._group_reply_index = GroupReplyIndex(index_path)
    first._api_request = AsyncMock(return_value={
        "id": "bot-message", "ext_info": {"ref_idx": "sent-reference=="},
    })
    await first._post_message("/v2/groups/allowed-group/messages", {"content": "bot answer"})

    restarted = make_adapter()
    restarted._group_reply_index = GroupReplyIndex(index_path)
    restarted._ingest = AsyncMock()
    await restarted._on_message("GROUP_MESSAGE_CREATE", {
        **reference_payload, "id": "reply", "content": "接着说",
        "group_openid": "allowed-group", "author": {"member_openid": "human"},
    })
    restarted._ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_reply_to_another_user_does_not_trigger(tmp_path):
    from standalone_qq.platform.group_mentions import GroupReplyIndex

    adapter = make_adapter()
    adapter._group_reply_index = GroupReplyIndex(tmp_path / "reply-index.sqlite3")
    adapter._group_reply_index.remember(
        "/v2/groups/allowed-group/messages",
        {"id": "bot-message", "ext_info": {"ref_idx": "bot-ref"}},
    )
    adapter._ingest = AsyncMock()
    await adapter._on_message("GROUP_MESSAGE_CREATE", {
        "id": "reply", "content": "接着说", "message_scene": {"ext": ["ref_msg_idx=other-person"]},
        "group_openid": "allowed-group", "author": {"member_openid": "human"},
    })
    adapter._ingest.assert_not_awaited()
