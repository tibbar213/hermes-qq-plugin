"""Native slash confirmations rendered as QQ cards, scoped to the initiating member."""
from dataclasses import dataclass
import secrets
import time

from agent.i18n import t
from gateway.platforms.base import SendResult
from tools import slash_confirm as pending
from .clarify import _KeyboardPayload
from ..settings import owner_id

PREFIX = "qqslash:"


@dataclass
class Card:
    session: str
    entry: dict
    chat: str
    scene: str
    anchor: str | None
    choices: tuple


def _matches(card):
    current = pending.get_pending(card.session)
    return bool(current and current["confirm_id"] == card.entry["confirm_id"]
                and current["handler"] is card.entry["handler"]
                and current["created_at"] == card.entry["created_at"]
                and time.time()-current["created_at"] <= pending.DEFAULT_TIMEOUT_SECONDS)


def _body(command, permanent):
    details = {
        "new": "将开启新对话，当前聊天内容不再作为上下文，新版人设会在新对话加载",
        "reset": "将开启新对话，当前聊天内容不再作为上下文",
        "undo": "将撤销最近的对话记录，已执行的服务器操作不会因此回滚",
        "reload-mcp": "将重新连接 MCP 并刷新工具，可能需要重新建立模型提示缓存",
    }
    text = "确认 /" + command + "\n" + details.get(command, "将执行这条命令，请确认是否继续")
    if permanent:
        text += "\n“以后免确认”会关闭此类命令的后续确认"
    return text


async def send_confirm(adapter, chat_id, title, message, session_key, confirm_id, metadata=None):
    entry = pending.get_pending(session_key)
    scene = adapter._guess_chat_type(chat_id)
    parsed = adapter._parse_gateway_session_key(session_key) or {}
    if not entry or entry["confirm_id"] != confirm_id or scene not in {"group", "c2c"}:
        return SendResult(success=False, error="No matching QQ command confirmation")
    requester = parsed.get("user_id") if scene == "group" else parsed.get("chat_id")
    if not requester or parsed.get("chat_id") != chat_id or parsed.get("platform") != "qqbot":
        return SendResult(success=False, error="Cannot identify QQ command requester")
    choices = [("once", "确认这次", 1), ("cancel", "取消", 0)]
    if requester == owner_id():
        choices.insert(1, ("always", "以后免确认", 0))
    states = getattr(adapter, "_slash_confirm_cards", None)
    if states is None:
        states = adapter._slash_confirm_cards = {}
    for key, card in list(states.items()):
        if not _matches(card):
            states.pop(key, None)
    token = secrets.token_hex(10)
    anchor = (metadata or {}).get("message_id") or adapter._last_msg_id.get(chat_id)
    states[token] = Card(session_key, entry, chat_id, scene, anchor, tuple(c[0] for c in choices))
    buttons = [{"id": "slash_"+choice,
        "render_data": {"label": label, "visited_label": label, "style": style},
        "action": {"type": 1, "permission": {"type": 2}, "data": PREFIX+token+":"+choice}}
        for choice, label, style in choices]
    keyboard = _KeyboardPayload({"content": {"rows": [{"buttons": buttons}]}})
    result = await adapter.send_with_keyboard(
        chat_id, _body(entry["command"], requester == owner_id()), keyboard, reply_to=anchor)
    if not result.success:
        states.pop(token, None)
    return result


def _result_text(command, choice, result):
    if choice == "cancel" and result == t("gateway.confirm.cancelled", command=command):
        return "取消了，继续刚才的聊天"
    if command in {"new", "reset"} and any(result.startswith(t(key)) for key in
            ("gateway.reset.header_default", "gateway.reset.header_new")):
        text = "新对话开好了，说吧"
        if choice == "always":
            saved = t("gateway.confirm.destructive_optout") in result
            text += "\n以后此类命令不再询问" if saved else "\n免确认设置没保存，下次仍会询问"
        return text
    return result


async def handle_callback(adapter, event):
    if not event.button_data.startswith(PREFIX):
        return False
    parts = event.button_data.split(":")
    if len(parts) != 3:
        return True
    _, token, choice = parts
    states = getattr(adapter, "_slash_confirm_cards", {})
    card = states.get(token)
    if not card or choice not in card.choices:
        return True
    if not _matches(card):
        states.pop(token, None)
        return True
    if event.scene != card.scene or not adapter._is_authorized_interaction_for_session(event, card.session):
        return True
    if card.scene == "group" and not adapter._is_group_allowed(card.chat, event.operator_openid):
        return True
    if card.scene == "c2c" and not adapter._is_dm_intake_allowed(event.operator_openid):
        return True
    states.pop(token, None)
    result = await pending.resolve(card.session, card.entry["confirm_id"], choice)
    if result:
        await adapter.send(card.chat, _result_text(card.entry["command"], choice, result), reply_to=card.anchor)
    return True
