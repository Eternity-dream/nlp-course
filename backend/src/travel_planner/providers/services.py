from datetime import UTC, datetime

import httpx
from pydantic import StrictBool

from travel_planner.config import Settings
from travel_planner.domain.models import Model
from travel_planner.providers.amap import Amap
from travel_planner.providers.errors import ProviderError
from travel_planner.providers.exa_mcp import ExaMCP
from travel_planner.providers.http import ProviderHTTP
from travel_planner.providers.llm import ChatModel
from travel_planner.providers.local_fetch import fetch_local
from travel_planner.providers.search import SearchEngine
from travel_planner.providers.search_rest import RestSearch


class CheckOutput(Model):
    ok: StrictBool


class Services:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.http = ProviderHTTP(client, settings.provider_timeout_seconds)
        self.model = ChatModel(settings, self.http)
        self.amap = Amap(settings.amap_api_key.get_secret_value(), self.http)
        providers = {}
        for mode in settings.status()["search"]:
            name = mode["provider"]
            if name == "exa" and mode["auth_mode"] == "anonymous":
                providers[name] = ExaMCP(client, settings.provider_timeout_seconds)
            elif mode["configured"]:
                key = getattr(settings, name + "_api_key").get_secret_value()
                providers[name] = RestSearch(name, key, self.http)
        self.search = SearchEngine(settings, providers, local_fetch=fetch_local)

    async def check(self, service: str, destination: str) -> dict:
        result = {
            "service": service,
            "checked_at": datetime.now(UTC).isoformat(),
            "success": False,
            "details": {},
        }
        try:
            if service == "llm":
                output = await self.model.generate(
                    [{"role": "user", "content": "连通性检查，返回 ok 为 true。"}], CheckOutput
                )
                result["success"] = output.ok
            elif service == "search":
                response = await self.search.scope().search(destination, "博物馆 开放时间 官方", 3)
                result["success"] = response.status == "found"
                result["details"] = {
                    "source_count": len(response.sources),
                    "attempts": [attempt.model_dump(mode="json") for attempt in response.attempts],
                }
            elif service == "amap":
                places = await self.amap.places(destination, "博物馆", 3)
                result["success"] = bool(places)
                result["details"] = {"place_count": len(places)}
            else:
                raise ProviderError("config", "request_invalid")
        except ProviderError as exc:
            result["details"] = {"error": exc.payload()}
        return result
