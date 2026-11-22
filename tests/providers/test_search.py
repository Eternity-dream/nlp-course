import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from travel_planner.providers.errors import ProviderError
from travel_planner.providers.models import SearchSource
from travel_planner.providers.search import SearchEngine
from travel_planner.providers.search_rest import RestSearch
from travel_planner.providers.services import Services

from .conftest import configured, make_http

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("name", ["exa", "tavily", "anysearch"])
async def test_rest_mapping_and_auth(settings, samples, name):
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["query"] == "杭州 博物馆"
        if name == "exa":
            assert request.headers["x-api-key"] == "search-key"
            assert body["numResults"] == 5
        else:
            assert request.headers["authorization"] == "Bearer search-key"
            assert body["max_results"] == 5
        return httpx.Response(200, json=samples["search"][name])

    client, http = make_http(handle)
    async with client:
        sources = await RestSearch(name, "search-key", http).search("杭州 博物馆", 5)
        assert len(sources) == 1
        assert sources[0].provider == name
        assert sources[0].queried_at.tzinfo
        assert sources[0].status == "unverified"
        if name == "tavily":
            assert sources[0].snippet == "搜索摘要"
            assert sources[0].text == "网页正文：西湖步行路线。"


async def test_deepseek_search_maps_content_and_truncates_unicode_query(samples):
    query = "杭州🏛" * 50

    def handle(request):
        assert request.method == "POST"
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        assert request.headers["authorization"] == "Bearer search-key"
        assert json.loads(request.content) == {
            "query": query[:128],
            "count": 2,
            "need_content": True,
        }
        return httpx.Response(200, json=samples["search"]["deepseek"])

    client, http = make_http(handle)
    async with client:
        article, preview = await RestSearch("deepseek", "search-key", http).search(f"  {query} ", 2)
    assert article.provider == "deepseek"
    assert article.title == "博物馆参观公告"
    assert article.snippet == "开放时间公告"
    assert article.published_at == "2026-11-01T08:00:00Z"
    assert article.text == "博物馆每周二至周日开放。"
    assert article.fetched_at is not None
    assert preview.snippet == "预约需提前办理。"
    assert preview.text is preview.fetched_at is preview.published_at is None


async def test_first_success_stops_and_identical_query_cached_per_task(settings, samples):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.host == "api.exa.ai"
        return httpx.Response(200, json=samples["search"]["exa"])

    client, _ = make_http(handle)
    async with client:
        engine = Services(configured(settings), client).search
        scope = engine.scope()
        first = await scope.search("西安", "碑林")
        second = await scope.search("西安", " 碑林 ")
        assert first == second
        assert len(first.attempts) == 1
        second.sources.clear()
        assert (await scope.search("西安", "碑林")).sources
        await engine.scope().search("西安", "碑林")
        await scope.search("杭州", "碑林")
    assert len(calls) == 3
    assert json.loads(calls[-1].content)["query"] == "杭州 碑林"


@pytest.mark.parametrize("status", [401, 402, 403])
async def test_auth_quota_disable_for_task_and_do_not_switch_to_anonymous(
    settings, samples, status
):
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if request.url.host == "api.exa.ai":
            return httpx.Response(status, text="password=hidden api_key=hidden")
        assert request.url.host == "api.deepseek.com"
        return httpx.Response(200, json=samples["search"]["deepseek"])

    client, _ = make_http(handle)
    async with client:
        engine = Services(configured(settings), client).search
        scope = engine.scope()
        result = await scope.search("西安", "博物馆")
        assert result.sources[0].provider == "deepseek"
        assert "hidden" not in result.model_dump_json()
        assert result.attempts[0].auth_mode == "key"
        await scope.search("西安", "票价")
        assert calls == ["api.exa.ai", "api.deepseek.com", "api.deepseek.com"]
        await engine.scope().search("西安", "新的任务")
        assert calls[-2:] == ["api.exa.ai", "api.deepseek.com"]


async def test_retry_after_shared_between_tasks_search_and_fetch(settings, samples):
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if request.url.host == "api.exa.ai":
            return httpx.Response(429, headers={"Retry-After": "120"})
        if request.url.path == "/extract":
            return httpx.Response(
                200, json={"results": [{"url": "https://example.com/xihu", "raw_content": "正文"}]}
            )
        return httpx.Response(200, json=samples["search"]["tavily"])

    client, _ = make_http(handle)
    async with client:
        engine = Services(configured(settings, deepseek_api_key=""), client).search
        engine.local_fetch = None
        first = await engine.scope().search("西安", "开放时间")
        assert first.attempts[0].retry_at > datetime.now(UTC)
        assert [(a.provider, a.outcome) for a in first.attempts] == [
            ("exa", "failed"),
            ("deepseek", "skipped"),
            ("tavily", "succeeded"),
        ]
        assert first.attempts[1].auth_mode == "disabled"
        second = engine.scope()
        result = await second.search("杭州", "博物馆")
        assert result.attempts[0].outcome == "skipped"
        source = result.sources[0].model_copy(update={"text": None, "fetched_at": None})
        fetched = await second.fetch(source)
        assert fetched.sources[0].id == source.id
        assert fetched.sources[0].text == "正文"
        assert calls.count("api.exa.ai") == 1
        engine.cooldowns[("exa", "key")] = (
            datetime.now(UTC),
            datetime.now(UTC) - timedelta(seconds=1),
        )
        await engine.scope().search("西安", "重试")
        assert calls.count("api.exa.ai") == 2


