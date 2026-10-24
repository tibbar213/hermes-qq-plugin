import json
import sqlite3

from standalone_qq.cli import read_groups, read_tasks


def test_missing_tasks_does_not_create_state(tmp_path):
    home = tmp_path / "absent"
    assert read_tasks(home) == []
    assert not home.exists()


def test_task_query_is_readonly_and_recent_first(tmp_path):
    root = tmp_path / "qqbot"
    root.mkdir()
    path = root / "task-state.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tasks(id,chat,user,goal,status,updated,directory)")
        db.executemany("INSERT INTO tasks VALUES(?,?,?,?,?,?,?)", [
            ("old", "g", "u", "old task", "complete", 1, "old-dir"),
            ("new", "g", "v", "new task", "running", 2, "new-dir")])
    db.close()
    before = path.read_bytes()
    assert read_tasks(tmp_path, 1)[0]["id"] == "new"
    assert path.read_bytes() == before


def test_groups_separates_observed_from_authorized(tmp_path):
    (tmp_path / "channel_directory.json").write_text(json.dumps({"platforms": {"qqbot": [
        {"id": "seen", "type": "group", "name": "cached"},
        {"id": "private", "type": "dm"}]}}), encoding="utf-8")
    result = read_groups(tmp_path, {"group_allow_from": "allowed,*"})
    assert result["allow_all"] is True
    assert {g["openid"] for g in result["groups"]} == {"seen", "allowed"}
    seen = next(g for g in result["groups"] if g["openid"] == "seen")
    assert seen["observed"] and not seen["explicitly_allowed"]
