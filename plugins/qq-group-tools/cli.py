"""Task-temp maintenance through the native Hermes plugin CLI."""
import json
from contextlib import closing
import sqlite3
from pathlib import Path
from .platform.task_workspaces import TaskStore

def read_tasks(home, limit=20):
    """Inspect existing state without creating directories or migrating schemas."""
    path = Path(home) / "qqbot" / "task-state.sqlite3"
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT id,chat,user,goal,status,updated,directory FROM tasks ORDER BY updated DESC LIMIT ?",
            (max(1, min(limit, 100)),))]

def read_groups(home, extra):
    raw = extra.get("group_allow_from", extra.get("groupAllowFrom", []))
    allowed = raw.split(",") if isinstance(raw, str) else (raw or [])
    allowed = {str(value).strip() for value in allowed if str(value).strip()}
    path = Path(home) / "channel_directory.json"
    cached = {}
    if path.is_file():
        directory = json.loads(path.read_text(encoding="utf-8"))
        cached = {str(item["id"]): item.get("name", "") for item in
                  directory.get("platforms", {}).get("qqbot", []) if item.get("type") == "group"}
    return {"policy": extra.get("group_policy", "pairing"), "allow_all": "*" in allowed,
            "groups": [{"openid": gid, "explicitly_allowed": gid in allowed,
                        "observed": gid in cached, "cached_label": cached.get(gid)}
                       for gid in sorted((allowed - {"*"}) | set(cached))]}

def setup_qq(parser):
    commands = parser.add_subparsers(dest="qq_command", required=True)
    commands.add_parser("status", help="Show QQ configuration and live connection state")
    commands.add_parser("groups", help="List allowed and previously observed group OpenIDs")
    tasks = commands.add_parser("tasks", help="List recent QQ tasks (read only)")
    tasks.add_argument("--limit", type=int, default=20, choices=range(1, 101), metavar="1..100")

def run_qq(args):
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    try:
        if args.qq_command == "tasks":
            result = {"tasks": read_tasks(home, args.limit)}
        else:
            from gateway.config import load_gateway_config
            config = load_gateway_config()
            platform = next((value for key, value in config.platforms.items()
                             if getattr(key, "value", key) == "qqbot"), None)
            extra = platform.extra if platform else {}
            if args.qq_command == "groups":
                result = read_groups(home, extra)
            else:
                from gateway.status import (read_runtime_status, runtime_status_is_stale,
                                            runtime_status_pid_is_live)
                runtime = read_runtime_status() or {}
                live = runtime_status_pid_is_live(runtime)
                fresh = not runtime_status_is_stale(runtime)
                qq = runtime.get("platforms", {}).get("qqbot", {})
                result = {"enabled": bool(platform and platform.enabled),
                          "credentials_configured": bool(extra.get("app_id") and extra.get("client_secret")),
                          "gateway_process_alive": live, "snapshot_fresh": fresh,
                          "connection": qq.get("state", "unknown") if live and fresh else "unknown",
                          "snapshot_updated_at": runtime.get("updated_at")}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        # Avoid printing configuration values or exception messages containing credentials.
        raise SystemExit("QQ 查询失败：配置或状态文件不可读（%s）" % type(exc).__name__) from None
def setup(parser):
    parser.add_argument("--dry-run",action="store_true")
def run(args):
    print(json.dumps({"dry_run":args.dry_run,"temporary_directories":TaskStore().cleanup(dry_run=args.dry_run)},ensure_ascii=False))
def register(ctx):
    ctx.register_cli_command("qq","Inspect QQ connection, groups and tasks",setup_qq,run_qq)
    ctx.register_cli_command("qq-task-cleanup","Clean idle QQ task temporary files",setup,run)
