"""Attribute extract results by URL and verify provider-reported redirects independently."""
import asyncio
from urllib.parse import urlsplit

from tools.url_safety import normalize_url_for_request


def _identity(url):
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(normalize_url_for_request(url))
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return None
        port = p.port or (443 if p.scheme.lower() == "https" else 80)
        return p.scheme.lower(), p.hostname.lower(), port, p.path or "/", p.query
    except ValueError:
        return None


async def _redirect_matches(requested, returned):
    from tools.url_safety import async_is_safe_url, create_ssrf_safe_async_client
    from tools.website_policy import check_website_access
    from urllib.parse import urljoin

    current = requested
    async with create_ssrf_safe_async_client(timeout=3, follow_redirects=False) as client:
        for hop in range(6):
            if not await async_is_safe_url(current) or check_website_access(current):
                return False
            async with client.stream("GET", current) as response:
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(str(response.url), response.headers["location"])
                    continue
                return hop > 0 and 200 <= response.status_code < 300 and _identity(str(response.url)) == _identity(returned)
    return False


async def _verified_redirect(requested, returned):
    import httpx
    try:
        return await asyncio.wait_for(_redirect_matches(requested, returned), timeout=10)
    except (httpx.HTTPError, OSError, ValueError, TimeoutError):
        return False


def _error(url, message, reported=None):
    result = {"url": url, "title": "", "content": "", "error": message}
    if reported:
        result["reported_url"] = reported
    return result


async def align_extract_results(urls, results):
    """Return exactly one entry per requested URL, including errors for absent/mismatched pages.

    sourceURL attributes a result to a request; it is not proof that a changed final URL is real.
    A redirect is accepted only after a bounded, SSRF/policy-checked HTTP redirect-chain probe.
    """
    requested = {_identity(u): u for u in urls}
    aligned = {}
    redirects = {}
    for row in results:
        meta = row.get("metadata") or {}
        source = meta.get("sourceURL") if isinstance(meta, dict) else None
        returned = row.get("url")
        origin = requested.get(_identity(source or returned))
        if not origin:
            continue
        if row.get("error"):
            accepted = _error(origin, str(row["error"]))
        elif not _identity(returned):
            accepted = _error(origin, "Extract backend omitted the final source URL")
        elif _identity(returned) != _identity(origin):
            pair = origin, returned
            if pair not in redirects:
                redirects[pair] = await _verified_redirect(*pair)
            if redirects[pair]:
                accepted = {**row, "requested_url": origin, "source_verified": "http_redirect"}
            else:
                accepted = _error(origin, "Extract source mismatch: returned page is not a verified redirect; body discarded", returned)
        else:
            accepted = {**row, "url": origin}
        # Providers may append per-URL failures after successful documents.
        if origin not in aligned or aligned[origin].get("error"):
            aligned[origin] = accepted
    return [aligned.get(u) or _error(u, "Extract backend returned no matching result for this URL") for u in urls]
