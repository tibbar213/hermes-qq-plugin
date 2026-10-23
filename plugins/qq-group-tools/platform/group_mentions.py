"""Full-group trigger handling based on Tencent's qqbot-nodejs protocol and mention gate."""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import closing
import re
import sqlite3
from pathlib import Path


def mentioned_group_text(
    payload: dict, app_id: str, *, keywords: Sequence[str] = (), reply_to_bot: bool = False,
) -> str | None:
    """Keep ordinary conversation silent unless addressed by mention, keyword or bot reply."""
    author = payload.get("author") or {}
    if author.get("bot") is True:
        return None
    content = str(payload.get("content") or "").strip()
    mentions = payload.get("mentions") or []
    self_mentions = [
        mention for mention in mentions
        if isinstance(mention, dict) and mention.get("is_you") is True
    ]
    self_ids = {app_id} if app_id else set()
    for mention in self_mentions:
        member_id = mention.get("member_openid") or mention.get("id") or mention.get("user_openid")
        if member_id:
            self_ids.add(str(member_id))
    markers = [rf"<@!?{re.escape(member_id)}>" for member_id in self_ids]
    addressed = self_mentions or any(re.search(marker, content) for marker in markers)
    keyword_hit = any(keyword and keyword.casefold() in content.casefold() for keyword in keywords)
    if not (addressed or keyword_hit or reply_to_bot):
        return None
    for marker in markers:
        content = re.sub(marker, "", content)
    return content.strip()


def reply_reference(payload: dict) -> str:
    """Read the quote reference in the same precedence as Tencent's event dispatcher."""
    reference = ""
    for item in (payload.get("message_scene") or {}).get("ext") or []:
        key, separator, value = str(item).partition("=")
        if separator and key == "ref_msg_idx":
            reference = value
    if str(payload.get("message_type") or "") == "103":
        for element in payload.get("msg_elements") or []:
            if isinstance(element, dict) and element.get("msg_idx"):
                return str(element["msg_idx"])
    return reference


class GroupReplyIndex:
    """Persist this bot's sent references per group so quote triggers survive restarts."""

    def __init__(self, path: Path):
        self.path = path

    def is_reply_to_bot(self, group_id: str, payload: dict) -> bool:
        reference = reply_reference(payload)
        if not reference or not self.path.exists():
            return False
        with closing(sqlite3.connect(self.path)) as connection, connection:
            return connection.execute(
                "SELECT 1 FROM sent WHERE group_id=? AND reference=?",
                (group_id, reference),
            ).fetchone() is not None

    def remember(self, path: str, response: dict) -> None:
        match = re.fullmatch(r"/v2/groups/([^/]+)/messages", path)
        if not match:
            return
        references = {str(value) for value in (
            response.get("id"), (response.get("ext_info") or {}).get("ref_idx"),
        ) if value}
        if not references:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sent "
                "(group_id TEXT, reference TEXT, PRIMARY KEY(group_id, reference))"
            )
            connection.executemany(
                "INSERT OR REPLACE INTO sent VALUES (?, ?)",
                [(match.group(1), reference) for reference in references],
            )
            connection.execute(
                "DELETE FROM sent WHERE rowid NOT IN "
                "(SELECT rowid FROM sent ORDER BY rowid DESC LIMIT 2048)"
            )
        self.path.chmod(0o600)
