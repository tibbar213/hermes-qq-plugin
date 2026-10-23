"""Source-verified extraction registered through Hermes' opt-in tool override surface."""
import json
import logging
from typing import Any, Optional
from tools.registry import tool_error
from tools.url_safety import async_is_safe_url
from tools.web_tools import (_get_extract_backend, _ensure_web_plugins_loaded, _finish_debug,
    WEB_EXTRACT_SCHEMA, check_web_api_key)
from tools.web_tools_truncate import (_effective_char_limit, _trim_results, _truncate_results,
    convert_base64_images_to_links)
from .web_extract import (_extract_safe_urls, _merge_in_order, _resolve_extract_provider,
    _result_entry, _validate_extract_urls)
logger=logging.getLogger(__name__)
async def web_extract_tool(urls: list[Any], format: str | None = None, char_limit: Optional[int] = None) -> str:
    """Extract clean page content (no LLM) from URLs via the configured backend.

    Pages over ``char_limit`` (default web.extract_char_limit or 15000) are head+tail truncated with a footer
    pointing at the stored full text; inline base64 images become ``[IMAGE: alt]``. URLs carrying secrets are
    refused before any fetch; private-network URLs are blocked per entry. Returns JSON ``{"results": [...]}``.
    """
    normalized_urls, normalized_indices, invalid_urls, blocked = _validate_extract_urls(urls)
    if blocked is not None:
        return blocked
    debug_call_data = {
        "parameters": {"urls": normalized_urls, "format": format, "char_limit": char_limit}, "error": None,
        "pages_extracted": 0, "pages_truncated": 0, "original_response_size": 0, "final_response_size": 0,
        "truncation_metrics": [], "processing_applied": [],
    }

    try:
        logger.info("Extracting content from %d URL(s)", len(normalized_urls))
        # SSRF protection — filter private/internal URLs before any backend.
        safe_urls, safe_indices, ssrf_blocked = [], [], {}
        for index, url in zip(normalized_indices, normalized_urls):
            if await async_is_safe_url(url):
                safe_urls.append(url)
                safe_indices.append(index)
            else:
                ssrf_blocked[index] = _result_entry(
                    url, "Blocked: URL targets a private or internal network address"
                )

        results = []
        if safe_urls:
            backend = _get_extract_backend()
            _ensure_web_plugins_loaded()
            provider, error_json = _resolve_extract_provider(backend)
            if error_json is not None:
                return error_json
            results = await _extract_safe_urls(provider, safe_urls, format)
        # Reconstruct input order across invalid, blocked, and provider entries (providers preserve
        # the order of the safe URL list they receive).
        if invalid_urls or ssrf_blocked:
            fixed = {**ssrf_blocked, **invalid_urls}
            results = _merge_in_order(len(urls), fixed, safe_indices, safe_urls, results)

        logger.info("Extracted content from %d pages", len(results))
        debug_call_data["pages_extracted"] = len(results)
        debug_call_data["original_response_size"] = len(json.dumps({"results": results}))
        debug_call_data["processing_applied"].append("truncate_and_store")
        _truncate_results(results, _effective_char_limit(char_limit), debug_call_data)
        trimmed = _trim_results(results)
        result_json = (
            json.dumps({"results": trimmed}, indent=2, ensure_ascii=False) if trimmed
            else tool_error("Content was inaccessible or not found")
        )
        # Belt-and-suspenders sweep of the serialized JSON: a provider may tuck a base64 blob in metadata.
        cleaned_result = convert_base64_images_to_links(result_json)
        debug_call_data["final_response_size"] = len(cleaned_result)
        debug_call_data["processing_applied"].append("base64_image_conversion")
        _finish_debug("web_extract_tool", debug_call_data)
        return cleaned_result
    except Exception as e:
        return _finish_debug("web_extract_tool", debug_call_data, f"Error extracting content: {e!s}")

def register(ctx):
    ctx.register_tool(name="web_extract",toolset="web",schema=WEB_EXTRACT_SCHEMA,
        handler=lambda args,**kw:web_extract_tool(**args),is_async=True,
        check_fn=check_web_api_key,override=True)
