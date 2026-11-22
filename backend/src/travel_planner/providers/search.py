import asyncio
from datetime import UTC, datetime

from travel_planner.config import Settings
from travel_planner.providers.errors import ProviderError
from travel_planner.providers.models import SearchAttempt, SearchResult, SearchSource
from travel_planner.providers.search_rest import source_url


class SearchEngine:
    """共享限流状态；查询缓存与额度／鉴权失败留在单次任务内。"""

    def __init__(self, settings: Settings, providers: dict, local_fetch=None):
        self.modes = settings.status()["search"]
        self.providers = providers
        self.local_fetch = local_fetch
        self.cooldowns: dict[tuple[str, str], tuple[datetime, datetime | None]] = {}
        self.lock = asyncio.Lock()

    def scope(self):
        return SearchScope(self)


class SearchScope:
    def __init__(self, engine: SearchEngine):
        self.engine = engine
        self.disabled: dict[str, ProviderError] = {}
        self.cache: dict[tuple, SearchResult] = {}
        self.sources: dict[str, SearchSource] = {}

    async def search(self, destination: str, query: str, limit: int = 5) -> SearchResult:
        if not destination.strip() or not query.strip() or not 1 <= limit <= 10:
            raise ProviderError("search", "request_invalid")
        public_query = " ".join((destination + " " + query).split())
        return await self._execute(
            "search", (public_query, limit), ("search", public_query.casefold(), limit)
        )

    async def fetch(self, source: SearchSource) -> SearchResult:
        if source.text and source.fetched_at is not None:
            return SearchResult(status="found", sources=[source.model_copy(deep=True)])
        if not source_url(source.url):
            raise ProviderError("search", "request_invalid")
        return await self._execute("fetch", (source.url,), ("fetch", source.url), source=source)

    async def _execute(
        self, operation: str, args: tuple, cache_key: tuple, source: SearchSource | None = None
    ):
        async with self.engine.lock:
            if cache_key in self.cache:
                result = self.cache[cache_key].model_copy(deep=True)
                if source:
                    result.sources = [
                        row.model_copy(update={"id": source.id, "queried_at": source.queried_at})
                        for row in result.sources
                    ]
                return result
            attempts = []
            if source and self.engine.local_fetch:
                try:
                    local = await self.engine.local_fetch(source)
                    if local and local.text:
                        return self._remember(
                            cache_key,
                            [local],
                            [
                                SearchAttempt(
                                    provider="local", auth_mode="none", outcome="succeeded"
                                )
                            ],
                        )
                except ProviderError as exc:
                    attempts.append(
                        SearchAttempt(
                            provider="local",
                            auth_mode="none",
                            outcome="failed",
                            error=exc.payload(),
                        )
                    )
            for mode in self.engine.modes:
                name, auth = mode["provider"], mode["auth_mode"]
                record = SearchAttempt(provider=name, auth_mode=auth, outcome="skipped")
                if not mode["configured"]:
                    record.error = ProviderError(name, "not_configured").payload()
                    attempts.append(record)
                    continue
                if name in self.disabled:
                    record.error = self.disabled[name].payload()
                    cooldown = self.engine.cooldowns.get((name, auth))
                    if cooldown:
                        record.cooldown_started_at, record.retry_at = cooldown
                    attempts.append(record)
                    continue
                cooldown = self.engine.cooldowns.get((name, auth))
                if cooldown:
                    record.cooldown_started_at, record.retry_at = cooldown
                    if record.retry_at and record.retry_at > datetime.now(UTC):
                        record.error = ProviderError(
                            name, "rate_limited", retry_at=record.retry_at
                        ).payload()
                        attempts.append(record)
                        continue
                try:
                    provider = self.engine.providers.get(name)
                    if provider is None:
                        raise ProviderError(name, "not_configured")
                    rows = await getattr(provider, operation)(*args)
                    if source:
                        # 保持应用已有来源 ID，正文提供方记录实际成功的服务。
                        rows = [
                            row.model_copy(
                                update={"id": source.id, "queried_at": source.queried_at}
                            )
                            for row in rows
                            if row.url == source.url and row.text
                        ]
                        if not rows:
                            raise ProviderError(name, "source_unavailable")
                    elif not rows:
                        raise ProviderError(name, "empty_results")
                    record.outcome = "succeeded"
                    attempts.append(record)
                    return self._remember(cache_key, rows, attempts)
                except ProviderError as exc:
                    if exc.reason == "request_invalid":
                        raise
                    record.outcome, record.error = "failed", exc.payload()
                    if exc.reason == "rate_limited":
                        record.cooldown_started_at = datetime.now(UTC)
                        record.retry_at = exc.retry_at
                        self.engine.cooldowns[(name, auth)] = (
                            record.cooldown_started_at,
                            exc.retry_at,
                        )
                        if exc.retry_at is None:
                            self.disabled[name] = exc
                    elif exc.reason in {"auth_failed", "quota_exhausted"}:
                        self.disabled[name] = exc
                    attempts.append(record)
            result = SearchResult(status="unavailable", sources=[], attempts=attempts)
            self.cache[cache_key] = result
            return result.model_copy(deep=True)

    def _remember(self, cache_key, rows, attempts):
        for source in rows:
            self.sources[source.id] = source.model_copy(deep=True)
        result = SearchResult(status="found", sources=rows, attempts=attempts)
        self.cache[cache_key] = result
        return result.model_copy(deep=True)
