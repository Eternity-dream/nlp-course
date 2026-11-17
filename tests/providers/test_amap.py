import httpx
import pytest
from travel_planner.providers.amap import Amap
from travel_planner.providers.errors import ProviderError

from .conftest import make_http

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("city", ["西安", "杭州"])
async def test_place_destination_filter_identity_and_coordinates(samples, city):
    def handle(request):
        assert request.url.path == "/v5/place/text"
        assert request.url.params["region"] == city
        assert request.url.params["city_limit"] == "true"
        assert request.url.params["key"] == "map-secret"
        assert request.url.params["show_fields"] == "business"
        return httpx.Response(200, json=samples["amap"])

    client, http = make_http(handle)
    async with client:
        places = await Amap("map-secret", http).places(city, "同名餐馆")
    assert places[0].id != places[1].id
    assert places[0].name == places[1].name
    assert places[0].address != places[1].address
    assert places[0].coordinate_system == "GCJ-02"
    assert places[0].longitude == 108.95
    assert places[0].opening_hours_text == "09:00-17:00"
    assert places[1].opening_hours_text is None
    assert places[2].longitude is None


@pytest.mark.parametrize("mode", ["walking", "driving", "transit"])
async def test_routes_keep_poi_ids_city_code_and_estimates(samples, mode):
    def handle(request):
        if request.url.path == "/v5/place/text":
            return httpx.Response(200, json=samples["amap"])
        params = request.url.params
        assert params["show_fields"] == "cost"
        assert params["origin"] == "108.950000,34.250000"
        if mode == "transit":
            assert request.url.path == "/v5/direction/transit/integrated"
            assert params["city1"] == params["city2"] == "029"
            assert params["originpoi"] == "B001"
        else:
            assert request.url.path == "/v5/direction/" + mode
            assert params["origin_id"] == "B001"
            assert params["destination_id"] == "B002"
        key = "transits" if mode == "transit" else "paths"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {key: [{"distance": "1500", "cost": {"duration": "610.5"}}]},
            },
        )

    client, http = make_http(handle)
    async with client:
        amap = Amap("map-secret", http)
        places = await amap.places("西安", "餐馆")
        route = await amap.route(places[0], places[1], mode)
    assert route.duration_seconds == 611
    assert route.distance_meters == 1500
    assert route.status == "estimated"
    assert route.queried_at.tzinfo


@pytest.mark.parametrize(
    "path",
    [
        {},
        {"cost": {}},
        {"cost": {"duration": "bad"}},
        {"cost": {"duration": "NaN"}},
        {"cost": {"duration": "-1"}},
    ],
)
async def test_missing_or_invalid_duration_is_unknown(samples, path):
    def handle(request):
        return httpx.Response(
            200,
            json=samples["amap"]
            if request.url.path.endswith("text")
            else {
                "status": "1",
                "route": {"paths": [path]},
            },
        )

    client, http = make_http(handle)
    async with client:
        amap = Amap("map-secret", http)
        places = await amap.places("西安", "餐馆")
        result = await amap.route(places[0], places[1])
        assert result.status == "unknown"
        assert result.duration_seconds is None


async def test_unconfirmed_coordinates_and_other_city_do_not_produce_routes(samples):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=samples["amap"])

    client, http = make_http(handle)
    async with client:
        amap = Amap("map-secret", http)
        places = await amap.places("西安", "餐馆")
        assert (await amap.route(places[0], places[2])).status == "unknown"
        other = places[1].model_copy(update={"citycode": "0571"})
        assert (await amap.route(places[0], other)).status == "unknown"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "code,reason",
    [
        ("10001", "auth_failed"),
        ("10003", "quota_exhausted"),
        ("10004", "rate_limited"),
        ("20000", "request_invalid"),
        ("99999", "unavailable"),
    ],
)
async def test_amap_200_error_classification_and_redaction(code, reason):
    client, http = make_http(
        lambda r: httpx.Response(
            200,
            json={
                "status": "0",
                "infocode": code,
                "info": "private map-secret",
            },
        )
    )
    async with client:
        with pytest.raises(ProviderError) as error:
            await Amap("map-secret", http).places("西安", "碑林")
    assert error.value.reason == reason
    assert "secret" not in str(error.value.payload())


async def test_default_http_request_logs_do_not_expose_amap_key(samples, caplog):
    client, http = make_http(lambda request: httpx.Response(200, json=samples["amap"]))
    async with client:
        with caplog.at_level("INFO", logger="httpx"):
            await Amap("map-secret", http).places("西安", "博物馆")
    assert "map-secret" not in caplog.text
    assert "status=200" in caplog.text


@pytest.mark.parametrize(
    "mode,fare,basis",
    [("walking", 0, "per_person"), ("transit", 550, "per_person"), ("driving", 3050, "per_group")],
)
async def test_route_fare_preserves_group_and_person_basis(samples, mode, fare, basis):
    def handle(request):
        if request.url.path.endswith("text"):
            return httpx.Response(200, json=samples["amap"])
        key = "transits" if mode == "transit" else "paths"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "taxi_cost": "30.50",
                    key: [{"distance": "1000", "cost": {"duration": "600", "transit_fee": "5.50"}}],
                },
            },
        )

    client, http = make_http(handle)
    async with client:
        amap = Amap("map-secret", http)
        places = await amap.places("西安", "餐馆")
        route = await amap.route(places[0], places[1], mode)
        assert route.fare_cents == fare
        assert route.fare_basis == basis


async def test_same_area_meal_anchor_does_not_query_a_detour(samples):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=samples["amap"])

    client, http = make_http(handle)
    async with client:
        amap = Amap("map-secret", http)
        places = await amap.places("北京", "区域")
        route = await amap.route(places[0], places[0], "transit")
    assert len(calls) == 1
    assert route.duration_seconds == 0
    assert route.distance_meters == 0
    assert route.fare_cents == 0
    assert route.mode == "walking"
