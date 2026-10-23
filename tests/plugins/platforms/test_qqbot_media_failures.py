"""QQ attachment failures retain actionable causes without publishing diagnostics."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from standalone_qq.platform.adapter import QQAdapter
OWNER="test-owner"
from standalone_qq.platform.media_failures import remember

@pytest.fixture
def adapter():
    a=QQAdapter(PlatformConfig(enabled=True,extra={"app_id":"123","client_secret":"fixture"}))
    a._ensure_connected=AsyncMock(return_value=True)
    a._send_chunk=AsyncMock(return_value=SendResult(success=True))
    a._last_msg_id[OWNER]="owner-anchor"
    return a

@pytest.mark.asyncio
async def test_quota_failure_reports_exact_reason_privately_and_retains_file(adapter,tmp_path):
    file=tmp_path/"计时器.html"
    file.write_text("artifact")
    adapter._upload_local_file=AsyncMock(side_effect=RuntimeError("QQ Bot API error [400] /v2/groups/group/upload_prepare: 群文件超过上限"))
    result=await adapter.send_document("group",str(file))
    assert not result.success and result.retryable is False
    await adapter._notify_media_delivery_failure("group",str(file))
    call=adapter._send_chunk.call_args
    assert call.args[0]==OWNER and call.args[2]=="owner-anchor"
    text=call.args[1]
    assert "群文件超过上限" in text and "计时器.html" in text and "文件仍保存在服务器" in text
    assert "Couldn't" not in text and str(tmp_path) not in text and "/v2/" not in text
    assert file.read_text()=="artifact"

@pytest.mark.asyncio
async def test_failure_details_do_not_cross_chat_or_file(adapter,tmp_path):
    file=str(tmp_path/"same.html")
    remember(adapter,"group-a",file,SendResult(success=False,error="群文件超过上限"))
    await adapter._notify_media_delivery_failure("group-b",file)
    assert "群文件超过上限" not in adapter._send_chunk.call_args.args[1]
    await adapter._notify_media_delivery_failure("group-a",file)
    assert "群文件超过上限" in adapter._send_chunk.call_args.args[1]

@pytest.mark.asyncio
async def test_private_failure_never_falls_back_to_group(adapter,tmp_path):
    adapter._send_chunk.return_value=SendResult(success=False,error="forbidden")
    adapter.send=AsyncMock()
    await adapter._notify_media_delivery_failure("group",str(tmp_path/"file.html"))
    adapter.send.assert_not_awaited()
    assert all(call.args[0]==OWNER for call in adapter._send_chunk.call_args_list)

def test_success_clears_stale_error_and_cache_is_bounded(adapter,tmp_path):
    file=str(tmp_path/"file.html")
    remember(adapter,"group",file,SendResult(success=False,error="群文件超过上限"))
    remember(adapter,"group",file,SendResult(success=True))
    assert not adapter._qq_media_failures
    for i in range(80):
        remember(adapter,"group",str(tmp_path/str(i)),SendResult(success=False,error="temporary"))
    assert len(adapter._qq_media_failures)==64

@pytest.mark.asyncio
async def test_api_preserves_business_code_without_echoing_unrelated_response_fields(adapter):
    adapter._auth_headers=AsyncMock(return_value={})
    response=SimpleNamespace(status_code=400,json=Mock(return_value={"code":40093002,"message":"群文件超过上限","access_token":"must-not-leak"}))
    adapter._http_client=SimpleNamespace(request=AsyncMock(return_value=response))
    with pytest.raises(RuntimeError) as caught:
        await adapter._api_request("POST","/v2/groups/group/upload_prepare",{})
    assert "40093002" in str(caught.value) and "群文件超过上限" in str(caught.value)
    assert "must-not-leak" not in str(caught.value)
