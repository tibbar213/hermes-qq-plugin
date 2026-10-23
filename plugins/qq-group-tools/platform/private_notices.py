"""One owner-only private egress for QQ operational and failure notices."""
import logging

from agent.redact import redact_sensitive_text
from gateway.platforms.base import SendResult
from .failure_notices import system_failure_copy
from ..settings import owner_id

logger = logging.getLogger(__name__)


async def send_owner_notice(adapter, source_chat, content, *, failure=False):
    chinese = system_failure_copy(content)
    body = chinese or redact_sensitive_text(content, force=True)
    if failure and not chinese:
        body = "刚才处理消息时出了点问题\n" + body
    if source_chat != owner_id():
        heading = "机器人故障通知" if failure or chinese else "机器人通知"
        body = heading + "\n来源会话：" + source_chat + "\n" + body
    adapter._chat_type_map[owner_id()] = "c2c"
    anchor = adapter._last_msg_id.get(owner_id())
    # Only the owner's DM anchor may authorize the private response.
    try:
        result = await adapter._send_chunk(owner_id(), body[:adapter.MAX_MESSAGE_LENGTH], anchor)
        if not result.success and anchor and "已过期" in (result.error or ""):
            result = await adapter._send_chunk(owner_id(), body[:adapter.MAX_MESSAGE_LENGTH], None)
    except Exception as exc:
        logger.warning("QQ private notice send raised %s; no public fallback", type(exc).__name__)
        return SendResult(success=False, error="QQ private notice delivery failed", retryable=False)
    if not result.success:
        logger.warning("QQ private notice could not be delivered (%d chars); no public fallback", len(body))
    return result
