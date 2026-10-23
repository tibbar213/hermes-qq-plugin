"""Parent agents can inspect task files without acquiring a server execution grant."""
from pathlib import Path

from gateway.session_context import get_session_env
from .platform.task_workspaces import TaskStore

TOOLS = frozenset({"read_file", "search_files"})


def guard(tool_name, args, user, owner, home):
    root = Path(home) / "qqbot" / "tasks"
    raw = args.get("path", ".")
    if not isinstance(raw, str) or not raw.strip():
        return {"action": "block", "message": "请指定任务目录中的路径"}
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        base = root.resolve()
        if root.is_symlink() or not resolved.is_relative_to(base):
            raise ValueError("outside task directory")
        relative = resolved.relative_to(base)
        if user != owner:
            if not relative.parts:
                raise ValueError("task root belongs to all members")
            TaskStore(home).get_by_directory(relative.parts[0], get_session_env("HERMES_SESSION_CHAT_ID", ""), user)
        if tool_name == "read_file" and not resolved.is_file():
            return {"action": "block", "message": "列目录请使用 search_files，target=files；读取正文使用 read_file"}
    except (OSError, ValueError, RuntimeError):
        return {"action": "block", "message": "主代理只读范围为任务目录；其他成员只能查看本人在当前会话的任务文件"}
    return {"action": "modify", "args": {**args, "path": str(resolved)}}
