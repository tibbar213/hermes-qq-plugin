"""Scoped query-only access for QQ children; unknown MCP operations remain unapproved."""
import os
from pathlib import Path

FILE_TOOLS = frozenset({"read_file", "search_files"})
MCP_QUERIES = {"tavily": ("tavily-search", "tavily-extract", "tavily-map")}
_SENSITIVE_NAMES = frozenset({".ssh", ".gnupg", ".netrc", ".npmrc", ".pypirc",
    "credentials", "credentials.json", "auth.json", "token.json", "tokens.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"})


def _block(message):
    return {"action": "block", "message": message}


def mcp_allowed(tool_name):
    from tools.mcp_tool_schema import mcp_prefixed_tool_name
    return any(tool_name == mcp_prefixed_tool_name(server, tool)
               for server, tools in MCP_QUERIES.items() for tool in tools)


def _sensitive(path):
    return any(part.lower() in _SENSITIVE_NAMES or part.lower().startswith(".env")
               or Path(part).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
               for part in path.parts)


def _search_tree_safe(root):
    # The native search traverses descendants. Checking only its root would admit secret files
    # or linked files beneath an otherwise permitted directory.
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=_raise_walk_error):
        for name in dirs + files:
            path = Path(directory) / name
            count += 1
            if count > 10000 or path.is_symlink() or _sensitive(Path(name)):
                return False
    return True


def _raise_walk_error(error):
    raise error


def guard_files(tool_name, args, task):
    from tools.skills_tool import skill_search_dirs

    raw = args.get("path", "." if tool_name == "search_files" else "")
    if not isinstance(raw, str) or not raw.strip():
        return _block("请指定本任务目录或已安装技能资料中的路径")
    try:
        work = Path(task.workspace)
        if work.is_symlink():
            raise ValueError("linked workspace")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = work / path
        resolved = path.resolve(strict=True)
        roots, _ = skill_search_dirs()
        allowed = [work.resolve()] + [Path(root).resolve() for _, root in roots]
        if _sensitive(path) or _sensitive(resolved) or not any(resolved.is_relative_to(root) for root in allowed):
            raise ValueError("outside query scope or credential path")
        if tool_name == "read_file" and not resolved.is_file():
            return _block("读取正文请指定文件；列目录请使用 search_files")
        if tool_name == "search_files" and resolved.is_dir() and not _search_tree_safe(resolved):
            return _block("搜索目录含凭据路径、符号链接或文件过多；请缩小到需要的资料子目录")
    except (OSError, ValueError, RuntimeError):
        return _block("查询子代理只能读取本任务目录和已安装技能资料；不可读取凭据或其他任务")
    return {"action": "modify", "args": {**args, "path": str(resolved)}}
