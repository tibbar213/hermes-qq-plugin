"""Extraction must preserve URL identity across partial, reordered and rescued responses."""
import asyncio
from unittest.mock import Mock
import httpx
import pytest
from standalone_web import web_extract as wte


@pytest.fixture
def isolated_sources(monkeypatch):
    put = Mock()
    monkeypatch.setattr("tools.web_result_cache.extract_cache_put", put)
    monkeypatch.setattr(wte, "_rescue_eligible", lambda p: False)
    monkeypatch.setattr("tools.url_safety.async_is_safe_url", lambda url: _true())
    monkeypatch.setattr("tools.website_policy.check_website_access", lambda url: None)
    monkeypatch.setattr("tools.url_safety.create_ssrf_safe_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, request=req)), **kw))
    return put


async def _true():
    return True


def provider(rows):
    class Provider:
        name = "source-test"

        async def extract(self, urls, format=None):
            return rows
    return Provider()


def test_reordered_partial_results_keep_requested_order(isolated_sources):
    urls = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]
    rows = [{"url": urls[2], "content": "C"}, {"url": urls[0], "content": "A"}]
    got = asyncio.run(wte._dispatch_extract(provider(rows), urls, None))
    assert [r["url"] for r in got] == urls
    assert got[0]["content"] == "A"
    assert got[1]["error"] and not got[1]["content"]
    assert got[2]["content"] == "C"


@pytest.mark.parametrize("returned", ["https://other.example/article", "https://example.com/contact"])
def test_unverified_source_change_never_returns_or_caches_body(isolated_sources, returned):
    url = "https://example.com/docs"
    rows = [{"url": returned, "content": "WRONG BODY", "raw_content": "WRONG BODY",
             "metadata": {"sourceURL": url}}]
    got = asyncio.run(wte._dispatch_extract(provider(rows), [url], None))
    assert got[0]["url"] == url
    assert got[0]["error"] and not got[0]["content"]
    assert "WRONG BODY" not in str(got)
    isolated_sources.assert_not_called()


def test_real_redirect_is_labelled_and_not_cached_under_old_url(monkeypatch, isolated_sources):
    url, target = "https://example.com/old", "https://example.com/new"
    def respond(req):
        return httpx.Response(302, headers={"location": target}, request=req) if str(req.url) == url else httpx.Response(200, request=req)
    monkeypatch.setattr("tools.url_safety.create_ssrf_safe_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kw))
    rows = [{"url": target, "content": "redirected", "metadata": {"sourceURL": url}}]
    got = asyncio.run(wte._dispatch_extract(provider(rows), [url], None))
    assert got[0]["url"] == target and got[0]["requested_url"] == url
    assert got[0]["content"] == "redirected"
    isolated_sources.assert_not_called()


def test_rescue_results_also_require_verified_source(monkeypatch, isolated_sources):
    url = "https://example.com/docs"
    monkeypatch.setattr(wte, "_rescue_eligible", lambda p: True)
    monkeypatch.setattr(wte, "_rescue_extract", lambda *a: [
        {"url": "https://other.example/wrong", "content": "WRONG BODY",
         "metadata": {"sourceURL": url}}])
    got = asyncio.run(wte._dispatch_extract(provider([{"url": url, "error": "outage"}]), [url], None))
    assert got[0]["error"] and not got[0]["content"]
    isolated_sources.assert_not_called()


def test_successful_rescue_stays_one_shot(monkeypatch, isolated_sources):
    url = "https://example.com/docs"
    monkeypatch.setattr(wte, "_rescue_eligible", lambda p: True)
    monkeypatch.setattr(wte, "_rescue_extract", lambda *a: [
        {"url": url, "content": "rescued", "metadata": {"rescued_from": "source-test"}}])
    got = asyncio.run(wte._dispatch_extract(provider([{"url": url, "error": "outage"}]), [url], None))
    assert got[0]["content"] == "rescued"
    isolated_sources.assert_not_called()


def test_redirect_probe_never_requests_blocked_target(monkeypatch, isolated_sources):
    url, blocked = "https://example.com/docs", "http://169.254.169.254/latest/meta-data/"
    seen = []
    def respond(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": blocked}, request=req)
    async def safe(u):
        return u != blocked
    monkeypatch.setattr("tools.url_safety.async_is_safe_url", safe)
    monkeypatch.setattr("tools.url_safety.create_ssrf_safe_async_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kw))
    rows = [{"url": blocked, "content": "not trusted", "metadata": {"sourceURL": url}}]
    got = asyncio.run(wte._dispatch_extract(provider(rows), [url], None))
    assert got[0]["error"] and seen == [url]
    isolated_sources.assert_not_called()
