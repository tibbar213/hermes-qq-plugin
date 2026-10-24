"""One approved QQ task runs in one real Hermes child; grants die with the call."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import secrets
import threading
from hermes_constants import get_hermes_home
from .settings import owner_id
from gateway.session_context import get_session_env
from tools import approval, approval_context
from tools.delegate_tool import delegate_task
from .result_status import task_result_status
from .platform.task_workspaces import TaskStore

CURRENT = ContextVar("qq_approved_task", default=None)
SERVER_LOCK = threading.Lock()
DISPATCH_LOCK = threading.Lock()

@dataclass
class Task:
    id: str
    user: str
    chat: str
    access: str
    workspace: str
    active: bool = True
    approval_key: str = ""
    lock_held: bool = False
    env_keys: set = field(default_factory=set, repr=False)
    sandbox_slot: bool = False
    execution_started: bool = False
    state_lock: object = field(default_factory=threading.Lock, repr=False)

def current():
    task = CURRENT.get()
    if (task and task.active and task.user == get_session_env("HERMES_SESSION_USER_ID", "")
            and task.chat == get_session_env("HERMES_SESSION_CHAT_ID", "")):
        return task
    return None

def enter_server_tool(task):
    from agent.subagent_lifecycle import get_active_subagent_parent
    child = get_active_subagent_parent()
    with task.state_lock:
        if not task.active:
            return "任务已结束"
        while not task.lock_held:
            if getattr(child, "_interrupt_requested", False):
                return "任务已取消"
            if SERVER_LOCK.acquire(timeout=0.5):
                task.lock_held = True
    # Only this child's tool context sees the private grant, never the chat's route key.
    approval_context.set_current_session_key(task.approval_key)
    return None


def finish_task(task, status):
    with task.state_lock:
        if not task.active:
            return
        task.active = False
        try:
            if task.access in {"server", "sandbox"}:
                approval.disable_session_yolo(task.approval_key)
            from .execution import cleanup
            try:
                cleanup(task)
            finally:
                TaskStore(get_hermes_home()).finish(task.id, status)
        finally:
            if task.lock_held:
                task.lock_held = False
                SERVER_LOCK.release()


def on_subagent_stop(child_status=None, **kwargs):
    task = current()
    if task:
        finish_task(task, child_status or "failed")


def handle(args, parent_agent=None, **kwargs):
    from agent.subagent_lifecycle import get_active_subagent_parent
    parent_agent = parent_agent or get_active_subagent_parent()
    if get_session_env("HERMES_SESSION_PLATFORM", "") != "qqbot":
        return json.dumps({"error": "QQ tasks only"})
    if CURRENT.get() is not None or getattr(parent_agent, "_delegate_depth", 0):
        return json.dumps({"error": "子代理不能再次申请任务"})
    user = get_session_env("HERMES_SESSION_USER_ID", "")
    chat = get_session_env("HERMES_SESSION_CHAT_ID", "")
    goal = str(args.get("goal", "")).strip()
    context = str(args.get("context", "")).strip()
    access = args.get("access", "sandbox")
    if not user or not chat or not parent_agent or not goal or access not in {"read_only", "sandbox", "server"}:
        return json.dumps({"error": "缺少任务、身份或有效权限类型"})
    store = TaskStore(get_hermes_home())
    resume_id = str(args.get("resume_task_id") or "").strip()
    run_id = secrets.token_hex(12)
    task_id = resume_id or run_id
    message = get_session_env("HERMES_SESSION_MESSAGE_ID", "")
    try:
        if resume_id:
            store.get(resume_id, chat, user)
        workspace = store.begin(task_id, chat, user, goal, context, message, resume=bool(resume_id), title=str(args.get("title") or ""))
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    try:
        approved_by = authorize(goal, context, access, user, chat, run_id)
        if not approved_by:
            result = {"error": "主人未批准此任务", "task_id": task_id}
            store.finish(task_id, "denied")
            return json.dumps(result, ensure_ascii=False)
        result = execute(parent_agent, task_id, run_id, user, chat, access, workspace, goal, context)
        payload = json.loads(result)
        state = task_result_status(payload)
        if state == "running":
            store.bind_delegation(payload["delegation_id"], task_id, chat, user, message)
        else:
            store.finish(task_id, state)
        response = {"task_id": task_id, "workspace": str(workspace),
                    "status": "dispatched" if state == "running" else state,
                    "approved_by": approved_by, "access": access, "result": result}
        if state == "failed":
            response["error"] = payload.get("error") if isinstance(payload, dict) else None
            response["error"] = response["error"] or "子代理未成功完成任务，请查看返回结果"
        return json.dumps(response, ensure_ascii=False)
    except BaseException:
        store.finish(task_id, "failed")
        raise


def authorize(goal, context, access, user, chat, run_id):
    if access != "server" or user == owner_id():
        return "requester"
    if approval._yolo_active() or approval_context._get_approval_mode() == "off":
        return ""
    reason = ("QQ_TASK\n成员：" + user + "\n群/会话：" + chat + "\n任务：" + goal
              + "\n范围与验收：" + context + "\n权限：宿主机/root（只限本任务）\n授权：仅本轮完整任务；结束自动失效")
    # This native action gate asks a human directly; it never calls the smart guardian.
    decision = approval.request_tool_approval(
        "qq_task", reason, rule_key="qq-task:" + run_id)
    return "owner" if decision.get("approved") else ""


def execute(parent_agent, task_id, run_id, user, chat, access, workspace, goal, context):
    from tools.async_delegation import active_count
    from tools.delegate_tool_config import _get_max_async_children
    from gateway.session_context import async_delivery_supported
    task = Task(task_id, user, chat, access, str(workspace), approval_key="qq-task:" + run_id)
    from tools.terminal_scope import get_terminal_scope, set_terminal_scope, reset_terminal_scope
    scope_token = set_terminal_scope(get_terminal_scope())
    token = CURRENT.set(task)
    # Native async results route by approval context; keep the actual conversation key here.
    route = get_session_env("HERMES_SESSION_KEY", "") or approval_context.get_current_session_key(default="")
    approval_token = approval_context.set_current_session_key(route)
    detached = False
    terminal_status = "failed"
    try:
        if not async_delivery_supported():
            return json.dumps({"error": "当前会话不支持后台任务回传"})
        if access in {"server", "sandbox"}:
            approval.enable_session_yolo(task.approval_key)
        # All QQ spawns use this lock: do not let native at-capacity fallback block the parent.
        with DISPATCH_LOCK:
            if active_count() >= _get_max_async_children():
                return json.dumps({"error": "后台任务数量已达上限，请等待一项完成；主代理仍可聊天"})
            result = delegate_task(
                goal=goal,
                context=(context + "\n任务工作目录：" + str(workspace)
                         + ("\n执行级别：隔离沙箱，命令与文件工具在容器内运行，工作目录 /workspace；只有本任务目录可持久写入。可访问公网，但不能访问宿主机、内网、云元数据或凭据。Python3、Node22、npm、git、rg可用。使用terminal执行脚本，不要使用execute_code、浏览器或技能预处理；技能资料用read_file读取/opt/hermes-skills或/opt/hermes-builtin-skills。缺少Python库时在/workspace创建.venv并通过.venv/bin/pip安装，Node依赖用npm安装到/workspace；普通装库不需升级服务器权限。只有需要系统软件包、宿主机服务或配置时才返回主代理申请server；不得自行提权。" if access == "sandbox" else ("\n执行级别：已由主人授权访问宿主机，terminal与文件工具具备root权限，仅操作本任务允许的对象。" if access == "server" else "\n执行级别：只读查询，只能使用已放行的查询工具和读取本任务资料，不得写入或运行任意脚本。"))
                         + "\n仅完成当前成员的上述任务；用中文报告结果及实际验证。"
                         + "\n交付文件留在工作目录；临时文件放 .tmp/，缓存放 .cache/。"
                         + "\n这两个临时目录在任务闲置14天后会清理；不得存放交付成果。"
                         + "\n不要操作其他任务的目录，不创建脱离任务生命周期的后台工作。"),
                background=True, parent_agent=parent_agent)
        terminal_status = task_result_status(json.loads(result))
        detached = terminal_status == "running"
        return result
    finally:
        if not detached:
            finish_task(task, terminal_status)
        approval_context.reset_current_session_key(approval_token)
        CURRENT.reset(token)
        reset_terminal_scope(scope_token)


SCHEMA = {
    "name": "qq_task",
    "description": "QQ后台独立子代理任务。查询用read_only；编写运行脚本、处理文件、构建项目默认sandbox，隔离容器内有Python3、Node22和npm，可联网搜索下载和用pip/npm安装本任务依赖，不能访问宿主机、内网、云元数据或凭据；修改宿主机服务、部署或安装系统软件包用server。主人来源身份发起的server任务直接授权，可从沙箱任务升级无需再问主人；查询和sandbox任务直接执行，不调用JEV或模型初审；其他成员的server任务直接私发给主人批准整项任务，拒绝或未批准都不能执行。权限仅由access决定。主代理自由指定分工，不能创造新权限。收到dispatched后结束当前回合，结果稍后由主代理自然转述；不轮询，无其他回答时输出NO_REPLY。可用resume_task_id继续当前成员已结束任务的原目录。整项任务一次申请，不逐命令审批。",
    "parameters": {"type": "object", "properties": {
        "goal": {"type": "string", "description": "用户的完整任务目标"},
        "title": {"type": "string", "description": "内部任务短标题，建议2到12字，例如接入Tavily、网站首页。不含密钥或私人信息；用来命名目录，不向群里展示工单标题。省略则从目标生成；复用原任务时保留原目录名。"},
        "context": {"type": "string", "description": "必要上下文、允许操作的对象、目录、限制和验收标准；不得擅自扩大用户请求"},
        "access": {"type": "string", "enum": ["read_only", "sandbox", "server"]},
        "resume_task_id": {"type": "string", "description": "仅紧接的追问确需沿用原目录时填写最近qq_task返回的task_id；新话题省略。只能继续当前成员在本会话的任务。context从近期对话提供本次所需信息，不自动加载旧结果。"}

    }, "required": ["goal", "context", "access"]}
}
