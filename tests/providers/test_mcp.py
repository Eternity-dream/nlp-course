import json
from pathlib import Path

import httpx
import pytest
from mcp.types import CallToolResult, TextContent
from travel_planner.providers.errors import ProviderError
from travel_planner.providers.exa_mcp import ExaMCP, parse_result
from travel_planner.providers.services import Services

from .conftest import configured, make_http

pytestmark = pytest.mark.anyio


class MCPServer:
    def __init__(self, result=None, error=None, *, sse=False, tool_schema=None):
        self.calls = []
        self.result = result or {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Title: 碑林公告\nURL: https://example.com/beilin\n"
                        "Published Date: 2026-11-01\nText: 开放时间请查阅公告。"
                    ),
                }
            ]
        }
        self.error = error
        self.sse = sse
        self.tool_schema = tool_schema

    def __call__(self, request):
        assert "authorization" not in request.headers
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        data = json.loads(request.content)
        self.calls.append(data)
        method = data["method"]
        if "id" not in data:
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture-exa", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "web_search_exa",
                        "inputSchema": self.tool_schema
                        or {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "objective": {"type": "string"},
                                "numResults": {"type": "integer", "maximum": 10},
                            },
                            "required": ["query", "objective"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "web_fetch_exa",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "urls": {"type": "array", "items": {"type": "string"}},
                                "maxCharacters": {"type": "integer"},
                            },
                            "required": ["urls"],
                            "additionalProperties": False,
                        },
                    },
                ]
            }
        elif method == "tools/call":
            if self.error:
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": data["id"], "error": self.error}
                )
            result = self.result
        else:
            raise AssertionError(method)
        payload = {"jsonrpc": "2.0", "id": data["id"], "result": result}
        if self.sse:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="event: message\ndata: " + json.dumps(payload) + "\n\n",
            )
        return httpx.Response(200, json=payload)


@pytest.mark.parametrize("sse", [False, True])
async def test_real_mcp_sdk_initializes_discovers_and_calls_schema(sse):
    server = MCPServer(sse=sse)
    client, _ = make_http(server)
    async with client:
        adapter = ExaMCP(client, 3)
        sources = await adapter.search("西安 碑林", 5)
        assert len(sources) == 1
        assert sources[0].text == "开放时间请查阅公告。"
        assert sources[0].published_at == "2026-11-01"
        assert [call["method"] for call in server.calls] == [
            "initialize",
            "notifications/initialized",
            "tools/list",
            "tools/call",
        ]
        assert server.calls[-1]["params"]["arguments"]["query"] == "西安 碑林"
        assert "objective" in server.calls[-1]["params"]["arguments"]
        fetched = await adapter.fetch("https://example.com/beilin")
        assert fetched[0].text
        assert server.calls[-1]["params"]["arguments"]["urls"] == ["https://example.com/beilin"]


@pytest.mark.parametrize("kind", ["rpc", "tool", "http"])
async def test_mcp_200_errors_and_http429_fall_back_and_share_cooldown(settings, samples, kind):
    rpc_error = {
        "code": -32000,
        "message": "Rate limit exceeded secret",
        "data": {"status": 429, "retryAfter": 120},
    }
    server = MCPServer(
        error=rpc_error if kind == "rpc" else None,
        result={"isError": True, "content": [{"type": "text", "text": json.dumps(rpc_error)}]},
    )
    hosts = []

    def handle(request):
        hosts.append(request.url.host)
        if request.url.host == "mcp.exa.ai":
            if kind == "http":
                return httpx.Response(429, headers={"Retry-After": "120"})
            return server(request)
        return httpx.Response(200, json=samples["search"]["tavily"])

    client, _ = make_http(handle)
    async with client:
        config = configured(settings, exa_api_key="")
        engine = Services(config, client).search
        first = await engine.scope().search("西安", "票价")
        assert first.sources[0].provider == "tavily"
        assert first.attempts[0].error["code"] == "provider_rate_limited"
        assert first.attempts[0].retry_at
        assert "secret" not in first.model_dump_json()
        count = hosts.count("mcp.exa.ai")
        second = await engine.scope().search("杭州", "票价")
        assert second.attempts[0].outcome == "skipped"
        assert hosts.count("mcp.exa.ai") == count


async def test_mcp_schema_mismatch_does_not_send_guessed_arguments():
    server = MCPServer(
        tool_schema={
            "type": "object",
            "required": ["unknown"],
            "properties": {"unknown": {"type": "string"}},
        }
    )
    client, _ = make_http(server)
    async with client:
        with pytest.raises(ProviderError) as error:
            await ExaMCP(client, 3).search("西安", 5)
    assert error.value.reason == "unsupported_tool"
    assert "tools/call" not in [call["method"] for call in server.calls]


async def test_mcp_structured_results_are_mapped_and_deduplicated():
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {"results": [{"url": "https://example.com", "title": "a", "text": "正文"}]}
                ),
            )
        ],
        structuredContent={
            "results": [{"url": "https://example.com", "title": "a", "text": "正文"}]
        },
    )
    assert len(parse_result(result)) == 1


async def test_anonymous_mcp_success_does_not_call_rest_fallback(settings):
    server = MCPServer()
    client, _ = make_http(server)
    async with client:
        engine = Services(configured(settings, exa_api_key=""), client).search
        result = await engine.scope().search("西安", "碑林")
        assert result.status == "found"
        assert len(result.attempts) == 1
        assert result.attempts[0].auth_mode == "anonymous"


async def test_recorded_anonymous_fetch_maps_actual_markdown_response():
    path = Path(__file__).parents[1] / "fixtures" / "providers" / "exa_mcp_live.json"
    record = json.loads(path.read_text())
    result = CallToolResult.model_validate(record["result"])
    sources = parse_result(result, fetch_url=record["arguments"]["urls"][0])
    assert len(sources) == 1
    assert sources[0].title == "西安碑林博物馆"
    assert "开放时间" in sources[0].text
    assert sources[0].fetched_at is not None
    assert sources[0].status == "unverified"


async def test_fetch_target_403_is_not_provider_auth_failure():
    result = CallToolResult(
        isError=True,
        content=[
            TextContent(type="text", text="target URL returned HTTP 403 while fetching webpage")
        ],
    )
    with pytest.raises(ProviderError) as error:
        parse_result(result, fetch_url="https://example.com")
    assert error.value.reason == "source_unavailable"
