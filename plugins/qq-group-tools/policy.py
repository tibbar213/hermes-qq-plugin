"""Task-scoped QQ authorization; identity comes from the gateway, never the prompt."""
import hashlib
import re
from gateway.session_context import get_session_env
from . import tasks, parent_files, query_access, execution
SAFE_TOOLS = frozenset({"web_search", "web_extract", "vision_analyze", "text_to_speech",
    "todo_list", "skills_list", "skill_view", "tool_search", "tool_describe"})
PARENT_TOOLS = frozenset({"qq_task", "tool_search", "tool_describe", "clarify", "todo_list", "skills_list", "skill_view"})
SAFE_COMMAND = re.compile(r"^(?:date(?: -u| -Is| -Iseconds)?|whoami|id|uptime|uname(?: -[amnrspvio]+)?|pwd|hostname|free(?: -[mhg])?|df(?: -[hT])?)$")

def actor():
    return str(get_session_env("HERMES_SESSION_PLATFORM", "")), str(get_session_env("HERMES_SESSION_USER_ID", ""))

def guard(tool_name, args, **kwargs):
    try:
        return _guard(tool_name, args, **kwargs)
    except Exception:
        if get_session_env("HERMES_SESSION_PLATFORM", "") == "qqbot":
            return {"action": "block", "message": "任务执行权限检查失败，已阻止操作；请让主代理报告此问题"}
        raise

def _guard(tool_name, args, **kwargs):
    platform, user = actor()
    if platform != "qqbot":
        return None
    if not user:
        return {"action": "block", "message": "缺少可验证的 QQ 成员身份"}
    task = tasks.current()
    if task:
        if tool_name in {"qq_task", "delegate_task"}:
            return {"action": "block", "message": "当前子代理只执行本任务，不可递归派发"}
        if tool_name == "terminal" and args.get("persist_on_release"):
            return {"action": "block", "message": "任务进程不能脱离生命周期；部署常驻服务请使用已授权的服务器服务管理"}
        if task.access == "server":
            if tool_name == "terminal" and (args.get("background") or str(args.get("command", "")).rstrip().endswith("&")):
                return {"action": "block", "message": "服务器命令请以前台方式运行并设置适当timeout；常驻服务使用systemd管理"}
            error = tasks.enter_server_tool(task)
            if not error and tool_name in execution.EXECUTION_TOOLS:
                error = execution.enter(task, kwargs.get("task_id"))
            return {"action": "block", "message": error} if error else None
        if task.access == "sandbox":
            if tool_name == "process_manage":
                from tools.process_registry import process_registry
                process = process_registry.get(str(args.get("session_id", ""))) if args.get("session_id") else None
                if (args.get("action") in {"poll", "log", "wait", "kill", "close", "write", "submit"}
                        and process and process.owner_task_id in task.env_keys):
                    return None
                return {"action": "block", "message": "只能管理本任务进程，不能移交、保活或查看其他任务"}
            allowed = execution.EXECUTION_TOOLS | {"web_search", "web_extract", "todo_list", "skills_list",
                                                  "tool_search", "tool_describe"}
            if query_access.mcp_allowed(tool_name):
                return None
            if tool_name not in allowed:
                return {"action": "block", "message": "沙箱任务仅允许隔离终端/文件和明确放行的查询工具。技能资料用read_file读取，运行脚本用terminal；需要宿主机操作请返回主代理申请server任务，不要尝试绕过。"}
            if tool_name in execution.EXECUTION_TOOLS:
                error = execution.acquire_sandbox(task) or execution.enter(task, kwargs.get("task_id"))
                if error:
                    return {"action": "block", "message": error}
                approval_context = __import__("tools.approval_context", fromlist=["set_current_session_key"])
                approval_context.set_current_session_key(task.approval_key)
                updates = {}
                if tool_name == "terminal" and args.get("workdir"):
                    updates["workdir"] = execution.map_path(args["workdir"], task)
                if tool_name != "terminal" and args.get("path"):
                    updates["path"] = execution.map_path(args["path"], task)
                return {"action": "modify", "args": {**args, **updates}} if updates else None
            return None
        if tool_name in query_access.FILE_TOOLS:
            return query_access.guard_files(tool_name, args, task)
        if query_access.mcp_allowed(tool_name):
            return None
        if tool_name.startswith("mcp__"):
            return {"action": "block", "message": "此 MCP 工具未获查询授权；当前仅放行 Tavily 搜索、提取和站点结构查询。不要重复尝试；确需其他操作时交回主代理判断任务授权。"}
        if tool_name in {'search_bilibili', 'bili_parse_video', 'watch_video', 'bili_recall'}:
            return None
        if tool_name == 'bili_bangumi' and args.get('action') in {'search', 'info', 'trending', 'timeline', 'updates'}:
            return None
        if tool_name == 'bilibili_manage' and args.get('action', 'status') in {'status', 'check', 'plan'}:
            return None
        if tool_name in SAFE_TOOLS and not (tool_name == "text_to_speech" and args.get("output_path")):
            return None
        if (tool_name == "terminal" and set(args) <= {"command", "timeout", "max_output_chars", "workdir"}
                and (not args.get("workdir") or args["workdir"] == task.workspace)
                and SAFE_COMMAND.fullmatch(str(args.get("command", "")).strip())):
            return None
        return {"action": "block", "message": "此任务只有查询权限；如确需服务器操作，返回主代理申请整个任务的授权"}
    if tool_name in parent_files.TOOLS:
        return parent_files.guard(tool_name, args, user, tasks.owner_id(), tasks.get_hermes_home())
    if tool_name in PARENT_TOOLS:
        return None
    if tool_name == "memory" and user == tasks.owner_id():
        return None
    return {"action": "block", "message": "群内实际工作必须调用 qq_task，交给独立子代理；不要直接操作或逐命令申请"}

def isolate_browser(tool_name, args, **kwargs):
    platform, user = actor()
    if platform != "qqbot" or not user:
        return None
    task = tasks.current()
    if tool_name == "terminal" and task and not args.get("workdir"):
        return {"action": "modify", "args": {"workdir": "/workspace" if task.access == "sandbox" else task.workspace}}
    if tool_name != "browser_exec":
        return None
    identity = task.id if task else str(get_session_env("HERMES_SESSION_KEY", "")) or ":".join([
        str(get_session_env("HERMES_SESSION_CHAT_ID", "")),
        str(get_session_env("HERMES_SESSION_THREAD_ID", "")), user])
    return {"action": "modify", "args": {"session": "qq-" + hashlib.sha256(identity.encode()).hexdigest()[:24]}}
