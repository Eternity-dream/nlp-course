import asyncio
import math
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from travel_planner.providers.errors import ProviderError
from travel_planner.providers.http import ProviderHTTP
from travel_planner.providers.models import Place, Route
from travel_planner.providers.search_rest import text_value


def number(value) -> int | None:
    try:
        parsed = Decimal(str(value))
        if parsed.is_finite() and parsed >= 0:
            return math.ceil(parsed)
    except (InvalidOperation, ValueError, OverflowError):
        pass
    return None


def coordinate(value) -> tuple[float | None, float | None]:
    try:
        x, y = value.split(",")
        longitude, latitude = float(x), float(y)
        if (
            math.isfinite(longitude)
            and math.isfinite(latitude)
            and (-180 <= longitude <= 180 and -90 <= latitude <= 90)
        ):
            return longitude, latitude
    except (ValueError, AttributeError):
        pass
    return None, None


def money_cents(value) -> int | None:
    try:
        amount = Decimal(str(value))
        if amount.is_finite() and 0 <= amount <= 10_000_000:
            return math.ceil(amount * 100)
    except (InvalidOperation, ValueError, OverflowError):
        pass
    return None


class Amap:
    def __init__(self, key: str, http: ProviderHTTP, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.key, self.http = key, http
        self._lock = asyncio.Lock()
        self._clock, self._sleep = clock, sleep
        self._interval, self._next_request = 0.0, 0.0

    async def _get(self, path: str, params: dict) -> dict:
        if not self.key.strip():
            raise ProviderError("amap", "not_configured")
        async with self._lock:
            for attempt in range(3):
                delay = self._next_request - self._clock()
                if delay > 30:
                    raise ProviderError(
                        "amap",
                        "rate_limited",
                        retry_at=datetime.now(UTC) + timedelta(seconds=delay),
                    )
                if delay > 0:
                    await self._sleep(delay)
                try:
                    return await self._request(path, params)
                except ProviderError as exc:
                    if exc.reason != "rate_limited":
                        raise
                    # 首次收到限流后对地点、路线及显式检查共用节流状态。
                    self._interval = 1.1
                    delay = max(self._interval, 2**attempt)
                    if exc.retry_at:
                        delay = max(delay, (exc.retry_at - datetime.now(UTC)).total_seconds())
                    self._next_request = self._clock() + delay
                    if attempt == 2 or delay > 30:
                        raise ProviderError(
                            "amap",
                            "rate_limited",
                            retry_at=datetime.now(UTC) + timedelta(seconds=delay),
                        ) from exc
                finally:
                    self._next_request = max(self._next_request, self._clock() + self._interval)

    async def _request(self, path: str, params: dict) -> dict:
        result = await self.http.json(
            "amap",
            "GET",
            "https://restapi.amap.com" + path,
            params={"key": self.key, "output": "json", **params},
        )
        if str(result.get("status")) != "1":
            code = str(result.get("infocode"))
            if code in {
                "10001",
                "10002",
                "10005",
                "10006",
                "10007",
                "10008",
                "10009",
                "10012",
                "10013",
                "10026",
                "10041",
                "40002",
            }:
                reason = "auth_failed"
            elif code in {"10003", "10044", "10045", "40000", "40003"}:
                reason = "quota_exhausted"
            elif code in {"10004", "10014", "10015", "10019", "10020", "10021", "10029"}:
                reason = "rate_limited"
            elif code.startswith("200"):
                reason = "request_invalid"
            else:
                reason = "unavailable"
            raise ProviderError(
                "amap",
                reason,
                retry_at=datetime.now(UTC) + timedelta(seconds=60) if code == "10004" else None,
            )
        return result

    async def places(self, destination: str, keywords: str, limit: int = 5) -> list[Place]:
        if (
            not destination.strip()
            or not keywords.strip()
            or len(keywords) > 80
            or not 1 <= limit <= 25
        ):
            raise ProviderError("amap", "request_invalid")
        data = await self._get(
            "/v5/place/text",
            {
                "region": destination,
                "city_limit": "true",
                "keywords": keywords,
                "show_fields": "business",
                "page_size": limit,
                "page_num": 1,
            },
        )
        if not isinstance(data.get("pois"), list):
            raise ProviderError("amap", "invalid_response")
        result = []
        timestamp = datetime.now(UTC)
        for poi in data["pois"]:
            if (
                not isinstance(poi, dict)
                or not text_value(poi.get("id"))
                or not text_value(poi.get("name"))
            ):
                continue
            longitude, latitude = coordinate(poi.get("location"))
            business = poi.get("business")
            business = business if isinstance(business, dict) else {}
            result.append(
                Place(
                    id="amap:" + poi["id"],
                    provider_id=poi["id"],
                    name=poi["name"],
                    category=text_value(poi.get("type")),
                    category_code=text_value(poi.get("typecode")),
                    address=text_value(poi.get("address")),
                    longitude=longitude,
                    latitude=latitude,
                    city=text_value(poi.get("cityname")),
                    citycode=text_value(poi.get("citycode")),
                    adcode=text_value(poi.get("adcode")),
                    queried_at=timestamp,
                    opening_hours_text=text_value(
                        business.get("opentime_week") or business.get("opentime_today")
                    ),
                    cost_text=text_value(business.get("cost")),
                )
            )
        return result[:limit]

    async def route(self, origin: Place, destination: Place, mode: str = "walking") -> Route:
        if mode not in {"walking", "transit", "driving"}:
            raise ProviderError("amap", "request_invalid")
        unknown = Route(
            origin_place_id=origin.id,
            destination_place_id=destination.id,
            mode=mode,
            duration_seconds=None,
            distance_meters=None,
            queried_at=datetime.now(UTC),
            status="unknown",
        )
        for place in (origin, destination):
            if (
                place.coordinate_system != "GCJ-02"
                or place.longitude is None
                or place.latitude is None
                or coordinate(f"{place.longitude},{place.latitude}")[0] is None
            ):
                return unknown
        if not origin.citycode or origin.citycode != destination.citycode:
            return unknown
        if origin.id == destination.id:
            # 同一锚点的自由用餐/分段参观没有点间位移，不请求绕行路线。
            return unknown.model_copy(
                update={
                    "mode": "walking",
                    "duration_seconds": 0,
                    "distance_meters": 0,
                    "fare_cents": 0,
                    "status": "estimated",
                }
            )
        params = {
            "origin": f"{origin.longitude:.6f},{origin.latitude:.6f}",
            "destination": f"{destination.longitude:.6f},{destination.latitude:.6f}",
            "show_fields": "cost",
        }
        path = "/v5/direction/" + mode
        if mode == "transit":
            path = "/v5/direction/transit/integrated"
            params.update(
                {
                    "city1": origin.citycode,
                    "city2": destination.citycode,
                    "originpoi": origin.provider_id,
                    "destinationpoi": destination.provider_id,
                }
            )
        else:
            params.update(
                {"origin_id": origin.provider_id, "destination_id": destination.provider_id}
            )
        data = await self._get(path, params)
        route = data.get("route")
        if not isinstance(route, dict):
            raise ProviderError("amap", "invalid_response")
        paths = route.get("transits" if mode == "transit" else "paths", [])
        if not isinstance(paths, list):
            raise ProviderError("amap", "invalid_response")
        for path in paths:
            if not isinstance(path, dict):
                continue
            cost = path.get("cost")
            duration = number(cost.get("duration")) if isinstance(cost, dict) else None
            if duration is not None:
                fare = 0 if mode == "walking" else None
                basis = "per_person"
                if mode == "transit":
                    fare = money_cents(cost.get("transit_fee"))
                elif mode == "driving":
                    fare = money_cents(route.get("taxi_cost") or cost.get("taxi_fee"))
                    basis = "per_group"
                return unknown.model_copy(
                    update={
                        "duration_seconds": duration,
                        "distance_meters": number(path.get("distance")),
                        "status": "estimated",
                        "queried_at": datetime.now(UTC),
                        "fare_cents": fare,
                        "fare_basis": basis,
                    }
                )
        return unknown
