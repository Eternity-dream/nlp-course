from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from travel_planner.main import create_app

TRIP = {
    "destination": "西安",
    "start_date": "2026-12-01",
    "end_date": "2026-12-03",
    "travelers": 2,
    "budget_cents": 300000,
}


@pytest.mark.parametrize("days", [3, 7])
def test_inclusive_date_boundaries(client, days):
    response = client.post("/api/trips", json={**TRIP, "end_date": f"2026-12-0{days}"})
    assert response.status_code == 201
    trip = response.json()
    assert trip["missing_fields"] == []
    assert trip["timezone"] == "Asia/Shanghai"
    assert trip["currency"] == "CNY"
    assert trip["budget_cents"] == 300000
    assert client.get(f"/api/trips/{trip['id']}").json() == trip


@pytest.mark.parametrize(
    "changes",
    [
        {"end_date": "2026-11-30"},
        {"end_date": "2026-12-02"},
        {"end_date": "2026-12-08"},
        {"start_date": "bad"},
        {"travelers": 0},
        {"travelers": True},
        {"budget_cents": 1.5},
        {"budget_cents": -1},
        {"destination": " "},
        {"extra_field": "value"},
        {"start_date": 1796083200},
        {"start_date": "20261201"},
    ],
)
def test_reject_invalid_trip(client, changes):
    response = client.post("/api/trips", json={**TRIP, **changes})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert client.get("/api/trips").json() == []


def test_missing_critical_fields_are_saved_for_clarification(client):
    trip = client.post("/api/trips", json={"destination": "杭州"}).json()
    assert set(trip["missing_fields"]) == {"start_date", "end_date", "travelers"}
    response = client.patch(
        f"/api/trips/{trip['id']}",
        json={
            "expected_revision": 1,
            "start_date": "2026-12-01",
            "end_date": "2026-12-03",
            "travelers": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert response.json()["missing_fields"] == []
    assert response.json()["destination"] == "杭州"


def test_patch_atomicity_and_revision_history(client):
    trip = client.post("/api/trips", json=TRIP).json()
    path = f"/api/trips/{trip['id']}"
    invalid = client.patch(path, json={"expected_revision": 1, "end_date": "2026-12-08"})
    assert invalid.status_code == 422
    assert client.get(path).json() == trip
    updated = client.patch(path, json={"expected_revision": 1, "destination": "杭州"})
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert (
        client.patch(path, json={"expected_revision": 1, "destination": "北京"}).status_code == 409
    )
    with client.app.state.db.connect() as connection:
        rows = connection.execute(
            "SELECT revision FROM trip_revisions ORDER BY revision"
        ).fetchall()
    assert [row["revision"] for row in rows] == [1, 2]
    assert client.patch(path, json={"expected_revision": 2}).status_code == 422


def test_parallel_updates_only_one_wins(client):
    trip = client.post("/api/trips", json=TRIP).json()
    path = f"/api/trips/{trip['id']}"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda city: client.patch(path, json={"expected_revision": 1, "destination": city}),
                ["杭州", "成都"],
            )
        )
    assert sorted(result.status_code for result in results) == [200, 409]
    assert client.get(path).json()["revision"] == 2


def test_trips_preferences_persist_on_restart(settings):
    with TestClient(create_app(settings)) as client:
        trip = client.post("/api/trips", json=TRIP).json()
        preferences = client.put(
            "/api/preferences",
            json={
                "expected_revision": 1,
                "preferences": {"节奏": "慢节奏", "饮食": "素食"},
            },
        ).json()
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/trips/{trip['id']}").json() == trip
        assert client.get("/api/preferences").json() == preferences
        assert (
            client.put(
                "/api/preferences",
                json={
                    "expected_revision": 1,
                    "preferences": {},
                },
            ).status_code
            == 409
        )
        client.patch(
            f"/api/trips/{trip['id']}",
            json={
                "expected_revision": 1,
                "requirements": ["本次想快节奏游览"],
            },
        )
        assert client.get("/api/preferences").json() == preferences
        cleared = client.put(
            "/api/preferences",
            json={
                "expected_revision": 2,
                "preferences": {},
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["preferences"] == {}
