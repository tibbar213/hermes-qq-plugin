"""One acknowledgment on the originating QQ message, independent of model commentary."""
import asyncio
import logging
from gateway.config import Platform
from tools.send_message_senders import _live_adapter

logger = logging.getLogger(__name__)


def acknowledge(store, chat, message):
    runner, adapter = _live_adapter(Platform("qqbot"))
    if adapter is None or not message:
        return
    loop = getattr(runner, "_gateway_loop", None)
    if loop is None or not loop.is_running() or not store.claim_ack(chat, message):
        return
    future = asyncio.run_coroutine_threadsafe(
        adapter.send(chat, "行，我看看", reply_to=message), loop)
    try:
        result = future.result(timeout=15)
        if not result.success:
            logger.warning("QQ task acknowledgment failed: %s", result.error)
    except Exception:
        # Do not retry a potentially delivered message; keep the actual task running.
        logger.warning("QQ task acknowledgment delivery uncertain", exc_info=True)
