"""Durable QQ task workspaces, reply origins and conservative temporary-file cleanup."""
from contextlib import closing
from pathlib import Path
import re
import shutil
import sqlite3
import time

from hermes_constants import get_hermes_home

TASK_ID = re.compile(r"[0-9a-f]{24}")
RETENTION_SECONDS = 14 * 86400


def task_directory_name(title, task_id):
    from tools.delegation_redaction import redact_child_text
    safe = redact_child_text(str(title))
    slug = re.sub(r"[^\w\u3400-\u9fff-]+", "-", safe, flags=re.UNICODE).strip("-_")[:20].rstrip("-_")
    return (slug or "任务") + "-" + task_id[:12]


class TaskStore:
    def __init__(self, home=None):
        self.root = Path(home or get_hermes_home()) / "qqbot"
        self.path = self.root / "task-state.sqlite3"

    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, chat TEXT, user TEXT, goal TEXT, context TEXT,
                summary TEXT DEFAULT '', status TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS requests(
                chat TEXT, message TEXT, user TEXT, goal TEXT, task_id TEXT,
                ack INTEGER DEFAULT 0, created REAL,
                PRIMARY KEY(chat,message));
            CREATE TABLE IF NOT EXISTS delegations(
                id TEXT PRIMARY KEY, task_id TEXT, chat TEXT, user TEXT, message TEXT);
        """)
        connection.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
        if "directory" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN directory TEXT NOT NULL DEFAULT ''")
        connection.commit()
        self.path.chmod(0o600)
        return connection

    def get(self, task_id, chat, user):
        if not TASK_ID.fullmatch(task_id):
            raise ValueError("无效任务编号")
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id=? AND chat=? AND user=?", (task_id, chat, user)
            ).fetchone()
        if row is None:
            raise ValueError("找不到该成员在当前会话中的任务；不可复用别人的目录")
        return dict(row)

    def get_by_directory(self, directory, chat, user):
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT id FROM tasks WHERE (directory=? OR (directory='' AND id=?)) AND chat=? AND user=?",
                (directory, directory, chat, user)).fetchone()
        if row is None:
            raise ValueError("不是当前成员在本会话中的任务目录")
        return row["id"]

    def begin(self, task_id, chat, user, goal, context, message="", resume=False, title=""):
        if not TASK_ID.fullmatch(task_id):
            raise ValueError("无效任务编号")
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if resume:
                old = connection.execute(
                    "SELECT status,directory FROM tasks WHERE id=? AND chat=? AND user=?", (task_id, chat, user)
                ).fetchone()
                if old is None or old["status"] == "running":
                    raise ValueError("任务不存在或仍在执行，请勿重复派发")
                directory = old["directory"] or task_id
                connection.execute("UPDATE tasks SET status='running',updated=? WHERE id=?", (time.time(), task_id))
            else:
                directory = task_directory_name(title or goal, task_id)
                candidate = self.root / "tasks" / directory
                if candidate.exists() or candidate.is_symlink():
                    directory += task_id[12:]
                connection.execute(
                    "INSERT INTO tasks(id,chat,user,goal,context,status,updated,directory) VALUES(?,?,?,?,?,'running',?,?)",
                    (task_id, chat, user, goal, context, time.time(), directory))
            if message:
                connection.execute(
                    "INSERT INTO requests(chat,message,user,goal,task_id,created) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(chat,message) DO UPDATE SET task_id=excluded.task_id",
                    (chat, message, user, goal, task_id, time.time()))
            workspace = self.root / "tasks" / directory
            if workspace.is_symlink() or workspace.resolve() != (self.root / "tasks").resolve() / directory:
                raise ValueError("任务目录不是有效的独立目录")
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        return workspace

    def finish(self, task_id, status):
        with closing(self.connect()) as connection, connection:
            connection.execute("UPDATE tasks SET status=?,updated=? WHERE id=?",
                               (status, time.time(), task_id))

    def claim_ack(self, chat, message):
        if not message:
            return False
        with closing(self.connect()) as connection, connection:
            return connection.execute(
                "UPDATE requests SET ack=1 WHERE chat=? AND message=? AND ack=0", (chat, message)
            ).rowcount == 1

    def origin(self, chat, message):
        if not message or not self.path.exists():
            return None
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM requests WHERE chat=? AND message=?", (chat, message)
            ).fetchone()
        return dict(row) if row else None

    def bind_delegation(self, delegation_id, task_id, chat, user, message):
        with closing(self.connect()) as connection, connection:
            connection.execute("INSERT OR REPLACE INTO delegations VALUES(?,?,?,?,?)",
                               (delegation_id, task_id, chat, user, message))

    def anchor_completion(self, event):
        ids = (event.metadata or {}).get("gateway_completion_ids") or []
        if not event.internal or not ids or not self.path.exists():
            return
        with closing(self.connect()) as connection:
            for delegation_id in ids:
                row = connection.execute("SELECT * FROM delegations WHERE id=? AND chat=? AND user=?",
                    (delegation_id, event.source.chat_id, event.source.user_id)).fetchone()
                if row and row["message"]:
                    # Keep the native automation identity; only restore QQ's passive-send anchor.
                    event.reply_anchor_override = row["message"]
                    return

    def cleanup(self, now=None, dry_run=False):
        cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
        removed = []
        with closing(self.connect()) as connection, connection:
            # Claiming a task and cleaning it cannot race, including the daily timer process.
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id,directory FROM tasks WHERE status!='running' AND updated<?", (cutoff,)).fetchall()
            root = (self.root / "tasks").resolve()
            for row in rows:
                if not TASK_ID.fullmatch(row["id"]):
                    continue
                directory = row["directory"] or row["id"]
                if Path(directory).name != directory:
                    continue
                workspace = self.root / "tasks" / directory
                if workspace.is_symlink() or workspace.resolve() != root / directory or not workspace.is_dir():
                    continue
                for name in (".tmp", ".cache"):
                    target = workspace / name
                    if target.is_symlink() or not target.is_dir():
                        continue
                    removed.append(str(target))
                    if not dry_run:
                        shutil.rmtree(target)
                if not dry_run and not any(workspace.iterdir()):
                    workspace.rmdir()
        return removed


def task_reply_heading(origin):
    user = origin["user"]
    return f'<qqbot-at-user id="{user}" /> ' if re.fullmatch(r"[A-Za-z0-9_-]+", user) else ""
