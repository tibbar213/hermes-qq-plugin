"""Native QQ choice cards for the existing Hermes clarify lifecycle."""
from dataclasses import dataclass
from typing import Any

from gateway.platforms.base import BasePlatformAdapter
from gateway.session_context import get_session_env
from tools import clarify_gateway

PREFIX = "qqcl:"

@dataclass
class _KeyboardPayload:
    payload: dict[str, Any]

    def to_dict(self):
        return self.payload

@dataclass
class _Card:
    chat_id: str
    scene: str
    requester: str
    entry: Any

async def send_clarify(adapter, chat_id, question, choices, clarify_id, session_key, metadata=None):
    entry = clarify_gateway.get_pending(clarify_id)
    scene = adapter._guess_chat_type(chat_id)
    parsed = adapter._parse_gateway_session_key(session_key) or {}
    requester = str(get_session_env("HERMES_SESSION_USER_ID", "") or parsed.get("user_id") or
                    (chat_id if scene == "c2c" else ""))
    if (not choices or not entry or entry.multi_select or not requester or
            scene not in {"c2c", "group"}):
        return await BasePlatformAdapter.send_clarify(
            adapter, chat_id, question, choices, clarify_id, session_key, metadata)
    # Keep full labels in the body; QQ caps button labels at ten characters.
    text = question + "\n\n" + "\n".join(f"{i+1}. {choice}" for i, choice in enumerate(choices))
    text += "\n\n也可以直接回复序号或自己的想法"
    buttons = []
    for token, label in [(str(i), f"{i+1} {str(c).replace('(Recommended)', '').strip()}"[:10])
                         for i, c in enumerate(choices)] + [("other", "自己说")]:
        buttons.append({"id": "cl_" + token,
            "render_data": {"label": label, "visited_label": label, "style": 1},
            "action": {"type": 1, "permission": {"type": 2},
                       "data": f"{PREFIX}{clarify_id}:{token}"}})
    keyboard = _KeyboardPayload({"content": {"rows": [{"buttons": [b]} for b in buttons]}})
    # State must exist before sending: a fast callback can beat the HTTP response.
    states = adapter._clarify_cards
    for key, card in list(states.items()):
        if clarify_gateway.get_pending(key) is not card.entry or card.entry.event.is_set():
            states.pop(key, None)
    states[clarify_id] = _Card(chat_id, scene, requester, entry)
    result = await adapter.send_with_keyboard(
        chat_id, text, keyboard, reply_to=adapter._last_msg_id.get(chat_id))
    if not result.success:
        states.pop(clarify_id, None)
    return result

async def handle_callback(adapter, event):
    if not event.button_data.startswith(PREFIX):
        return False
    parts = event.button_data.split(":")
    if len(parts) != 3:
        return True
    _, clarify_id, token = parts
    card = adapter._clarify_cards.get(clarify_id)
    if card is None:
        return True
    entry = clarify_gateway.get_pending(clarify_id)
    if entry is not card.entry or entry.event.is_set():
        adapter._clarify_cards.pop(clarify_id, None)
        return True
    chat = event.group_openid if event.scene == "group" else event.user_openid
    operator = event.operator_openid
    if event.scene != card.scene or chat != card.chat_id or operator != card.requester:
        return True
    if event.scene == "group" and not adapter._is_group_allowed(chat, operator):
        return True
    if event.scene == "c2c" and not adapter._is_dm_intake_allowed(operator):
        return True
    if token == "other":
        if clarify_gateway.mark_awaiting_text(clarify_id):
            await adapter.send(chat, "直接说你的想法就行")
        return True
    if not token.isdecimal() or not entry.choices:
        return True
    index = int(token)
    if index >= len(entry.choices):
        return True
    if clarify_gateway.resolve_gateway_clarify(clarify_id, entry.choices[index]):
        adapter._clarify_cards.pop(clarify_id, None)
    return True

async def retire_card(adapter, clarify_id, notice):
    # QQ cannot edit sent messages. Expired cards remain visible but cannot resolve a new task.
    adapter._clarify_cards.pop(clarify_id, None)

def pending_requester(adapter, chat_id, user_id=None):
    for clarify_id, card in adapter._clarify_cards.items():
        entry = clarify_gateway.get_pending(clarify_id)
        if (card.chat_id == chat_id and (user_id is None or card.requester == user_id)
                and entry is card.entry and not entry.event.is_set()):
            return card.requester
    return None

