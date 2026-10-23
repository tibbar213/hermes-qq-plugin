"""Task-temp maintenance through the native Hermes plugin CLI."""
import json
from .platform.task_workspaces import TaskStore
def setup(parser):
    parser.add_argument("--dry-run",action="store_true")
def run(args):
    print(json.dumps({"dry_run":args.dry_run,"temporary_directories":TaskStore().cleanup(dry_run=args.dry_run)},ensure_ascii=False))
def register(ctx):
    ctx.register_cli_command("qq-task-cleanup","Clean idle QQ task temporary files",setup,run)
