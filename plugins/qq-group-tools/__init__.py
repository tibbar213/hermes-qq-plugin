"""QQ independent subagent tasks and task-level owner approval."""
from . import policy, tasks

def register(ctx):
    from .execution import TaskProvider, wrap_tool
    ctx.register_terminal_environment_provider(TaskProvider(True))
    ctx.register_terminal_environment_provider(TaskProvider(False))
    ctx.register_middleware("tool_execution", wrap_tool)
    ctx.register_hook("pre_tool_call", policy.isolate_browser)
    ctx.register_hook("pre_tool_call", policy.guard)
    ctx.register_hook("subagent_stop", tasks.on_subagent_stop)
    ctx.register_tool(name="qq_task", toolset="qq_tasks", schema=tasks.SCHEMA,
                      handler=tasks.handle, description=tasks.SCHEMA["description"], emoji="🐟")


    from .platform.register import register as register_platform
    register_platform(ctx)
    from .web import register as register_web
    register_web(ctx)
    from .cli import register as register_cli
    register_cli(ctx)
