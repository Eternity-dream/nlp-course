from datetime import datetime
from typing import Literal

from pydantic import Field

from travel_planner.domain.models import Model


class SearchSource(Model):
    id: str
    provider: str
    title: str
    url: str
    snippet: str | None = None
    text: str | None = None
    queried_at: datetime
    fetched_at: datetime | None = None
    published_at: str | None = None
    # 搜索来源进入取证流程后，再关联到具体事实及适用日期。
    status: Literal["unverified"] = "unverified"


class SearchAttempt(Model):
    provider: str
    auth_mode: str
    outcome: str
    error: dict | None = None
    retry_at: datetime | None = None
    cooldown_started_at: datetime | None = None


class SearchResult(Model):
    sources: list[SearchSource] = Field(default_factory=list)
    attempts: list[SearchAttempt] = Field(default_factory=list)
    status: Literal["found", "unavailable"]


class Place(Model):
    id: str
    provider: Literal["amap"] = "amap"
    provider_id: str
    name: str
    address: str | None
    longitude: float | None
    latitude: float | None
    coordinate_system: Literal["GCJ-02"] = "GCJ-02"
    city: str | None
    citycode: str | None
    adcode: str | None
    queried_at: datetime
    opening_hours_text: str | None = None
    cost_text: str | None = None
    category: str | None = None
    category_code: str | None = None


class Route(Model):
    provider: Literal["amap"] = "amap"
    origin_place_id: str
    destination_place_id: str
    mode: Literal["walking", "transit", "driving"]
    distance_meters: int | None
    duration_seconds: int | None
    queried_at: datetime
    status: Literal["estimated", "unknown"]
    fare_cents: int | None = None
    fare_basis: Literal["per_person", "per_group", "fixed"] = "per_person"
