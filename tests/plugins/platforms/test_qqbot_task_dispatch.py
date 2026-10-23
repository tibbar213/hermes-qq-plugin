"""Task-scoped approval, real child delegation entry, grant expiry and mobile callback checks."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from gateway.session_context import set_session_vars, clear_session_vars
from tools import approval
OWNER="test-owner"
from standalone_qq.platform.keyboards import ApprovalRequest, InteractionEvent
from gateway.platforms.base import SendResult
from gateway.config import PlatformConfig
from standalone_qq.platform.adapter import QQAdapter

@pytest.fixture
def plugin(tmp_path, monkeypatch):
    path=Path(__file__).resolve().parents[3] / "tests/fixtures/qq-plugin"
    spec=importlib.util.spec_from_file_location("qq_task_test",path/"__init__.py",submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.tasks,"get_hermes_home",lambda:tmp_path)
    monkeypatch.setattr(module.tasks,"review_task",lambda *a:{"verdict":"ESCALATE","summary":"test human gate"})
    tokens=set_session_vars(platform="qqbot",user_id="member-a",chat_id="group",chat_type="group",
                            session_key="agent:main:qqbot:group:group:member-a")
    yield module
    clear_session_vars(tokens)

def test_approve_once_runs_several_commands_and_expires(plugin,monkeypatch):
    ask=Mock(return_value={"approved":True})
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    captured=[]
    def child(**kwargs):
        task=plugin.tasks.current()
        captured.append(task)
        assert kwargs["background"] is True
        assert kwargs["goal"]=="创建演示文件然后清理"
        assert Path(task.workspace).is_dir()
        for command in ["touch demo.txt","rm demo.txt"]:
            assert plugin.policy.guard("terminal",{"command":command},task_id="approval-test-child") is None
            assert approval.check_all_command_guards(command,"local")["approved"]
        assert plugin.policy.guard("qq_task",{})["action"]=="block"
        return '{"status":"completed"}'
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    result=json.loads(plugin.tasks.handle({"goal":"创建演示文件然后清理","context":"仅任务目录","access":"server"},parent_agent=SimpleNamespace()))
    assert result["task_id"]
    ask.assert_called_once()
    assert not captured[0].active
    assert plugin.tasks.current() is None
    assert not approval.is_session_yolo_enabled("qq-task:"+result["task_id"])
    assert plugin.policy.guard("terminal",{"command":"touch another.txt"})["action"]=="block"

def test_denied_task_never_spawns(plugin,monkeypatch):
    monkeypatch.setattr(approval,"request_tool_approval",lambda *a,**k:{"approved":False})
    spawn=Mock()
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    result=json.loads(plugin.tasks.handle({"goal":"装软件","access":"server"},parent_agent=SimpleNamespace()))
    assert "error" in result
    spawn.assert_not_called()

def test_read_task_can_search_but_cannot_modify(plugin,monkeypatch):
    ask=Mock()
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    def child(**kwargs):
        assert plugin.policy.guard("web_search",{"query":"test"}) is None
        assert plugin.policy.guard("terminal",{"command":"date"}) is None
        updated=plugin.policy.isolate_browser("terminal",{"command":"date"})["args"]
        assert updated["workdir"]==plugin.tasks.current().workspace
        assert plugin.policy.guard("terminal",{"command":"date",**updated}) is None
        assert plugin.policy.guard("terminal",{"command":"touch demo"})["action"]=="block"
        assert plugin.policy.guard("write_file",{"path":"demo"})["action"]=="block"
        return '{"status":"completed"}'
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    plugin.tasks.handle({"goal":"查资料","access":"read_only"},parent_agent=SimpleNamespace())
    ask.assert_not_called()

def test_child_failure_revokes_grant(plugin,monkeypatch):
    monkeypatch.setattr(approval,"request_tool_approval",lambda *a,**k:{"approved":True})
    captured=[]
    def fail(**kw):
        captured.append(plugin.tasks.current())
        raise RuntimeError("test failure")
    monkeypatch.setattr(plugin.tasks,"delegate_task",fail)
    with pytest.raises(RuntimeError):
        plugin.tasks.handle({"goal":"任务","access":"server"},parent_agent=SimpleNamespace())
    assert not captured[0].active
    assert not approval.is_session_yolo_enabled("qq-task:"+captured[0].id)
    assert plugin.tasks.current() is None

def test_owner_task_needs_no_second_confirmation(plugin,monkeypatch):
    set_session_vars(platform="qqbot",user_id=OWNER,chat_id="group",chat_type="group")
    ask=Mock()
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    monkeypatch.setattr(plugin.tasks,"delegate_task",lambda **kw:'{"status":"completed"}')
    assert "error" not in json.loads(plugin.tasks.handle({"goal":"检查并修改","access":"server"},parent_agent=SimpleNamespace()))
    ask.assert_not_called()

def test_permission_is_bound_to_verified_member(plugin,monkeypatch):
    def child(**kw):
        task=plugin.tasks.current()
        set_session_vars(platform="qqbot",user_id="member-b",chat_id="group",chat_type="group")
        assert plugin.tasks.current() is None
        assert plugin.policy.guard("terminal",{"command":"touch demo"})["action"]=="block"
        task.active=False
        return '{"status":"completed"}'
    monkeypatch.setattr(approval,"request_tool_approval",lambda *a,**k:{"approved":True})
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    plugin.tasks.handle({"goal":"任务","access":"server"},parent_agent=SimpleNamespace())

@pytest.mark.asyncio
async def test_mobile_card_uses_server_identity_checks(monkeypatch):
    from standalone_qq.platform import owner_approval
    a=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"test",
        "group_policy":"allowlist","group_allow_from":["group"]}))
    a.send_with_keyboard=AsyncMock(return_value=SendResult(success=True))
    pending={"request_id":"task-one","description":"QQ_TASK\n任务：测试"}
    monkeypatch.setattr(owner_approval,"get_pending_gateway_approval",lambda key:dict(pending))
    resolve=Mock(return_value=1)
    monkeypatch.setattr(owner_approval,"resolve_gateway_approval",resolve)
    key="agent:main:qqbot:group:group:member-a"
    await a.send_approval_request("group",ApprovalRequest(session_key=key,title="tool",description=pending["description"]))
    call=a.send_with_keyboard.call_args
    assert "任务授权" in call.args[1]
    button=call.args[2].to_dict()["content"]["rows"][0]["buttons"][0]
    assert button["action"]["permission"]=={"type":2}
    assert button["render_data"]["label"]=="允许此任务"
    data=button["action"]["data"]
    await owner_approval.handle_callback(a,InteractionEvent(scene="c2c",user_openid="stranger",button_data=data))
    resolve.assert_not_called()
    await owner_approval.handle_callback(a,InteractionEvent(scene="c2c",user_openid=OWNER,button_data=data))
    resolve.assert_called_once_with(key,"once",request_id="task-one")

def test_parallel_read_tasks_keep_member_and_workspace_separate(plugin,monkeypatch):
    import threading
    from contextvars import copy_context
    from concurrent.futures import ThreadPoolExecutor
    rendezvous=threading.Barrier(2)
    results={}
    futures=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        def child(**kwargs):
            context=copy_context()
            def work():
                task=plugin.tasks.current()
                rendezvous.wait(timeout=5)
                assert task.user==kwargs["goal"]
                results[task.user]=task.workspace
                plugin.tasks.on_subagent_stop(child_status="completed")
            futures.append(pool.submit(context.run,work))
            return json.dumps({"status":"dispatched","delegation_id":"fake-"+kwargs["goal"]})
        monkeypatch.setattr(plugin.tasks,"delegate_task",child)
        def run(user):
            tokens=set_session_vars(platform="qqbot",user_id=user,chat_id="group",chat_type="group")
            try:
                return json.loads(plugin.tasks.handle({"goal":user,"access":"read_only"},parent_agent=SimpleNamespace()))
            finally:
                clear_session_vars(tokens)
        first=run("a")
        second=run("b")
        for future in futures:
            future.result(timeout=10)
    assert set(results)=={"a","b"}
    assert results["a"]!=results["b"]
    assert first["task_id"]!=second["task_id"]


def test_registry_handler_resolves_parent_from_host_turn(plugin,monkeypatch):
    from agent.subagent_lifecycle import bind_subagent_parent
    captured=[]
    parent=SimpleNamespace()
    def child(**kw):
        captured.append(kw["parent_agent"])
        return '{"status":"completed"}'
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    with bind_subagent_parent(parent):
        result=plugin.tasks.handle({"goal":"查询","access":"read_only"})
    assert "error" not in json.loads(result)
    assert captured==[parent]

def test_followup_reuses_artifacts_with_only_parent_supplied_context(plugin,monkeypatch):
    observed=[]
    def child(**kw):
        workspace=Path(plugin.tasks.current().workspace)
        observed.append(kw["context"])
        if len(observed)==1:
            (workspace/"result.txt").write_text("first")
            return json.dumps({"summary":"created result.txt; verified first"})
        assert (workspace/"result.txt").read_text()=="first"
        (workspace/"result.txt").write_text("second")
        return json.dumps({"summary":"updated result.txt; verified second"})
    monkeypatch.setattr(plugin.tasks,"delegate_task",child)
    first=json.loads(plugin.tasks.handle({"goal":"创建文件","context":"只用任务目录","access":"read_only"},parent_agent=SimpleNamespace()))
    second=json.loads(plugin.tasks.handle({"goal":"改成second","context":"保留其他文件","access":"read_only","resume_task_id":first["task_id"]},parent_agent=SimpleNamespace()))
    assert first["workspace"]==second["workspace"]
    assert first["task_id"]==second["task_id"]
    assert "created result.txt; verified first" not in observed[1]
    assert "只用任务目录" not in observed[1]
    assert "保留其他文件" in observed[1]
    assert Path(second["workspace"],"result.txt").read_text()=="second"


def test_resume_rejects_another_member_or_group(plugin,monkeypatch):
    spawn=Mock(return_value="{}")
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    first=json.loads(plugin.tasks.handle({"goal":"查询","access":"read_only"},parent_agent=SimpleNamespace()))
    for user,chat in [("member-b","group"),("member-a","other-group")]:
        set_session_vars(platform="qqbot",user_id=user,chat_id=chat,chat_type="group")
        result=json.loads(plugin.tasks.handle({"goal":"继续","access":"read_only","resume_task_id":first["task_id"]},parent_agent=SimpleNamespace()))
        assert "error" in result
    assert spawn.call_count==1


def test_resume_requests_new_server_permission(plugin,monkeypatch):
    ask=Mock(side_effect=[{"approved":True},{"approved":False}])
    monkeypatch.setattr(approval,"request_tool_approval",ask)
    spawn=Mock(return_value="{}")
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    first=json.loads(plugin.tasks.handle({"goal":"创建文件","access":"server"},parent_agent=SimpleNamespace()))
    second=json.loads(plugin.tasks.handle({"goal":"继续修改","access":"server","resume_task_id":first["task_id"]},parent_agent=SimpleNamespace()))
    assert "error" in second
    assert spawn.call_count==1
    assert ask.call_count==2
    assert ask.call_args_list[0].kwargs["rule_key"]!=ask.call_args_list[1].kwargs["rule_key"]


def test_in_progress_workspace_cannot_be_reentered(plugin,monkeypatch):
    from standalone_qq.platform.task_workspaces import TaskStore
    store=TaskStore(plugin.tasks.get_hermes_home())
    task_id="a"*24
    store.begin(task_id,"group","member-a","goal","context")
    spawn=Mock()
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    result=json.loads(plugin.tasks.handle({"goal":"继续","access":"read_only","resume_task_id":task_id},parent_agent=SimpleNamespace()))
    assert "error" in result
    spawn.assert_not_called()


def test_origin_and_single_ack_are_bound_to_request(tmp_path):
    from standalone_qq.platform.task_workspaces import TaskStore
    store=TaskStore(tmp_path)
    task_id="b"*24
    store.begin(task_id,"group","member-a","写网站","仅任务目录","request")
    assert store.claim_ack("group","request")
    assert not store.claim_ack("group","request")
    assert store.origin("group","request")["user"]=="member-a"
    assert store.origin("other","request") is None
    store.finish(task_id,"completed")
    assert TaskStore(tmp_path).get(task_id,"group","member-a")["status"]=="completed"


def test_cleanup_keeps_deliverables_active_recent_and_symlink_targets(tmp_path):
    from contextlib import closing
    from standalone_qq.platform.task_workspaces import TaskStore,RETENTION_SECONDS
    store=TaskStore(tmp_path)
    workspaces=[]
    for index in range(4):
        task_id=f"{index:024x}"
        workspace=store.begin(task_id,"group","member","task","scope")
        (workspace/".tmp").mkdir()
        (workspace/".tmp"/"scratch").write_text("discard")
        (workspace/"report.txt").write_text("deliverable")
        workspaces.append(workspace)
        if index!=1:
            store.finish(task_id,"completed")
    with closing(store.connect()) as c,c:
        c.execute("UPDATE tasks SET updated=1 WHERE id!=?",(f"{2:024x}",))
    outside=tmp_path/"outside"
    outside.mkdir()
    (outside/"keep").write_text("keep")
    (workspaces[3]/".cache").symlink_to(outside,target_is_directory=True)
    now=RETENTION_SECONDS+100
    dry=store.cleanup(now=now,dry_run=True)
    assert set(dry)=={str(workspaces[i]/".tmp") for i in (0,3)}
    assert all((w/".tmp").exists() for w in workspaces)
    assert store.cleanup(now=now)==dry
    assert all((w/"report.txt").read_text()=="deliverable" for w in workspaces)
    assert (workspaces[1]/".tmp"/"scratch").exists()
    assert (workspaces[2]/".tmp"/"scratch").exists()
    assert (outside/"keep").read_text()=="keep"
    assert (workspaces[3]/".cache").is_symlink()


@pytest.mark.asyncio
async def test_task_result_has_only_member_mention_and_stays_one_message(tmp_path,monkeypatch):
    from standalone_qq.platform.task_workspaces import TaskStore
    a=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"test","markdown_support":True}))
    a._task_store=TaskStore(tmp_path)
    a._task_store.begin("c"*24,"group","member-a","创建网站","仅任务目录","request-a")
    a._chat_type_map["group"]="group"
    a._ensure_connected=AsyncMock(return_value=True)
    a._send_chunk=AsyncMock(return_value=SendResult(success=True))
    await a.send("group","已创建\n也检查了","request-a")
    assert a._send_chunk.call_count==1
    content=a._send_chunk.call_args.args[1]
    assert 'id="member-a"' in content
    assert "创建网站" not in content
    assert content.startswith('<qqbot-at-user id="member-a" /> 已创建')
    assert "已创建\n也检查了" in content
    a._send_chunk.reset_mock()
    await a.send("group","普通聊天","other-request")
    assert a._send_chunk.call_args.args[1]=="普通聊天"

def test_start_ack_is_sent_once_to_original_request(plugin,monkeypatch):
    import asyncio
    import threading
    from standalone_qq.platform.task_workspaces import TaskStore
    store=TaskStore(plugin.tasks.get_hermes_home())
    store.begin("d"*24,"group","member-a","测试任务","仅任务目录","original-request")
    loop=asyncio.new_event_loop()
    ready=threading.Event()
    loop.call_soon(ready.set)
    worker=threading.Thread(target=loop.run_forever)
    adapter=SimpleNamespace(send=AsyncMock(return_value=SendResult(success=True)))
    notices=sys.modules[plugin.tasks.acknowledge.__module__]
    monkeypatch.setattr(notices,"_live_adapter",
                        lambda platform:(SimpleNamespace(_gateway_loop=loop),adapter))
    worker.start()
    try:
        assert ready.wait(timeout=5)
        notices.acknowledge(store,"group","original-request")
        notices.acknowledge(store,"group","original-request")
        adapter.send.assert_awaited_once_with("group","行，我看看",reply_to="original-request")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=5)
        loop.close()

@pytest.mark.asyncio
async def test_inbound_message_anchor_reaches_task_context():
    a=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"test"}))
    a._process_attachments=AsyncMock(return_value={
        "voice_transcripts":[],"attachment_info":"","image_urls":[],"image_media_types":[]})
    a._process_quoted_context=AsyncMock(return_value={
        "quote_block":"","image_urls":[],"image_media_types":[]})
    a.handle_message=AsyncMock()
    await a._ingest({},"original-request","执行任务",None,"",
                    chat_id="group",qq_chat_type="group",user_id="member-a",chat_type="group")
    event=a.handle_message.call_args.args[0]
    assert event.message_id=="original-request"
    assert event.source.message_id=="original-request"
    assert event.source.user_id=="member-a"

def test_parent_can_read_and_find_task_contents_without_child(plugin):
    from tools.file_tools import read_file_tool,search_tool
    set_session_vars(platform="qqbot",user_id=OWNER,chat_id="group",chat_type="group")
    root=plugin.tasks.get_hermes_home()/"qqbot"/"tasks"
    work=root/("e"*24)
    work.mkdir(parents=True)
    (work/"notes.txt").write_text("parent-read-proof",encoding="utf-8")
    checked=plugin.policy.guard("read_file",{"path":str(work/"notes.txt")})
    assert checked["action"]=="modify"
    result=json.loads(read_file_tool(**checked["args"],task_id="parent-read-test"))
    assert not result.get("error"), result
    assert "parent-read-proof" in str(result)
    listing=plugin.policy.guard("search_files",{"path":".","pattern":"*","target":"files"})
    assert listing["action"]=="modify"
    assert listing["args"]["path"]==str(root.resolve())
    result=json.loads(search_tool(**listing["args"],task_id="parent-list-test"))
    assert not result.get("error"), result
    assert "notes.txt" in str(result)
    assert plugin.policy.guard("terminal",{"command":"cat "+str(work/"notes.txt")})["action"]=="block"
    assert plugin.policy.guard("write_file",{"path":str(work/"notes.txt"),"content":"changed"})["action"]=="block"
    assert (work/"notes.txt").read_text()=="parent-read-proof"


def test_parent_file_reads_are_scoped_to_task_root_and_verified_member(plugin):
    from standalone_qq.platform.task_workspaces import TaskStore
    home=plugin.tasks.get_hermes_home()
    store=TaskStore(home)
    workspace=store.begin("f"*24,"group","member-a","task","scope")
    file=workspace/"notes.txt"
    file.write_text("member-a")
    assert plugin.policy.guard("read_file",{"path":str(file)})["action"]=="modify"
    assert plugin.policy.guard("search_files",{"path":str(workspace),"pattern":"member-a"})["action"]=="modify"
    assert plugin.policy.guard("search_files",{"path":".","pattern":"*"})["action"]=="block"
    set_session_vars(platform="qqbot",user_id="member-b",chat_id="group",chat_type="group")
    assert plugin.policy.guard("read_file",{"path":str(file)})["action"]=="block"
    set_session_vars(platform="qqbot",user_id=OWNER,chat_id="group",chat_type="group")
    outside=home/"private.txt"
    outside.write_text("outside")
    alias=workspace/"escape.txt"
    alias.symlink_to(outside)
    for path in [str(outside),str(alias),"../../private.txt","/etc/passwd"]:
        assert plugin.policy.guard("read_file",{"path":path})["action"]=="block"
    assert plugin.policy.guard("read_file",{"path":str(workspace)})["action"]=="block"

def test_background_task_returns_before_work_and_keeps_grant_until_stop(plugin,monkeypatch):
    from contextvars import copy_context
    from standalone_qq.platform.task_workspaces import TaskStore
    from tools import approval_context
    route="agent:main:qqbot:group:group:"+OWNER
    set_session_vars(platform="qqbot",user_id=OWNER,chat_id="group",chat_type="group",session_key=route,message_id="request-bg")
    pending=[]
    parent=SimpleNamespace(_active_children=[])
    def spawn(**kwargs):
        assert kwargs["background"] is True
        assert approval_context.get_current_session_key(default="")==route
        task=plugin.tasks.current()
        assert task.active
        pending.append((copy_context(),task))
        return json.dumps({"status":"dispatched","delegation_id":"background-one"})
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    first=json.loads(plugin.tasks.handle({"goal":"慢任务","access":"server"},parent_agent=parent))
    assert json.loads(first["result"])["status"]=="dispatched"
    context,task=pending[0]
    assert task.active
    assert plugin.tasks.current() is None
    assert not parent._active_children
    assert approval.is_session_yolo_enabled(task.approval_key)
    assert not approval.is_session_yolo_enabled(route)
    assert plugin.policy.guard("terminal",{"command":"touch bad"})["action"]=="block"
    assert TaskStore(plugin.tasks.get_hermes_home()).get(first["task_id"],"group",OWNER)["status"]=="running"
    def work():
        assert plugin.policy.guard("terminal",{"command":"touch allowed"},task_id="background-test-child") is None
        assert approval.check_all_command_guards("touch allowed","local")["approved"]
        plugin.tasks.on_subagent_stop(child_status="completed")
    context.run(work)
    assert not task.active
    assert not approval.is_session_yolo_enabled(task.approval_key)
    assert not plugin.tasks.SERVER_LOCK.locked()
    assert TaskStore(plugin.tasks.get_hermes_home()).get(first["task_id"],"group",OWNER)["status"]=="completed"


def test_cancelled_background_task_revokes_permission(plugin,monkeypatch):
    from contextvars import copy_context
    set_session_vars(platform="qqbot",user_id=OWNER,chat_id="group",chat_type="group")
    pending=[]
    def spawn(**kw):
        pending.append((copy_context(),plugin.tasks.current()))
        return json.dumps({"status":"dispatched","delegation_id":"cancel-test"})
    monkeypatch.setattr(plugin.tasks,"delegate_task",spawn)
    plugin.tasks.handle({"goal":"任务","access":"server"},parent_agent=SimpleNamespace())
    context,task=pending[0]
    context.run(lambda:plugin.tasks.on_subagent_stop(child_status="interrupted"))
    assert not task.active
    assert not approval.is_session_yolo_enabled(task.approval_key)


def test_background_completion_preserves_member_and_passive_reply_anchor(tmp_path):
    from standalone_qq.platform.task_workspaces import TaskStore
    from gateway.platforms.event import MessageEvent
    from gateway.session import SessionSource
    from gateway.config import Platform
    store=TaskStore(tmp_path)
    store.bind_delegation("job-one","a"*24,"group","member-a","request-a")
    event=MessageEvent(text="result",internal=True,
        source=SessionSource(platform=Platform.QQBOT,chat_id="group",user_id="member-a",chat_type="group"),
        metadata={"gateway_completion_ids":["job-one"]})
    store.anchor_completion(event)
    assert event.reply_anchor_override=="request-a"
    assert event.message_id is None
    event.reply_anchor_override=None
    event.source.user_id="member-b"
    store.anchor_completion(event)
    assert event.reply_anchor_override is None

@pytest.mark.asyncio
async def test_replayed_completion_restores_anchor_at_final_delivery(tmp_path):
    from unittest.mock import patch
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.platforms.event import MessageEvent
    from standalone_qq.platform.task_workspaces import TaskStore
    a=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"test"}))
    a._task_store=TaskStore(tmp_path)
    a._task_store.bind_delegation("job-one","a"*24,"group","member-a","request-a")
    event=MessageEvent(text="done",internal=True,message_id="native-automation-identity",
        source=a.build_source(chat_id="group",user_id="member-a",chat_type="group"),
        metadata={"gateway_completion_ids":["job-one"]})
    with patch.object(BasePlatformAdapter,"send_final_ledgered",new_callable=AsyncMock) as send:
        await a.send_final_ledgered(event,"route","done",{},reply_to=None)
        assert send.call_args.kwargs["reply_to"]=="request-a"
        assert event.message_id=="native-automation-identity"

def test_named_workspace_keeps_stable_id_and_reuses_directory(tmp_path):
    from standalone_qq.platform.task_workspaces import TaskStore
    store=TaskStore(tmp_path)
    task_id="1"*24
    workspace=store.begin(task_id,"group","member-a","原始完整目标","范围",title="接入Tavily")
    assert workspace.name=="接入Tavily-"+task_id[:12]
    assert store.get(task_id,"group","member-a")["directory"]==workspace.name
    assert store.get_by_directory(workspace.name,"group","member-a")==task_id
    with pytest.raises(ValueError):
        store.get_by_directory(workspace.name,"group","member-b")
    (workspace/"report.txt").write_text("keep")
    store.finish(task_id,"completed")
    again=store.begin(task_id,"group","member-a","继续原任务","范围",resume=True,title="不该改名")
    assert again==workspace
    assert (again/"report.txt").read_text()=="keep"


def test_task_directory_names_are_safe_and_collision_free(tmp_path):
    from standalone_qq.platform.task_workspaces import TaskStore
    store=TaskStore(tmp_path)
    first=store.begin("a"*24,"group","member","goal","scope",title="../../网站 首页;命令")
    second=store.begin("a"*23+"b","group","member","goal","scope",title="../../网站 首页;命令")
    assert first!=second
    assert first.parent==second.parent==tmp_path/"qqbot"/"tasks"
    assert "/" not in first.name and ".." not in first.name
    assert "网站" in first.name


def test_legacy_task_directory_survives_schema_upgrade(tmp_path):
    import sqlite3
    from standalone_qq.platform.task_workspaces import TaskStore
    root=tmp_path/"qqbot"
    root.mkdir()
    task_id="2"*24
    with sqlite3.connect(root/"task-state.sqlite3") as c:
        c.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY,chat TEXT,user TEXT,goal TEXT,context TEXT,summary TEXT,status TEXT,updated REAL)")
        c.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)",(task_id,"group","member","old","scope","","completed",1))
    old=root/"tasks"/task_id
    old.mkdir(parents=True)
    (old/"report.txt").write_text("old result")
    store=TaskStore(tmp_path)
    assert store.get_by_directory(task_id,"group","member")==task_id
    again=store.begin(task_id,"group","member","continue","scope",resume=True)
    assert again==old
    assert (again/"report.txt").read_text()=="old result"
