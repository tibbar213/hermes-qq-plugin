"""QQ query children can inspect scoped files and use explicitly approved query tools."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest
from gateway.session_context import set_session_vars, clear_session_vars
from tools.mcp_tool_schema import mcp_prefixed_tool_name

@pytest.fixture
def query(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "tests/fixtures/qq-plugin"
    spec = importlib.util.spec_from_file_location("qq_query_test", path/"__init__.py", submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    work = tmp_path/"tasks"/"current"
    work.mkdir(parents=True)
    skills = tmp_path/"skills"
    skills.mkdir()
    monkeypatch.setattr("tools.skills_tool.skill_search_dirs", lambda: ([(1, skills)], skills))
    tokens = set_session_vars(platform="qqbot", user_id="member", chat_id="group")
    task = module.tasks.Task("a"*24, "member", "group", "read_only", str(work))
    token = module.tasks.CURRENT.set(task)
    yield module, work, skills
    module.tasks.CURRENT.reset(token)
    clear_session_vars(tokens)


def test_query_reads_and_searches_own_files_through_real_file_tools(query):
    module, work, _ = query
    (work/"notes.txt").write_text("query permission proof")
    from tools.file_tools import read_file_tool, search_tool
    checked = module.policy.guard("read_file", {"path": "notes.txt"})
    assert checked["action"] == "modify"
    result = json.loads(read_file_tool(**checked["args"], task_id="query-read-proof"))
    assert "query permission proof" in str(result)
    checked = module.policy.guard("search_files", {"path": ".", "pattern": "*.txt", "target": "files"})
    assert checked["action"] == "modify"
    result = search_tool(**checked["args"], task_id="query-search-proof")
    assert "notes.txt" in result


def test_query_can_read_skill_assets_but_cannot_execute(query):
    module, _, skills = query
    asset = skills/"demo"/"references"/"guide.md"
    asset.parent.mkdir(parents=True)
    asset.write_text("reference")
    assert module.policy.guard("read_file", {"path": str(asset)})["action"] == "modify"
    assert module.policy.guard("search_files", {"path": str(skills), "pattern": "reference"})["action"] == "modify"
    assert module.policy.guard("terminal", {"command": "python demo.py"})["action"] == "block"
    assert module.policy.guard("execute_code", {"code": "print(1)"})["action"] == "block"


@pytest.mark.parametrize("tool", ["tool_search", "tool_describe"])
def test_query_can_discover_tools(query, tool):
    assert query[0].policy.guard(tool, {"query": "tavily"}) is None


@pytest.mark.parametrize("tool", ["tavily-search", "tavily-extract", "tavily-map"])
def test_only_explicit_tavily_queries_are_allowed(query, tool):
    assert query[0].policy.guard(mcp_prefixed_tool_name("tavily", tool), {}) is None


@pytest.mark.parametrize("server,tool", [("other", "tavily-search"), ("tavily", "tavily-research"),
    ("tavily", "delete-file"), ("tavily_extra", "tavily-search")])
def test_unknown_or_nonquery_mcp_is_blocked(query, server, tool):
    result = query[0].policy.guard(mcp_prefixed_tool_name(server, tool), {})
    assert result["action"] == "block"


@pytest.mark.parametrize("name", [".env", ".env.local", "credentials.json", "private.key"])
def test_query_does_not_read_credentials_even_inside_workspace(query, name):
    module, work, _ = query
    target = work/name
    target.write_text("test fixture")
    assert module.policy.guard("read_file", {"path": str(target)})["action"] == "block"
    assert module.policy.guard("search_files", {"path": str(work), "pattern": "fixture"})["action"] == "block"


def test_query_cannot_read_other_tasks_or_escape_via_symlink(query):
    module, work, _ = query
    other = work.parent/"other"
    other.mkdir()
    (other/"private.txt").write_text("other member")
    (work/"escape").symlink_to(other, target_is_directory=True)
    for path in [other/"private.txt", work/"escape"/"private.txt", work/".."/"other"/"private.txt"]:
        assert module.policy.guard("read_file", {"path": str(path)})["action"] == "block"
    assert module.policy.guard("search_files", {"path": str(work), "pattern": "member"})["action"] == "block"


def test_parent_still_cannot_call_mcp_or_read_skill_assets(query):
    module, _, skills = query
    asset = skills/"guide.md"
    asset.write_text("docs")
    module.tasks.CURRENT.set(None)
    assert module.policy.guard(mcp_prefixed_tool_name("tavily", "tavily-search"), {})["action"] == "block"
    assert module.policy.guard("read_file", {"path": str(asset)})["action"] == "block"


def test_query_still_cannot_modify_files(query):
    module, work, _ = query
    for tool in ["write_file", "patch", "memory", "send_message"]:
        assert module.policy.guard(tool, {"path": str(work/"test.txt")})["action"] == "block"
