"""Keep technical failures out of shared chats; notify the verified bot owner privately."""
from agent.turn_failure_copy import FAILED_TURN_NOTICE, PARTIAL_FAILED_TURN_NOTICE, AUTOMATION_INTERRUPTED_NOTICE
from ..settings import owner_id

_NOTICE_COPY = {
    FAILED_TURN_NOTICE: "刚才那条没处理成功，需要的话再发我一次",
    PARTIAL_FAILED_TURN_NOTICE: "刚才的处理没完成，可能已经做了一部分，继续前先确认结果",
    AUTOMATION_INTERRUPTED_NOTICE: "刚才的后台处理断了，没有自动重跑，可能已经做了一部分，先确认结果",
}


def system_failure_copy(text):
    return _NOTICE_COPY.get((text or "").strip())


async def notify_owner(adapter, source_chat, content):
    """Use the same private egress as generic gateway operational notices."""
    return await adapter.send_private_notice(
        source_chat, owner_id(), content, metadata={"qq_notice_kind": "failure"})
