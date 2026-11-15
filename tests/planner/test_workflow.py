import json
import threading

import httpx
import pytest
from conftest import wait_source
from fastapi.testclient import TestClient
from travel_planner.main import create_app

from .conftest import TRIP, config, submit, wait_job


@pytest.mark.parametrize("city,days", [("西安", 3), ("杭州", 7)])
def test_complete_planning_uses_city_dates_evidence_routes_and_group_budget(
    planned_client, services, city, days
):
    client = planned_client
    trip = client.post(
        "/api/trips",
        json={**TRIP, "destination": city, "end_date": f"2026-12-0{days}", "budget_cents": 1000000},
    ).json()
    trip, job = submit(client, trip)
    completed = wait_job(client, job["id"])
    assert completed["status"] == "succeeded", completed
    result = client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert len(result["days"]) == days
    assert result["validation"]["status"] == "passed", result["validation"]
    assert result["validation"]["known_cost_cents"] == days * 26000
    assert len(result["transport"]) == days * 2
    assert all(fact["status"] == "supported" for fact in result["evidence"])
    assert result["timezone"] == "Asia/Shanghai"
    assert result["currency"] == "CNY"
    assert completed["result_revision"] == 1
    stages = {event["stage"] for event in completed["events"]}
    assert {
        "conditions",
        "knowledge",
        "places",
        "evidence",
        "generation",
        "routes",
        "validation",
        "saving",
        "saved",
    } <= stages
    assert all(source["queried_at"] and source["fetched_at"] for source in result["sources"])
    ids = {a["id"] for day in result["days"] for a in day["activities"]}
    assert all(len(item) == 32 for item in ids)
    assert all(
        leg["from_activity_id"] in ids and leg["to_activity_id"] in ids
        for leg in result["transport"]
    )
    assert all(leg["route"]["status"] == "estimated" for leg in result["transport"])
    assert client.get(f"/api/trips/{trip['id']}/jobs").json()[0]["id"] == job["id"]


def test_missing_critical_input_awaits_without_calling_services_and_can_resume(settings, services):
    with TestClient(
        create_app(config(settings), provider_transport=httpx.MockTransport(services))
    ) as client:
        trip = client.post("/api/trips", json={"destination": "西安"}).json()
        _, job = submit(client, trip)
        waiting = wait_job(client, job["id"])
        assert waiting["status"] == "awaiting_input"
        assert len(waiting["questions"]) == 3
        assert services.requests == []
        assert (
            client.post(f"/api/jobs/{job['id']}/resume", json={"expected_revision": 1}).status_code
            == 409
        )
        update = client.patch(
            f"/api/trips/{trip['id']}",
            json={
                "expected_revision": 1,
                **{key: value for key, value in TRIP.items() if key != "destination"},
            },
        ).json()
        resumed = client.post(
            f"/api/jobs/{job['id']}/resume", json={"expected_revision": update["revision"]}
        )
        assert resumed.status_code == 202
        assert wait_job(client, job["id"])["status"] == "succeeded"
        replay = client.post(
            f"/api/trips/{trip['id']}/jobs",
            json={"expected_revision": 1, "idempotency_key": "first"},
        )
        assert replay.status_code == 202 and replay.json()["id"] == job["id"]


def test_missing_configuration_rejects_complete_jobs_but_stores_clarification(client):
    trip = client.post("/api/trips", json=TRIP).json()
    response = client.post(
        f"/api/trips/{trip['id']}/jobs", json={"expected_revision": 1, "idempotency_key": "a"}
    )
    assert response.status_code == 503
    assert client.get(f"/api/trips/{trip['id']}/jobs").json() == []
    incomplete = client.post("/api/trips", json={"destination": "西安"}).json()
    _, job = submit(client, incomplete)
    assert wait_job(client, job["id"])["status"] == "awaiting_input"


