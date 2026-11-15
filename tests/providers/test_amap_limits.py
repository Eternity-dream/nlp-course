import asyncio

import httpx
import pytest
from travel_planner.providers.amap import Amap
from travel_planner.providers.errors import ProviderError

from .conftest import make_http

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self):
        self.current = 0.0
        self.waits = []

    def now(self):
        return self.current

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.current += seconds


async def test_qps_retry_recovers_and_paces_concurrent_following_calls(samples):
    clock, arrivals = Clock(), []

    def handle(request):
        arrivals.append(clock.now())
        if len(arrivals) == 1:
            return httpx.Response(200, json={"status": "0", "infocode": "10021"})
        return httpx.Response(200, json=samples["amap"])

    client, http = make_http(handle)
    async with client:
        amap = Amap("test-key", http, clock=clock.now, sleep=clock.sleep)
        assert await amap.places("西安", "碑林")
        await asyncio.gather(amap.places("西安", "公园"), amap.places("西安", "餐馆"))
    assert len(arrivals) == 4
    assert all(
        after - before >= 1.09 for before, after in zip(arrivals, arrivals[1:], strict=False)
    )


@pytest.mark.parametrize("code", ["10014", "10019", "10020", "10021", "10029"])
async def test_persistent_qps_limit_has_bounded_attempts_and_reports_cooldown(code):
    clock, arrivals = Clock(), []

    def handle(request):
        arrivals.append(clock.now())
        return httpx.Response(200, json={"status": "0", "infocode": code, "info": "secret"})

    client, http = make_http(handle)
    async with client:
        with pytest.raises(ProviderError) as error:
            await Amap("test-key", http, clock=clock.now, sleep=clock.sleep).places("西安", "碑林")
    assert error.value.reason == "rate_limited"
    assert error.value.retry_at is not None
    assert len(arrivals) == 3
    assert clock.waits == pytest.approx([1.1, 2.0])
    assert "secret" not in str(error.value.payload())


async def test_http_429_observes_retry_after_before_retry(samples):
    clock, arrivals = Clock(), []

    def handle(request):
        arrivals.append(clock.now())
        if len(arrivals) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json=samples["amap"])

    client, http = make_http(handle)
    async with client:
        assert await Amap("test-key", http, clock=clock.now, sleep=clock.sleep).places(
            "西安", "碑林"
        )
    assert len(arrivals) == 2 and arrivals[1] >= 2.9


async def test_long_cooldown_returns_without_automatic_retry():
    clock, calls = Clock(), []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "90"})

    client, http = make_http(handle)
    async with client:
        amap = Amap("test-key", http, clock=clock.now, sleep=clock.sleep)
        with pytest.raises(ProviderError) as error:
            await amap.places("西安", "碑林")
        with pytest.raises(ProviderError):
            await amap.places("西安", "公园")
    assert len(calls) == 1 and not clock.waits
    assert error.value.retry_at is not None


async def test_minute_limit_reports_cooldown_without_burst_retries():
    clock, calls = Clock(), []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "0", "infocode": "10004"})

    client, http = make_http(handle)
    async with client:
        with pytest.raises(ProviderError) as error:
            await Amap("test-key", http, clock=clock.now, sleep=clock.sleep).places("西安", "碑林")
    assert len(calls) == 1 and not clock.waits
    assert error.value.reason == "rate_limited" and error.value.retry_at is not None


@pytest.mark.parametrize("code", ["10001", "10002", "10003", "10044", "40000", "20000"])
async def test_auth_quota_and_invalid_parameters_are_not_retried(code):
    clock, calls = Clock(), []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "0", "infocode": code})

    client, http = make_http(handle)
    async with client:
        with pytest.raises(ProviderError):
            await Amap("test-key", http, clock=clock.now, sleep=clock.sleep).places("西安", "碑林")
    assert len(calls) == 1 and not clock.waits
