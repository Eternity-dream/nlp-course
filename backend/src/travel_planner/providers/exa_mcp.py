import asyncio
import json
import re
from datetime import timedelta

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamable_http_client

from travel_planner.providers.errors import ProviderError, http_error
from travel_planner.providers.models import SearchSource
from travel_planner.providers.search_rest import normalize_sources

EXA_MCP_URL = "https://mcp.exa.ai/mcp"


def mcp_error(value) -> ProviderError:
    """识别 JSON-RPC 和工具错误内的状态；原文仅在函数内分类。"""
    data = value if isinstance(value, dict) else {}
    if isinstance(data.get("error"), dict):
        data = data["error"]
    message = str(data.get("message", value)).lower()
    status = data.get("status") or data.get("statusCode") or data.get("code")
    extra = data.get("data")
    if isinstance(extra, dict):
        status = extra.get("status") or extra.get("statusCode") or status
    reason = None
    if status in {-32600, -32602, 400, 422, "400", "422"}:
        reason = "request_invalid"
    for code, name, markers in [
        (429, "rate_limited", ("rate limit", "too many requests")),
        (402, "quota_exhausted", ("quota exceeded", "quota exhausted", "credit balance")),
        (401, "auth_failed", ("unauthorized", "invalid api key")),
        (403, "auth_failed", ("permission denied", "forbidden")),
    ]:
        if (
            status in {code, str(code)}
            or re.search(rf"\b{code}\b", message)
            or any(marker in message for marker in markers)
        ):
            reason = name
            break
    error = ProviderError("exa", reason or "unavailable")
    if error.reason == "rate_limited":
        from datetime import UTC, datetime

        from travel_planner.providers.errors import retry_time

        headers = data.get("headers", {})
        if isinstance(extra, dict):
            headers = extra.get("headers", headers)
            if "retryAfter" in extra:
                headers = {**headers, "retry-after": extra["retryAfter"]}
        if isinstance(headers, dict):
            error.retry_at = retry_time(
                {str(k).lower(): v for k, v in headers.items()}, datetime.now(UTC)
            )
    return error


def classify_exception(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            if isinstance(nested, Exception):
                error = classify_exception(nested)
                if error.reason != "unavailable":
                    return error
    if isinstance(exc, httpx.HTTPStatusError):
        return http_error("exa", exc.response.status_code, dict(exc.response.headers))
    if isinstance(exc, McpError):
        return mcp_error(exc.error.model_dump())
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ProviderError("exa", "timeout")
    return ProviderError("exa", "unavailable")


def parse_result(result, *, fetch_url: str | None = None) -> list[SearchSource]:
    if result.isError:
        chunks = [block.text for block in result.content if block.type == "text"]
        combined = "\n".join(chunks)
        try:
            data = json.loads(combined)
        except ValueError:
            data = combined
        error = mcp_error(data)
        if (
            fetch_url
            and error.reason == "auth_failed"
            and any(
                marker in combined.lower()
                for marker in ("target url", "webpage", "crawl", "fetch url")
            )
        ):
            raise ProviderError("exa", "source_unavailable")
        raise error
    rows = []
    if isinstance(result.structuredContent, dict):
        structured = result.structuredContent
        rows.extend(
            structured.get("results", [])
            if isinstance(structured.get("results"), list)
            else [structured]
        )
    for block in result.content:
        if block.type != "text":
            continue
        try:
            value = json.loads(block.text)
            if isinstance(value, dict):
                value = value.get("results", [value])
            if isinstance(value, list):
                rows.extend(value)
                continue
        except ValueError:
            pass
        # Exa 文本工具以 Title / URL / Published Date / Text 返回来源。
        for section in re.split(r"(?m)(?=^Title: )", block.text):
            match = re.search(r"(?m)^URL: (https?://\S+)\s*$", section)
            if not match:
                continue
            title = re.search(r"(?m)^(?:Title: |# )(.+)$", section)
            text = re.search(r"(?ms)^Text:\s*\n?(.*)", section)
            if fetch_url and text is None:
                # 抓取工具也可能直接在 URL 行后返回 Markdown 正文。
                tail = section[match.end() :].strip()
                if tail:
                    rows.append(
                        {"url": match[1], "text": tail, "title": title[1] if title else match[1]}
                    )
                    continue
            published = re.search(r"(?m)^Published Date: (.+)$", section)
            rows.append(
                {
                    "title": title[1] if title else match[1],
                    "url": match[1],
                    "text": text[1] if text else None,
                    "publishedDate": published[1] if published else None,
                }
            )
    sources = normalize_sources("exa", rows)
    if fetch_url:
        return [source for source in sources if source.url == fetch_url and source.text]
    return sources


class ExaMCP:
    name = "exa"
    auth_mode = "anonymous"

    def __init__(self, client: httpx.AsyncClient, timeout: float):
        self.client, self.timeout = client, timeout

    async def search(self, query: str, limit: int) -> list[SearchSource]:
        return await self._call("web_search_exa", query=query, limit=limit)

    async def fetch(self, url: str) -> list[SearchSource]:
        return await self._call("web_fetch_exa", url=url)

    async def _call(self, name: str, *, query=None, limit=None, url=None):
        try:
            async with asyncio.timeout(self.timeout):
                async with streamable_http_client(EXA_MCP_URL, http_client=self.client) as streams:
                    async with ClientSession(
                        streams[0], streams[1], read_timeout_seconds=timedelta(seconds=self.timeout)
                    ) as s:
                        await s.initialize()
                        tools, cursor = [], None
                        for _ in range(10):
                            page = await s.list_tools(cursor=cursor)
                            tools.extend(page.tools)
                            cursor = page.nextCursor
                            if not cursor:
                                break
                        tool = next((tool for tool in tools if tool.name == name), None)
                        if tool is None:
                            raise ProviderError("exa", "unsupported_tool")
                        props = tool.inputSchema.get("properties", {})
                        if (query is not None and "query" not in props) or (
                            url is not None and not {"url", "urls"}.intersection(props)
                        ):
                            raise ProviderError("exa", "unsupported_tool")
                        candidates = (
                            {
                                "query": query,
                                "numResults": limit,
                                "objective": "查找目的地的官方旅行信息与可核对的公开来源。",
                            }
                            if query is not None
                            else {"url": url, "urls": [url], "maxCharacters": 50000}
                        )
                        arguments = {
                            key: value for key, value in candidates.items() if key in props
                        }
                        try:
                            Draft202012Validator.check_schema(tool.inputSchema)
                            Draft202012Validator(tool.inputSchema).validate(arguments)
                        except (SchemaError, ValidationError):
                            raise ProviderError("exa", "unsupported_tool") from None
                        result = await s.call_tool(name, arguments)
                        return parse_result(result, fetch_url=url)
        except Exception as exc:
            raise classify_exception(exc) from None