def test_single_executor_queue_and_idempotency(planned_client, services):
    client = planned_client
    services.block, services.entered = threading.Event(), threading.Event()
    first_trip, first = submit(client)
    assert services.entered.wait(3)
    try:
        second_trip, second = submit(client, key="second")
        assert client.get("/api/jobs/" + second["id"]).json()["status"] == "queued"
        replay = client.post(
            f"/api/trips/{first_trip['id']}/jobs",
            json={"expected_revision": 1, "idempotency_key": "first"},
        )
        assert replay.status_code == 202 and replay.json()["id"] == first["id"]
        with client.app.state.db.connect() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM plan_jobs WHERE status='running'"
                ).fetchone()[0]
                == 1
            )
        assert services.operations == ["candidates"]
    finally:
        services.block.set()
    assert wait_job(client, first["id"])["status"] == "succeeded"
    assert wait_job(client, second["id"])["status"] == "succeeded"
    assert client.get(f"/api/trips/{first_trip['id']}/itinerary").status_code == 200
    assert client.get(f"/api/trips/{second_trip['id']}/itinerary").status_code == 200


@pytest.mark.parametrize(
    "mode,rule",
    [("closed", "closed"), ("long_route", "travel_time"), ("over_budget", "budget_exceeded")],
)
def test_known_conflict_remains_blocked_after_two_repairs(planned_client, services, mode, rule):
    if mode != "over_budget":
        setattr(services, mode, True)
    client = planned_client
    trip = client.post(
        "/api/trips", json={**TRIP, "budget_cents": 1 if mode == "over_budget" else 100000}
    ).json()
    _, job = submit(client, trip)
    completed = wait_job(client, job["id"])
    assert completed["status"] == "succeeded", completed
    result = client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert result["validation"]["status"] == "blocked"
    assert rule in {issue["rule"] for issue in result["validation"]["issues"]}
    assert services.draft_calls == 3


def test_automatic_repair_resolves_conflict(planned_client, services):
    services.bad_first_draft = True
    trip, job = submit(planned_client)
    assert wait_job(planned_client, job["id"])["status"] == "succeeded"
    result = planned_client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert result["validation"]["status"] == "passed"
    assert services.draft_calls == 2


def test_all_search_failed_keeps_readable_draft_with_unknown_facts(planned_client, services):
    services.no_sources = True
    trip, job = submit(planned_client)
    completed = wait_job(planned_client, job["id"])
    assert completed["status"] == "succeeded", completed
    result = planned_client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert result["sources"] == [] and result["evidence"] == []
    assert result["validation"]["status"] == "needs_review"
    assert result["validation"]["unknown_cost_count"] == 9
    assert result["search_attempts"]
    assert result["notices"]


