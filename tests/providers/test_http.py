import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from travel_planner.providers import http as http_module
from travel_planner.providers.errors import ProviderError, retry_time

from .conftest import make_http

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "auth_failed"),
        (403, "auth_failed"),
        (402, "quota_exhausted"),
        (429, "rate_limited"),
        (400, "request_invalid"),
        (422, "request_invalid"),
        (503, "unavailable"),
        (302, "unavailable"),
    ],
)
async def test_http_status_and_errors_do_not_include_raw_secrets(status, reason):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="api_key=generated-secret password=secret",
            headers={"Location": "https://bad.example.com"},
        )

    client, http = make_http(handle)
    async with client:
        with pytest.raises(ProviderError) as error:
            await http.json("exa", "POST", "https://api.exa.ai/search")
    assert error.value.reason == reason
    assert "secret" not in str(error.value.payload())
    assert len(calls) == 1


async def test_wall_clock_timeout_stops_unresponsive_call():
    async def slow(request):
        await asyncio.sleep(2)
        return httpx.Response(200, json={})

    client, http = make_http(slow, timeout=0.01)
    async with client:
        with pytest.raises(ProviderError) as error:
            await http.json("exa", "POST", "https://api.exa.ai/search")
    assert error.value.reason == "timeout"


async def test_response_size_is_bounded(monkeypatch):
    monkeypatch.setattr(http_module, "MAX_RESPONSE_BYTES", 32)
    client, http = make_http(lambda request: httpx.Response(200, json={"body": "x" * 100}))
    async with client:
        with pytest.raises(ProviderError) as error:
            await http.json("exa", "POST", "https://api.exa.ai/search")
    assert error.value.reason == "invalid_response"


async def test_retry_after_seconds_date_and_reset_headers():
    timestamp = datetime(2026, 11, 15, tzinfo=UTC)
    expected = timestamp + timedelta(seconds=120)
    for headers in [
        {"retry-after": "120"},
        {"retry-after": format_datetime(expected)},
        {"x-ratelimit-reset": str(expected.timestamp())},
        {"ratelimit-reset": "120"},
    ]:
        assert retry_time(headers, timestamp) == expected
    for invalid in ["garbage", "NaN", "inf", "-1", "1e1000"]:
        assert retry_time({"retry-after": invalid}, timestamp) is None


async def test_malformed_llm_400_stops_as_request_error():
    client, http = make_http(lambda request: httpx.Response(400, text="invalid parameter"))
    async with client:
        with pytest.raises(ProviderError) as error:
            await http.json("llm", "POST", "https://model.example.com/chat/completions")
    assert error.value.reason == "request_invalid"


async def test_rejected_json_parameter_without_parameter_in_request_is_request_error():
    client, http = make_http(
        lambda request: httpx.Response(
            400, json={"error": {"param": "response_format", "code": "unsupported_parameter"}}
        )
    )
    async with client:
        with pytest.raises(ProviderError) as error:
            await http.json("llm", "POST", "https://model.example.com/chat/completions", json={})
    assert error.value.reason == "request_invalid"
