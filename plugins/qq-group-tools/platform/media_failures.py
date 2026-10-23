"""Bounded per-file failure details for owner-only QQ attachment notices."""
from pathlib import Path

def remember(adapter, chat_id, path, result):
    key = (chat_id, path)
    cache = getattr(adapter, "_qq_media_failures", None)
    if cache is None:
        cache = adapter._qq_media_failures = {}
    cache.pop(key, None)
    if result.success:
        return
    error = result.error or ""
    name = Path(path).name.replace("\n", " ").replace("\r", " ")[:160]
    if "群文件超过上限" in error:
        reason = "QQ返回：群文件超过上限。当前返回未说明具体限制类型，请检查群文件额度或机器人上传限制"
        result.retryable = False
    elif "daily upload limit" in error.lower() or "40093002" in error:
        reason = "QQ机器人当日上传额度已用尽，请等待额度恢复"
        result.retryable = False
    elif "per-file upload limit" in error.lower():
        reason = "文件超过QQ允许的单文件大小上限"
        result.retryable = False
    elif "Media file not found" in error:
        reason = "找不到要发送的附件文件"
    else:
        reason = "QQ上传或发送未成功，具体原因已记录在服务器日志"
    retained = "文件仍保存在服务器，尚未交付到QQ" if Path(path).is_file() else "附件尚未交付到QQ"
    cache[key] = "附件发送失败：" + name + "\n" + reason + "\n" + retained
    while len(cache) > 64:
        cache.pop(next(iter(cache)))

async def notify(adapter, chat_id, path, metadata=None):
    cache = getattr(adapter, "_qq_media_failures", {})
    text = cache.pop((chat_id, path), None)
    if text is None:
        name = Path(path).name.replace("\n", " ").replace("\r", " ")[:160]
        text = "附件发送失败：" + name + "\n附件尚未交付到QQ，具体原因请查看服务器日志"
    from ..settings import owner_id
    return await adapter.send_private_notice(
        chat_id, owner_id(), text, metadata={**(metadata or {}), "qq_notice_kind": "failure"})