def test_invalid_model_output_fails_without_overwriting_previous_revision(planned_client, services):
    client = planned_client
    trip, first = submit(client)
    assert wait_job(client, first["id"])["status"] == "succeeded"
    original = client.get(f"/api/trips/{trip['id']}/itinerary").json()
    services.break_draft = '{"days":[]}'
    _, second = submit(client, trip, "second")
    failed = wait_job(client, second["id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "provider_model_invalid_output"
    assert client.get(f"/api/trips/{trip['id']}/itinerary").json() == original
    assert (
        client.post(
            f"/api/jobs/{first['id']}/retry",
            json={"expected_revision": 1, "idempotency_key": "retry-ok"},
        ).status_code
        == 409
    )
    services.break_draft = None
    retry = client.post(
        f"/api/jobs/{second['id']}/retry",
        json={"expected_revision": 1, "idempotency_key": "retry-failed"},
    )
    assert retry.status_code == 202
    assert retry.json()["retry_of"] == second["id"]
    assert wait_job(client, retry.json()["id"])["status"] == "succeeded"
    assert client.get(f"/api/trips/{trip['id']}/itinerary").json()["revision"] == 2
    assert client.get(f"/api/trips/{trip['id']}/itinerary?revision=1").json() == original


def test_request_changes_during_generation_cannot_replace_current_result(planned_client, services):
    client = planned_client
    services.block, services.entered = threading.Event(), threading.Event()
    trip, job = submit(client)
    assert services.entered.wait(3)
    try:
        assert (
            client.patch(
                f"/api/trips/{trip['id']}", json={"expected_revision": 1, "destination": "杭州"}
            ).status_code
            == 200
        )
    finally:
        services.block.set()
    completed = wait_job(client, job["id"])
    assert completed["status"] == "failed"
    assert completed["error"]["code"] == "request_changed"
    assert client.get(f"/api/trips/{trip['id']}/itinerary").status_code == 404


def test_personal_data_only_in_model_context_and_rechecked_before_each_call(
    planned_client, services
):
    client = planned_client
    response = client.post(
        "/api/knowledge", json={"title": "私人资料", "text": "西安私人片段：只在模型中使用。"}
    ).json()
    assert wait_source(client, response["id"])["status"] == "ready"
    services.block, services.entered = threading.Event(), threading.Event()
    trip, job = submit(client)
    assert services.entered.wait(3)
    try:
        assert client.delete(f"/api/knowledge/{response['id']}").status_code == 204
    finally:
        services.block.set()
    assert wait_job(client, job["id"])["status"] == "succeeded"
    contexts = services.contexts
    assert contexts[0]["personal_material"]
    assert all(not item.get("personal_material") for item in contexts[1:])
    for request in services.requests:
        if request.url.host != "model.example.com":
            assert "私人" not in request.content.decode()


def test_current_requirements_are_separate_from_unchanged_preferences(planned_client, services):
    client = planned_client
    preferences = client.put(
        "/api/preferences", json={"expected_revision": 1, "preferences": {"节奏": "高强度"}}
    ).json()
    trip = client.post("/api/trips", json={**TRIP, "requirements": ["本次慢节奏"]}).json()
    _, job = submit(client, trip)
    assert wait_job(client, job["id"])["status"] == "succeeded"
    assert client.get("/api/preferences").json() == preferences
    assert services.contexts[0]["request"]["requirements"] == ["本次慢节奏"]
    assert services.contexts[0]["preferences"] == {"节奏": "高强度"}
    result = client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert any(issue["rule"] == "explicit_constraint" for issue in result["validation"]["issues"])
    assert "matching_reason" not in json.dumps(result)


def test_other_city_material_filtered_and_general_preferences_remain_retrievable(
    planned_client, services
):
    client = planned_client
    for title, text in [
        ("本城", "西安博物馆笔记：私人本城资料。"),
        ("他城", "杭州博物馆笔记：私人杭州资料。"),
        ("通用", "博物馆游览一般需要安排休息。"),
    ]:
        response = client.post("/api/knowledge", json={"title": title, "text": text}).json()
        assert wait_source(client, response["id"])["status"] == "ready"
    trip = client.post("/api/trips", json={**TRIP, "priorities": ["博物馆"]}).json()
    _, job = submit(client, trip)
    assert wait_job(client, job["id"])["status"] == "succeeded"
    content = str(services.contexts[0]["personal_material"])
    assert "本城资料" in content
    assert "私人杭州资料" not in content
    assert "安排休息" in content


def test_material_deleted_between_format_repair_calls_is_not_resent(
    planned_client, services, monkeypatch
):
    client = planned_client
    source = client.post(
        "/api/knowledge", json={"title": "私人笔记", "text": "西安PRIVATE_MARKER"}
    ).json()
    assert wait_source(client, source["id"])["status"] == "ready"
    model = client.app.state.services.model
    original_request = model._request
    call_count = 0

    async def invalidate_first(payload, prepare_messages=None):
        nonlocal call_count
        result = await original_request(payload, prepare_messages)
        call_count += 1
        if call_count == 1:
            client.app.state.knowledge.delete(source["id"])
            return {"choices": [{"finish_reason": "stop", "message": {"content": "bad JSON"}}]}
        return result

    monkeypatch.setattr(model, "_request", invalidate_first)
    trip, job = submit(client)
    assert wait_job(client, job["id"])["status"] == "succeeded"
    contexts = [item for item in services.contexts if item["operation"] == "candidates"]
    assert "PRIVATE_MARKER" in str(contexts[0]["personal_material"])
    assert contexts[1]["personal_material"] == []
