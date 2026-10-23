"""Task-scoped native terminal providers; model code never receives the host transport."""
import os
from pathlib import Path
import subprocess
import threading

from agent.terminal_env_provider import TerminalEnvironmentProvider
from tools.environments.base import BaseEnvironment
from tools.environments.base_output import _popen_bash

BROKER = "/usr/local/sbin/hermes-qq-exec"
SANDBOX_SLOT = threading.Semaphore(1)
EXECUTION_TOOLS = frozenset({"terminal", "read_file", "search_files", "write_file", "patch"})


def control(*args, timeout=40):
    return subprocess.run(["/usr/bin/sudo", "-n", BROKER, *args], check=True, capture_output=True,
                          text=True, timeout=timeout, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})


def run_id(task):
    return task.approval_key.removeprefix("qq-task:") or task.id


def enter(task, tool_key):
    from tools.terminal_scope import default_terminal_scope, set_terminal_scope
    from tools.terminal_tool import register_task_env_overrides, record_session_cwd
    if not tool_key:
        return "缺少独立子代理的工具会话，已拒绝在宿主机回退执行"
    kind = "qq_sandbox" if task.access == "sandbox" else "qq_host"
    scope = default_terminal_scope()
    scope.update(TERMINAL_ENV=kind, TERMINAL_CWD="/workspace" if task.access == "sandbox" else task.workspace)
    set_terminal_scope(scope)
    if tool_key not in task.env_keys:
        register_task_env_overrides(tool_key, {"env_type": kind})
        record_session_cwd(tool_key, scope["TERMINAL_CWD"])
        task.env_keys.add(tool_key)
    return None


def acquire_sandbox(task):
    from agent.subagent_lifecycle import get_active_subagent_parent
    child = get_active_subagent_parent()
    with task.state_lock:
        if not task.active:
            return "任务已结束"
        while not task.sandbox_slot:
            if getattr(child, "_interrupt_requested", False):
                return "任务已取消"
            if SANDBOX_SLOT.acquire(timeout=0.5):
                task.sandbox_slot = True
    return None


def cleanup(task):
    from tools.terminal_tool import clear_task_env_overrides
    from tools.terminal_tool_lifecycle import cleanup_vm
    try:
        errors = []
        for key in list(task.env_keys):
            try:
                cleanup_vm(key)
            except Exception as exc:
                errors.append(exc)
            finally:
                clear_task_env_overrides(key)
        if task.execution_started:
            control("stop", run_id(task))
        if errors:
            raise RuntimeError("Native environment cleanup failed after runtime teardown") from errors[0]
    finally:
        task.env_keys.clear()
        if task.sandbox_slot:
            task.sandbox_slot = False
            SANDBOX_SLOT.release()


class TaskEnvironment(BaseEnvironment):
    def __init__(self, task, timeout):
        self.task = task
        self.execution_id = run_id(task)
        kind = "sandbox" if task.access == "sandbox" else "host"
        cwd = "/workspace" if kind == "sandbox" else task.workspace
        self.host_cwd = task.workspace
        self.host_cwd_mount = "/workspace" if kind == "sandbox" else task.workspace
        control("start", self.execution_id, kind, task.workspace, str(os.getpid()))
        task.execution_started = True
        super().__init__(cwd=cwd, timeout=timeout)
        self.init_session()

    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        argv = ["/usr/bin/sudo", "-n", BROKER, "exec", self.execution_id, cmd_string, "plain"]
        return _popen_bash(argv, stdin_data, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})

    def cleanup(self):
        control("stop", self.execution_id)

    def _kill_process(self, proc):
        # Closing docker exec alone leaves the in-container process alive.
        try:
            control("stop", self.execution_id, timeout=20)
        finally:
            super()._kill_process(proc)


class TaskProvider(TerminalEnvironmentProvider):
    def __init__(self, sandbox):
        self.sandbox = sandbox

    @property
    def name(self):
        return "qq_sandbox" if self.sandbox else "qq_host"

    @property
    def is_container(self):
        return self.sandbox

    @property
    def default_cwd(self):
        return "/workspace" if self.sandbox else "/root"

    def is_available(self):
        return Path(BROKER).is_file()

    def create_environment(self, *, timeout, **kwargs):
        from .tasks import current
        task = current()
        if not task or (task.access == "sandbox") != self.sandbox:
            raise PermissionError("QQ execution provider requires the matching active approved task")
        return TaskEnvironment(task, timeout)


def map_path(raw, task):
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return raw
    if path.is_relative_to(task.workspace):
        return "/workspace/" + str(path.relative_to(task.workspace))
    for source, target in [
        (str(__import__("hermes_constants").get_hermes_home() / "skills"), "/opt/hermes-skills"),
        (str(Path(__import__("hermes_constants").__file__).resolve().parent / "skills"), "/opt/hermes-builtin-skills")]:
        if path.is_relative_to(source):
            return target + "/" + str(path.relative_to(source))
    return raw


def wrap_tool(tool_name, args, next_call, **kwargs):
    """Hooks run in copied contexts: bind the actual execution callback, then restore."""
    from gateway.session_context import get_session_env
    if get_session_env("HERMES_SESSION_PLATFORM", "") != "qqbot":
        return next_call(args)
    from tools.terminal_scope import get_terminal_scope, set_terminal_scope, reset_terminal_scope
    from tools import approval_context
    from tools.registry import tool_error
    from .policy import guard
    scope_token = set_terminal_scope(get_terminal_scope())
    approval_token = approval_context.set_current_session_key(
        approval_context.get_current_session_key(default=""))
    try:
        decision = guard(tool_name, args, **kwargs)
        if decision and decision.get("action") == "block":
            return tool_error(decision["message"])
        if decision and decision.get("action") == "modify":
            args = {**args, **decision["args"]}
        return next_call(args)
    finally:
        approval_context.reset_current_session_key(approval_token)
        reset_terminal_scope(scope_token)
