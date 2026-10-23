"""Owner-only private approval cards bind to one live request; old clicks cannot approve new work."""
from ..settings import owner_id
import secrets
import time
from tools.approval import get_pending_gateway_approval, resolve_gateway_approval
from gateway.platforms.base import SendResult
from .clarify import _KeyboardPayload
from .keyboards import build_approval_text

PREFIX="qqowner:"

def private_owner_authorized(adapter,event,session_key):
    parsed=adapter._parse_gateway_session_key(session_key)
    if not parsed or parsed.get("platform")!="qqbot":
        return False
    if event.scene!="c2c" or event.user_openid!=owner_id() or event.operator_openid!=owner_id():
        return False
    chat=parsed.get("chat_id","")
    if parsed.get("chat_type")=="dm":
        return chat==owner_id() or adapter._is_dm_intake_allowed(chat)
    return adapter._is_group_allowed(chat,parsed.get("user_id",""))

async def send_owner_approval(adapter,chat_id,req,reply_to=None):
    pending=get_pending_gateway_approval(req.session_key)
    if not pending or not pending.get("request_id"):
        return SendResult(success=False,error="Approval request is no longer pending")
    # The core bridge exposes the oldest request, so never bind a different prompt to it.
    if pending.get("description") and pending["description"]!=req.description:
        return SendResult(success=False,error="Another approval is at the head of this session")
    adapter._chat_type_map[owner_id()]="c2c"
    parsed=adapter._parse_gateway_session_key(req.session_key) or {}
    requester=parsed.get("user_id") or parsed.get("chat_id") or "unknown"
    text="操作确认\n来源："+chat_id+"\n成员："+requester+"\n"+build_approval_text(req)
    is_task = req.description.startswith("QQ_TASK")
    if is_task:
        text = "任务授权\n" + req.description.removeprefix("QQ_TASK\n") + "\n仅本任务有效，不是永久权限"
    token=secrets.token_hex(10)
    now=time.monotonic()
    for key,(_,_,created) in list(adapter._owner_approval_cards.items()):
        if now-created>600:
            adapter._owner_approval_cards.pop(key,None)
    adapter._owner_approval_cards[token]=(req.session_key,pending["request_id"],now)
    buttons=[]
    for choice,label,style in [("once","允许此任务" if is_task else "允许这一次",1),("deny","拒绝",0)]:
        buttons.append({"id":"owner_"+choice,"render_data":{"label":label,"visited_label":label,"style":style},
            "action":{"type":1,"permission":{"type":2},
                      "data":PREFIX+token+":"+choice}})
    keyboard=_KeyboardPayload({"content":{"rows":[{"buttons":buttons}]}})
    anchor=adapter._last_msg_id.get(owner_id()) if chat_id!=owner_id() else reply_to
    result=await adapter.send_with_keyboard(owner_id(),text,keyboard,reply_to=anchor)
    if not result.success:
        adapter._owner_approval_cards.pop(token,None)
    return result

async def handle_callback(adapter,event):
    if not event.button_data.startswith(PREFIX):
        return False
    parts=event.button_data.split(":")
    if len(parts)!=3 or parts[2] not in {"once","deny"}:
        return True
    _,token,choice=parts
    card=adapter._owner_approval_cards.get(token)
    if not card:
        return True
    session_key,request_id,created=card
    if not private_owner_authorized(adapter,event,session_key):
        return True
    pending=get_pending_gateway_approval(session_key)
    if (time.monotonic()-created>600 or not pending or pending.get("request_id")!=request_id):
        adapter._owner_approval_cards.pop(token,None)
        return True
    if resolve_gateway_approval(session_key,choice,request_id=request_id):
        adapter._owner_approval_cards.pop(token,None)
    return True