@pytest.mark.parametrize(
    "status,code", [(402, "provider_quota_exhausted"), (429, "provider_rate_limited")]
)
async def test_deepseek_quota_and_cooldown_fall_back_to_tavily(settings, samples, status, code):
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if request.url.host == "api.exa.ai":
            return httpx.Response(503)
        if request.url.host == "api.deepseek.com":
            return httpx.Response(status, headers={"Retry-After": "120"} if status == 429 else {})
        assert request.url.host == "api.tavily.com"
        return httpx.Response(200, json=samples["search"]["tavily"])

    client, _ = make_http(handle)
    async with client:
        engine = Services(configured(settings), client).search
        scope = engine.scope()
        first = await scope.search("西安", "票价")
        assert [(a.provider, a.outcome) for a in first.attempts] == [
            ("exa", "failed"),
            ("deepseek", "failed"),
            ("tavily", "succeeded"),
        ]
        assert first.attempts[1].error["code"] == code
        if status == 429:
            assert first.attempts[1].retry_at > datetime.now(UTC)
            scope = engine.scope()
        second = await scope.search("杭州", "开放时间")
        assert second.sources[0].provider == "tavily"
        assert second.attempts[1].outcome == "skipped"
        assert second.attempts[1].error["code"] == code
        assert calls.count("api.deepseek.com") == 1
        if status == 402:
            await engine.scope().search("西安", "新任务")
            assert calls.count("api.deepseek.com") == 2


async def test_no_retry_after_skips_remaining_task_then_next_task_retries(settings, samples):
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if request.url.host == "api.exa.ai":
            return httpx.Response(429)
        return httpx.Response(200, json=samples["search"]["deepseek"])

    client, _ = make_http(handle)
    async with client:
        engine = Services(configured(settings), client).search
        scope = engine.scope()
        first = await scope.search("西安", "开放时间")
        assert first.attempts[0].cooldown_started_at
        await scope.search("西安", "票价")
        assert calls.count("api.exa.ai") == 1
        await engine.scope().search("西安", "新任务")
        assert calls.count("api.exa.ai") == 2


async def test_skip_missing_deepseek_and_tavily_then_anysearch_anonymous(settings, samples):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.host == "api.exa.ai":
            return httpx.Response(503)
        assert request.url.host == "api.anysearch.com"
        assert "authorization" not in request.headers
        return httpx.Response(200, json=samples["search"]["anysearch"])

    client, _ = make_http(handle)
    async with client:
        config = configured(settings, deepseek_api_key="", tavily_api_key="", anysearch_api_key="")
        result = await Services(config, client).search.scope().search("杭州", "开放时间")
    assert result.sources[0].provider == "anysearch"
    assert [(a.provider, a.auth_mode, a.outcome) for a in result.attempts] == [
        ("exa", "key", "failed"),
        ("deepseek", "disabled", "skipped"),
        ("tavily", "disabled", "skipped"),
        ("anysearch", "anonymous", "succeeded"),
    ]
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["empty", "invalid_url", "invalid_json", "timeout", "5xx"])
async def test_all_failed_returns_reasons_and_keeps_prior_sources(settings, samples, failure):
    succeeded = True

    def handle(request):
        if succeeded:
            return httpx.Response(200, json=samples["search"]["exa"])
        if failure == "timeout":
            raise httpx.ReadTimeout("secret")
        if failure == "invalid_json":
            return httpx.Response(200, text="<html>secret</html>")
        if failure == "5xx":
            return httpx.Response(503)
        rows = [] if failure == "empty" else [{"title": "x", "url": "javascript:bad"}]
        body = {"results": rows}
        if request.url.host == "api.anysearch.com":
            body = {"code": 0, "data": body}
        return httpx.Response(200, json=body)

    client, _ = make_http(handle)
    async with client:
        scope = Services(configured(settings), client).search.scope()
        first = await scope.search("西安", "有效来源")
        succeeded = False
        result = await scope.search("西安", "不可用来源")
        assert result.status == "unavailable"
        assert [a.provider for a in result.attempts] == ["exa", "deepseek", "tavily", "anysearch"]
        assert all(attempt.error for attempt in result.attempts)
        assert first.sources[0].id in scope.sources
        assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("status", [400, 422])
async def test_request_errors_stop_fallback(settings, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "parameter wrong"})

    client, _ = make_http(handle)
    async with client:
        with pytest.raises(ProviderError) as error:
            await Services(configured(settings), client).search.scope().search("西安", "博物馆")
    assert error.value.reason == "request_invalid"
    assert len(calls) == 1


