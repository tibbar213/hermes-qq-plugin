"""QQ voice may use the configured Hermes transcription provider."""
from unittest.mock import patch
import pytest
from gateway.config import PlatformConfig
from standalone_qq.platform.adapter import QQAdapter

@pytest.mark.asyncio
@pytest.mark.parametrize("result,expected",[
    ({"success":True,"transcript":"你好小鱼"},"你好小鱼"),
    ({"success":False,"transcript":"","error":"unavailable"},None),
])
async def test_qq_calls_shared_transcription_provider(result,expected):
    adapter=QQAdapter(PlatformConfig(enabled=True,extra={
        "app_id":"123","client_secret":"test-only","stt":{"provider":"hermes"}}))
    with patch("tools.transcription_tools.transcribe_audio",return_value=result) as transcribe:
        assert await adapter._call_stt("/tmp/test-qq.wav")==expected
        transcribe.assert_called_once_with("/tmp/test-qq.wav")
