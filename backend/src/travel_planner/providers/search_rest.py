from datetime import UTC, datetime
from urllib.parse import urlsplit

from travel_planner.providers.errors import ProviderError
from travel_planner.providers.http import ProviderHTTP
from travel_planner.providers.models import SearchSource
from travel_planner.storage.repository import new_id


def source_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and parts.port != 0
        ):
            return value
    except ValueError:
        pass
    return None


def text_value(value, max_length: int = 50000) -> str | None:
    return value.strip()[:max_length] or None if isinstance(value, str) else None


def normalize_sources(provider: str, rows: list) -> list[SearchSource]:
    result, seen = [], set()
    timestamp = datetime.now(UTC)
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = source_url(row.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        text = text_value(row.get("text") or row.get("raw_content") or row.get("content"))
        # Tavily Search content 是摘要，raw_content 才是原文。
        if provider == "tavily" and "raw_content" in row:
            text = text_value(row.get("raw_content"))
        result.append(
            SearchSource(
                id=new_id(),
                provider=provider,
                title=text_value(row.get("title"), 1000) or url,
                url=url,
                snippet=text_value(row.get("snippet") or row.get("summary"), 3000),
                text=text,
                queried_at=timestamp,
                fetched_at=timestamp if text else None,
                published_at=text_value(
                    row.get("publishedDate") or row.get("published_at") or row.get("date"), 100
                ),
            )
        )
    return result


class RestSearch:
    def __init__(self, name: str, key: str, http: ProviderHTTP):
        self.name, self.key, self.http = name, key, http
        self.auth_mode = "key" if key else "anonymous"
        self.base_url = {
            "exa": "https://api.exa.ai",
            "deepseek": "https://api.deepseek.com/v1/tools",
            "tavily": "https://api.tavily.com",
            "anysearch": "https://api.anysearch.com/v1",
        }[name]

    async def _post(self, endpoint: str, payload: dict) -> dict:
        headers = {}
        if self.key:
            headers = (
                {"x-api-key": self.key}
                if self.name == "exa"
                else {"Authorization": "Bearer " + self.key}
            )
        data = await self.http.json(
            self.name, "POST", self.base_url + endpoint, headers=headers, json=payload
        )
        if self.name == "anysearch":
            if data.get("code") != 0:
                raise ProviderError(self.name, "invalid_response")
            data = data.get("data")
            if not isinstance(data, dict):
                raise ProviderError(self.name, "invalid_response")
        return data

    async def search(self, query: str, limit: int) -> list[SearchSource]:
        if self.name == "exa":
            payload = {
                "query": query,
                "numResults": limit,
                "type": "auto",
                "contents": {"text": {"maxCharacters": 12000}},
            }
        elif self.name == "deepseek":
            payload = {"query": query.strip()[:128], "count": limit, "need_content": True}
        else:
            payload = {"query": query, "max_results": limit}
            if self.name == "tavily":
                payload.update(
                    {"search_depth": "basic", "include_raw_content": True, "include_answer": False}
                )
            else:
                payload.update({"zone": "cn", "language": "zh-CN"})
        data = await self._post("/search", payload)
        rows = data.get("results")
        if not isinstance(rows, list):
            raise ProviderError(self.name, "invalid_response")
        if self.name == "tavily":
            rows = [
                {**row, "snippet": row.get("content"), "raw_content": row.get("raw_content")}
                if isinstance(row, dict)
                else row
                for row in rows
            ]
        return normalize_sources(self.name, rows)[:limit]

    async def fetch(self, url: str) -> list[SearchSource]:
        payload = {"ids": [url], "text": {"maxCharacters": 50000}, "maxAgeHours": 0}
        endpoint = "/contents"
        if self.name == "deepseek":
            endpoint, payload = "/fetch", {"url": url}
        elif self.name == "tavily":
            endpoint, payload = "/extract", {"urls": [url], "extract_depth": "basic"}
        elif self.name == "anysearch":
            endpoint, payload = "/extract", {"url": url}
        data = await self._post(endpoint, payload)
        if self.name == "deepseek":
            rows = [{"url": url, "title": data.get("title"), "content": data.get("content")}]
        elif self.name == "anysearch":
            rows = [data]
        else:
            rows = data.get("results")
        if not isinstance(rows, list):
            raise ProviderError(self.name, "invalid_response")
        return [
            source
            for source in normalize_sources(self.name, rows)
            if source.url == url and source.text
        ]