async def test_extract_failed_does_not_disable_provider(settings, samples):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("extract"):
            return httpx.Response(422, json={"error_code": "extract_failed"})
        return httpx.Response(200, json=samples["search"]["anysearch"])

    client, _ = make_http(handle)
    async with client:
        config = configured(settings, search_providers="anysearch")
        engine = Services(config, client).search
        engine.local_fetch = None
        scope = engine.scope()
        source = SearchSource(
            id="s1",
            provider="anysearch",
            title="t",
            url="https://example.com",
            queried_at=datetime.now(UTC),
        )
        assert (await scope.fetch(source)).status == "unavailable"
        assert (await scope.search("西安", "博物馆")).status == "found"
        assert not scope.disabled


async def test_cached_body_and_local_extraction_precede_provider_fetch(settings):
    async def local(source):
        return source.model_copy(update={"text": "本地正文", "fetched_at": datetime.now(UTC)})

    engine = SearchEngine(settings, {}, local_fetch=local)
    scope = engine.scope()
    source = SearchSource(
        id="s1", provider="exa", title="t", url="https://example.com", queried_at=datetime.now(UTC)
    )
    result = await scope.fetch(source)
    assert result.sources[0].text == "本地正文"
    assert len(result.attempts) == 1 and result.attempts[0].provider == "local"
    assert (await scope.fetch(result.sources[0])).attempts == []


@pytest.mark.parametrize("name", ["exa", "deepseek", "tavily", "anysearch"])
async def test_rest_fetch_requests_and_body_mapping(name):
    url = "https://example.com/museum"

    def handle(request):
        body = json.loads(request.content)
        if name == "exa":
            assert request.url.path == "/contents"
            assert body["ids"] == [url]
            assert body["maxAgeHours"] == 0
            result = {"results": [{"url": url, "text": "正文"}]}
        elif name == "deepseek":
            assert request.method == "POST"
            assert str(request.url) == "https://api.deepseek.com/v1/tools/fetch"
            assert request.headers["authorization"] == "Bearer key"
            assert body == {"url": url}
            result = {"title": "博物馆", "content": "正文"}
        elif name == "tavily":
            assert request.url.path == "/extract"
            assert body["urls"] == [url]
            result = {"results": [{"url": url, "raw_content": "正文"}]}
        else:
            assert request.url.path == "/v1/extract"
            assert body == {"url": url}
            result = {"code": 0, "data": {"url": url, "content": "正文"}}
        return httpx.Response(200, json=result)

    client, http = make_http(handle)
    async with client:
        result = await RestSearch(name, "key", http).fetch(url)
        assert len(result) == 1
        assert result[0].text == "正文"
        assert result[0].fetched_at
        assert result[0].url == url
        if name == "deepseek":
            assert result[0].title == "博物馆"


async def test_title_and_snippet_do_not_count_as_fetched_body():
    client, http = make_http(
        lambda request: httpx.Response(
            200, json={"results": [{"title": "t", "url": "https://example.com", "content": "摘要"}]}
        )
    )
    async with client:
        result = await RestSearch("tavily", "key", http).search("西安", 5)
        assert result[0].text is None
        assert result[0].fetched_at is None
        assert result[0].snippet == "摘要"


async def test_fetch_failure_falls_back_and_does_not_disable_search(settings, samples):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.host == "api.exa.ai" and request.url.path == "/contents":
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "statuses": [
                        {
                            "id": "https://example.com",
                            "status": "error",
                            "error": {"httpStatusCode": 403},
                        }
                    ],
                },
            )
        if request.url.host == "api.deepseek.com":
            assert request.url.path == "/v1/tools/fetch"
            return httpx.Response(200, json={"title": "博物馆", "content": ""})
        if request.url.host == "api.tavily.com":
            return httpx.Response(
                200, json={"results": [{"url": "https://example.com", "raw_content": "正文"}]}
            )
        return httpx.Response(200, json=samples["search"]["exa"])

    client, _ = make_http(handle)
    async with client:
        engine = Services(configured(settings), client).search
        engine.local_fetch = None
        scope = engine.scope()
        source = SearchSource(
            id="s1",
            provider="exa",
            title="t",
            url="https://example.com",
            queried_at=datetime.now(UTC),
        )
        result = await scope.fetch(source)
        assert [(a.provider, a.outcome) for a in result.attempts] == [
            ("exa", "failed"),
            ("deepseek", "failed"),
            ("tavily", "succeeded"),
        ]
        assert result.attempts[1].error["code"] == "provider_source_unavailable"
        assert result.sources[0].provider == "tavily"
        assert result.sources[0].id == "s1"
        assert not scope.disabled
        second = await scope.fetch(source.model_copy(update={"id": "s2"}))
        assert second.sources[0].id == "s2"
        assert len(calls) == 3
        assert (await scope.search("西安", "博物馆")).sources[0].provider == "exa"
