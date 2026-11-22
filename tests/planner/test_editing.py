import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from travel_planner.main import create_app
from travel_planner.planner.models import ConstraintReview, ItineraryEdit, PlanDraft
from travel_planner.providers.errors import ProviderError

from .conftest import config, submit, wait_job


@pytest.fixture
def original(planned_client):
    trip, job = submit(planned_client)
    assert wait_job(planned_client, job["id"])["status"] == "succeeded"
    return trip, planned_client.get(f"/api/trips/{trip['id']}/itinerary").json()


def edit(client, trip, base, operations, key="edit", wait=True):
    response = client.patch(
        f"/api/trips/{trip['id']}/itinerary",
        json={
            "expected_revision": base["revision"],
            "expected_request_revision": base["request_revision"],
            "idempotency_key": key,
            "operations": operations,
        },
    )
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["id"]) if wait else response.json()
    return job


def current(client, trip):
    return client.get(f"/api/trips/{trip['id']}/itinerary").json()


def activity_input(activity, **changes):
    return {
        **copy.deepcopy({key: value for key, value in activity.items() if key != "id"}),
        **changes,
    }


def assert_unchanged(base, result, indexes):
    for index in indexes:
        assert result["days"][index] == base["days"][index]
        ids = {item["id"] for item in base["days"][index]["activities"]}
        assert [leg for leg in result["transport"] if leg["from_activity_id"] in ids] == [
            leg for leg in base["transport"] if leg["from_activity_id"] in ids
        ]
        on_date = base["days"][index]["on_date"]
        assert [fact for fact in result["evidence"] if fact["on_date"] == on_date] == [
            fact for fact in base["evidence"] if fact["on_date"] == on_date
        ]


def test_remove_revalidates_only_changed_day_and_recalculates_total(
    planned_client, original, services
):
    trip, base = original
    target = base["days"][0]["activities"][0]
    preferences = planned_client.get("/api/preferences").json()
    previous_generation_calls = services.draft_calls
    job = edit(planned_client, trip, base, [{"op": "remove", "activity_id": target["id"]}])
    assert job["status"] == "succeeded", job
    assert job["kind"] == "edit" and job["base_revision"] == 1
    result = current(planned_client, trip)
    assert result["revision"] == 2
    assert_unchanged(base, result, [1, 2])
    assert target["id"] not in {a["id"] for a in result["days"][0]["activities"]}
    assert result["validation"]["known_cost_cents"] == 58000
    assert result["validation"]["status"] == "needs_review"
    assert planned_client.get("/api/preferences").json() == preferences
    assert planned_client.get(f"/api/trips/{trip['id']}/itinerary?revision=1").json() == base
    assert services.draft_calls == previous_generation_calls
    extracted = [
        context for context in services.contexts if context["operation"] == "extract_facts"
    ][-1]
    assert extracted["dates"] == [base["days"][0]["on_date"]]
    assert all("personal_material" not in context for context in [extracted])


def test_add_creates_stable_id_and_moves_following_activity_within_same_day(
    planned_client, original
):
    trip, base = original
    first, second = base["days"][0]["activities"][:2]
    spec = activity_input(
        first,
        place_id="amap:P3",
        start_minute=650,
        end_minute=710,
        costs=[{"category": "ticket", "amount_cents": 0}],
    )
    operations = [
        {
            "op": "add",
            "on_date": base["days"][0]["on_date"],
            "before_activity_id": second["id"],
            "activity": spec,
        }
    ]
    job = edit(planned_client, trip, base, operations)
    assert job["status"] == "succeeded", job
    result = current(planned_client, trip)
    activities = result["days"][0]["activities"]
    assert [a["id"] for a in activities if a["id"] in {first["id"], second["id"]}] == [
        first["id"],
        second["id"],
    ]
    added = activities[1]
    assert len(added["id"]) == 32 and added["id"] not in {
        a["id"] for day in base["days"] for a in day["activities"]
    }
    assert added["start_minute"] == 670
    assert activities[2]["start_minute"] == 740
    assert_unchanged(base, result, [1, 2])
    replay = edit(planned_client, trip, base, operations)
    assert replay["id"] == job["id"]
    assert current(planned_client, trip) == result


def test_replace_preserves_activity_id_and_uses_selected_new_poi(
    planned_client, original, services
):
    trip, base = original
    new_place = {**services.places()[0], "id": "P4", "name": "新展馆", "address": "西安路4号"}
    services.extra_places.append(new_place)
    services.prices["P4"] = 8000
    lookup = planned_client.get(f"/api/trips/{trip['id']}/places", params={"q": "新展馆"})
    assert lookup.status_code == 200
    assert lookup.json()[0]["id"] == "amap:P4"
    target = base["days"][0]["activities"][0]
    spec = activity_input(
        target,
        place_id="amap:P4",
        place_query="新展馆",
        costs=[{"category": "ticket", "amount_cents": 8000}],
    )
    job = edit(
        planned_client,
        trip,
        base,
        [{"op": "replace", "activity_id": target["id"], "activity": spec}],
    )
    assert job["status"] == "succeeded", job
    result = current(planned_client, trip)
    replacement = result["days"][0]["activities"][0]
    assert replacement["id"] == target["id"]
    assert replacement["place_id"] == "amap:P4"
    assert (
        next(place for place in result["places"] if place["id"] == "amap:P4")["address"]
        == "西安路4号"
    )
    assert_unchanged(base, result, [1, 2])


def test_same_day_move_uses_ids_and_only_reorders_selected_date(planned_client, original):
    trip, base = original
    activities = base["days"][0]["activities"]
    job = edit(
        planned_client,
        trip,
        base,
        [
            {
                "op": "move",
                "activity_id": activities[2]["id"],
                "target_date": base["days"][0]["on_date"],
                "before_activity_id": activities[1]["id"],
            }
        ],
    )
    assert job["status"] == "succeeded", job
    result = current(planned_client, trip)
    assert [a["id"] for a in result["days"][0]["activities"]] == [
        activities[0]["id"],
        activities[2]["id"],
        activities[1]["id"],
    ]
    assert result["days"][0]["activities"][2]["start_minute"] == 1210
    assert_unchanged(base, result, [1, 2])


def test_explicit_cross_day_move_rechecks_two_dates_and_preserves_third(planned_client, original):
    trip, base = original
    moved = base["days"][0]["activities"][0]
    anchor = base["days"][1]["activities"][0]
    job = edit(
        planned_client,
        trip,
        base,
        [
            {
                "op": "move",
                "activity_id": moved["id"],
                "target_date": base["days"][1]["on_date"],
                "before_activity_id": anchor["id"],
            }
        ],
    )
    assert job["status"] == "succeeded", job
    result = current(planned_client, trip)
    assert moved["id"] not in {a["id"] for a in result["days"][0]["activities"]}
    assert result["days"][1]["activities"][0]["id"] == moved["id"]
    evidence = {fact["id"]: fact for fact in result["evidence"]}
    cost = result["days"][1]["activities"][0]["costs"][0]
    assert evidence[cost["evidence_id"]]["on_date"] == base["days"][1]["on_date"]
    assert result["validation"]["known_cost_cents"] == base["validation"]["known_cost_cents"]
    assert_unchanged(base, result, [2])


def test_remove_last_activity_preserves_empty_day_instead_of_recreating_content(
    planned_client, original
):
    trip, base = original
    job = edit(
        planned_client,
        trip,
        base,
        [{"op": "remove", "activity_id": a["id"]} for a in base["days"][0]["activities"]],
    )
    assert job["status"] == "succeeded", job
    result = current(planned_client, trip)
    assert result["days"][0]["activities"] == []
    assert result["validation"]["status"] == "needs_review"
    assert_unchanged(base, result, [1, 2])


@pytest.mark.parametrize(
    "case",
    [
        "unknown_activity",
        "other_day_anchor",
        "outside_date",
        "self_anchor",
        "double_remove",
        "unknown_place",
        "wrong_evidence",
        "bad_time",
        "missing_date",
        "extra_field",
    ],
)
def test_invalid_operations_rejected_without_creating_job(planned_client, original, case):
    trip, base = original
    first = base["days"][0]["activities"][0]
    ops = [{"op": "remove", "activity_id": first["id"]}]
    if case == "unknown_activity":
        ops[0]["activity_id"] = "absent"
    elif case == "other_day_anchor":
        ops = [
            {
                "op": "move",
                "activity_id": first["id"],
                "target_date": base["days"][0]["on_date"],
                "before_activity_id": base["days"][1]["activities"][0]["id"],
            }
        ]
    elif case == "outside_date":
        ops = [{"op": "move", "activity_id": first["id"], "target_date": "2026-12-10"}]
    elif case == "self_anchor":
        ops = [
            {
                "op": "move",
                "activity_id": first["id"],
                "target_date": "2026-12-01",
                "before_activity_id": first["id"],
            }
        ]
    elif case == "double_remove":
        ops *= 2
    elif case in {"unknown_place", "wrong_evidence", "bad_time"}:
        spec = activity_input(first)
        if case == "unknown_place":
            spec["place_id"] = "amap:invented"
        elif case == "wrong_evidence":
            spec["costs"][0]["evidence_id"] = "invented"
        else:
            spec["end_minute"] = 100
        ops = [{"op": "replace", "activity_id": first["id"], "activity": spec}]
    elif case == "missing_date":
        ops = [{"op": "move", "activity_id": first["id"]}]
    else:
        ops[0]["on_date"] = "2026-12-01"
    before = len(planned_client.get(f"/api/trips/{trip['id']}/jobs").json())
    response = planned_client.patch(
        f"/api/trips/{trip['id']}/itinerary",
        json={
            "expected_revision": 1,
            "expected_request_revision": 1,
            "idempotency_key": "bad",
            "operations": ops,
        },
    )
    assert response.status_code == 422, response.text
    assert current(planned_client, trip) == base
    assert len(planned_client.get(f"/api/trips/{trip['id']}/jobs").json()) == before


def test_stale_itinerary_and_changed_request_return_conflict(planned_client, original):
    trip, base = original
    target = base["days"][0]["activities"][0]
    operations = [{"op": "remove", "activity_id": target["id"]}]
    assert edit(planned_client, trip, base, operations)["status"] == "succeeded"
    response = planned_client.patch(
        f"/api/trips/{trip['id']}/itinerary",
        json={
            "expected_revision": 1,
            "expected_request_revision": 1,
            "idempotency_key": "stale",
            "operations": operations,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "revision_conflict"
    planned_client.patch(
        f"/api/trips/{trip['id']}", json={"expected_revision": 1, "budget_cents": 999999}
    )
    response = planned_client.patch(
        f"/api/trips/{trip['id']}/itinerary",
        json={
            "expected_revision": 2,
            "expected_request_revision": 2,
            "idempotency_key": "changed",
            "operations": operations,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "request_changed"


def test_processing_edit_keeps_old_revision_readable_and_rejects_late_competitor(
    planned_client, original, services
):
    trip, base = original
    services.block_operation = "extract_facts"
    services.block, services.entered = threading.Event(), threading.Event()
    operations = [{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}]
    first = edit(planned_client, trip, base, operations, "first-edit", wait=False)
    assert services.entered.wait(3)
    try:
        second = edit(planned_client, trip, base, operations, "second-edit", wait=False)
        assert second["status"] == "queued"
        assert current(planned_client, trip) == base
    finally:
        services.block.set()
    assert wait_job(planned_client, first["id"])["status"] == "succeeded"
    stale = wait_job(planned_client, second["id"])
    assert stale["status"] == "failed"
    assert stale["error"]["code"] == "revision_conflict"
    assert current(planned_client, trip)["revision"] == 2


def test_changed_price_and_unknown_sources_update_budget_without_touching_other_days(
    planned_client, original, services
):
    trip, base = original
    services.prices["P1"] = 100000
    target = base["days"][0]["activities"][-1]
    operations = [
        {"op": "replace", "activity_id": target["id"], "activity": activity_input(target)}
    ]
    assert edit(planned_client, trip, base, operations)["status"] == "succeeded"
    result = current(planned_client, trip)
    assert result["validation"]["status"] == "blocked"
    assert result["validation"]["known_cost_cents"] == 258000
    assert_unchanged(base, result, [1, 2])
    services.no_sources = True
    target = result["days"][0]["activities"][-1]
    assert (
        edit(
            planned_client,
            trip,
            result,
            [{"op": "replace", "activity_id": target["id"], "activity": activity_input(target)}],
            "no-source",
        )["status"]
        == "succeeded"
    )
    unknown = current(planned_client, trip)
    assert unknown["days"][0]["activities"][0]["costs"][0]["evidence_id"] is None
    assert any(
        i["rule"] == "opening_hours" and i["severity"] == "unknown"
        for i in unknown["validation"]["issues"]
    )
    assert_unchanged(result, unknown, [1, 2])


@pytest.mark.parametrize("closed,expected_status", [(False, "needs_review"), (True, "blocked")])
def test_edit_saves_validation_when_quality_review_times_out(
    planned_client, original, services, monkeypatch, closed, expected_status
):
    trip, base = original
    services.closed = closed

    async def unavailable(*args, **kwargs):
        raise ProviderError("llm", "timeout")

    monkeypatch.setattr("travel_planner.planner.editor.review_itinerary", unavailable)
    target = base["days"][0]["activities"][-1]
    operation = {
        "op": "replace",
        "activity_id": target["id"],
        "activity": activity_input(target, end_minute=1170),
    }
    job = edit(planned_client, trip, base, [operation])
    assert job["status"] == "succeeded", job
    result = current(planned_client, trip)
    assert result["revision"] == 2
    assert result["days"][0]["activities"][-1]["end_minute"] == 1170
    assert result["planning_context"]["edit_review"] is None
    validation = result["validation"]
    assert validation["status"] == expected_status
    issues = {(item["rule"], item["severity"]) for item in validation["issues"]}
    assert ("quality_unavailable", "unknown") in issues
    if closed:
        assert ("closed", "conflict") in issues
    else:
        assert issues == {("quality_unavailable", "unknown")}


@pytest.mark.parametrize("failure", ["model", "route", "save", "scope"])
def test_failed_recheck_retains_original_and_can_retry(
    planned_client, original, services, monkeypatch, failure
):
    client = planned_client
    trip, base = original
    operations = [{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}]
    with monkeypatch.context() as patch:
        if failure == "model":
            services.fail_model = True
        elif failure == "route":

            async def fail_route(*args, **kwargs):
                raise ProviderError("amap", "timeout")

            patch.setattr(client.app.state.services.amap, "route", fail_route)
        elif failure == "save":
            with client.app.state.db.connect(write=True) as connection:
                connection.execute("""CREATE TRIGGER fail_edit_save
                    BEFORE UPDATE ON current_itineraries
                    BEGIN SELECT RAISE(ABORT, 'save failure'); END""")
        else:
            editor = client.app.state.planner.editor
            run = editor.run

            async def wrong_scope(job):
                result = await run(job)
                result["days"][2]["activities"][0]["start_minute"] += 1
                return result

            patch.setattr(editor, "run", wrong_scope)
        failed = edit(client, trip, base, operations)
        assert failed["status"] == "failed", failed
        assert current(client, trip) == base
        with client.app.state.db.connect() as connection:
            assert connection.execute("SELECT count(*) FROM itinerary_revisions").fetchone()[0] == 1
    services.fail_model = False
    if failure == "save":
        with client.app.state.db.connect(write=True) as connection:
            connection.execute("DROP TRIGGER fail_edit_save")
    response = client.post(
        f"/api/jobs/{failed['id']}/retry",
        json={"expected_revision": 1, "idempotency_key": "retry-edit"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "edit" and response.json()["base_revision"] == 1
    completed = wait_job(client, response.json()["id"])
    assert completed["status"] == "succeeded", completed
    assert current(client, trip)["revision"] == 2
    assert_unchanged(base, current(client, trip), [1, 2])
    replay = client.post(
        f"/api/jobs/{failed['id']}/retry",
        json={"expected_revision": 1, "idempotency_key": "retry-edit"},
    )
    assert replay.status_code == 202 and replay.json()["id"] == response.json()["id"]
    assert (
        client.post(
            f"/api/jobs/{completed['id']}/retry",
            json={"expected_revision": 1, "idempotency_key": "retry-success"},
        ).status_code
        == 409
    )


def test_parallel_edit_replays_return_one_job_and_different_payload_conflicts(
    planned_client, original, services
):
    client = planned_client
    trip, base = original
    operations = [{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}]
    body = {
        "expected_revision": 1,
        "expected_request_revision": 1,
        "idempotency_key": "same-edit",
        "operations": operations,
    }
    services.block_operation = "extract_facts"
    services.block, services.entered = threading.Event(), threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(
                executor.map(
                    lambda _: client.patch(f"/api/trips/{trip['id']}/itinerary", json=body),
                    range(4),
                )
            )
        assert all(response.status_code == 202 for response in responses)
        assert len({response.json()["id"] for response in responses}) == 1
        changed = copy.deepcopy(body)
        changed["operations"][0]["activity_id"] = base["days"][0]["activities"][1]["id"]
        collision = client.patch(f"/api/trips/{trip['id']}/itinerary", json=changed)
        assert collision.status_code == 409
        assert collision.json()["error"]["code"] == "idempotency_conflict"
        assert (
            client.post(
                f"/api/trips/{trip['id']}/jobs",
                json={"expected_revision": 1, "idempotency_key": "same-edit"},
            ).status_code
            == 409
        )
        plan_key = client.patch(
            f"/api/trips/{trip['id']}/itinerary", json={**body, "idempotency_key": "first"}
        )
        assert plan_key.status_code == 409
    finally:
        services.block.set()
    assert wait_job(client, responses[0].json()["id"])["status"] == "succeeded"


def test_request_changed_during_edit_blocks_save(planned_client, original, services):
    client = planned_client
    trip, base = original
    services.block_operation = "extract_facts"
    services.block, services.entered = threading.Event(), threading.Event()
    job = edit(
        client,
        trip,
        base,
        [{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}],
        wait=False,
    )
    assert services.entered.wait(3)
    try:
        changed = client.patch(
            f"/api/trips/{trip['id']}", json={"expected_revision": 1, "priorities": ["建筑"]}
        )
        assert changed.status_code == 200
    finally:
        services.block.set()
    result = wait_job(client, job["id"])
    assert result["status"] == "failed" and result["error"]["code"] == "request_changed"
    assert current(client, trip) == base
    retry = client.post(
        f"/api/jobs/{job['id']}/retry", json={"expected_revision": 2, "idempotency_key": "rebase"}
    )
    assert retry.status_code == 409


def test_restart_preserves_edit_operations_and_interrupted_retry(settings, services):
    cfg = config(settings)
    with TestClient(create_app(cfg, provider_transport=httpx.MockTransport(services))) as client:
        trip, initial = submit(client)
        assert wait_job(client, initial["id"])["status"] == "succeeded"
        base = current(client, trip)
        client.portal.call(client.app.state.planner.close)
        body = ItineraryEdit(
            expected_revision=1,
            expected_request_revision=1,
            idempotency_key="interrupted-edit",
            operations=[{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}],
        )
        job = client.app.state.jobs.submit_edit(trip["id"], body, configured=True)
        assert client.app.state.jobs.claim()["id"] == job["id"]
    with TestClient(create_app(cfg, provider_transport=httpx.MockTransport(services))) as client:
        assert client.get("/api/jobs/" + job["id"]).json()["status"] == "interrupted"
        assert current(client, trip) == base
        response = client.post(
            f"/api/jobs/{job['id']}/retry",
            json={"expected_revision": 1, "idempotency_key": "retry-after-restart"},
        )
        assert response.status_code == 202
        assert response.json()["kind"] == "edit"
        completed = wait_job(client, response.json()["id"])
        assert completed["status"] == "succeeded", completed
        result = current(client, trip)
        assert base["days"][0]["activities"][0]["id"] not in {
            a["id"] for a in result["days"][0]["activities"]
        }
        assert_unchanged(base, result, [1, 2])


@pytest.mark.parametrize("outside_city", [False, True])
def test_poi_selection_disambiguates_same_names_and_rejects_wrong_city(
    planned_client, original, services, outside_city
):
    client = planned_client
    trip, base = original
    source = services.places()[0]
    services.extra_places.extend(
        [
            {**source, "id": "P4", "name": "同名展馆", "address": "西安路4号"},
            {
                **source,
                "id": "P5",
                "name": "同名展馆",
                "address": "异地路5号" if outside_city else "西安路5号",
                "cityname": "杭州市" if outside_city else "西安市",
            },
        ]
    )
    services.prices.update({"P4": 10000, "P5": 10000})
    lookup = client.get(f"/api/trips/{trip['id']}/places", params={"q": "同名展馆"}).json()
    assert len(lookup) == (1 if outside_city else 2)
    first = base["days"][0]["activities"][0]
    job = edit(
        client,
        trip,
        base,
        [
            {
                "op": "replace",
                "activity_id": first["id"],
                "activity": activity_input(
                    first, place_id="amap:P5", place_query="同名展馆", costs=[]
                ),
            }
        ],
    )
    if outside_city:
        assert job["status"] == "failed" and job["error"]["code"] == "place_not_found"
        assert current(client, trip) == base
    else:
        assert job["status"] == "succeeded", job
        result = current(client, trip)
        assert result["days"][0]["activities"][0]["place_id"] == "amap:P5"
        assert next(p for p in result["places"] if p["id"] == "amap:P5")["address"] == "西安路5号"


def test_edit_and_plan_share_single_executor(planned_client, original, services):
    client = planned_client
    trip, base = original
    services.block_operation = "extract_facts"
    services.block, services.entered = threading.Event(), threading.Event()
    first = edit(
        client,
        trip,
        base,
        [{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}],
        wait=False,
    )
    assert services.entered.wait(3)
    try:
        _, queued = submit(client, trip, "queued-plan")
        assert queued["status"] == "queued"
        with client.app.state.db.connect() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM plan_jobs WHERE status='running'"
                ).fetchone()[0]
                == 1
            )
        assert current(client, trip) == base
    finally:
        services.block.set()
    assert wait_job(client, first["id"])["status"] == "succeeded"
    assert wait_job(client, queued["id"])["status"] == "succeeded"
    assert current(client, trip)["revision"] == 3


def test_cross_midnight_edit_flags_next_day_conflict_without_adjusting_next_day(
    planned_client, original
):
    client = planned_client
    trip, base = original
    last = base["days"][0]["activities"][-1]
    job = edit(
        client,
        trip,
        base,
        [
            {
                "op": "replace",
                "activity_id": last["id"],
                "activity": activity_input(last, start_minute=1380, end_minute=2000),
            }
        ],
    )
    assert job["status"] == "succeeded", job
    result = current(client, trip)
    assert result["validation"]["status"] == "blocked"
    assert any(issue["rule"] == "overnight_overlap" for issue in result["validation"]["issues"])
    assert_unchanged(base, result, [1, 2])


def test_removing_reserved_activity_keeps_explicit_conflict(planned_client):
    from .conftest import TRIP

    client = planned_client
    trip = client.post(
        "/api/trips",
        json={
            **TRIP,
            "time_constraints": [
                {
                    "kind": "reservation",
                    "on_date": "2026-12-01",
                    "minute": 540,
                    "end_minute": 600,
                    "place_name": "博物馆",
                }
            ],
        },
    ).json()
    _, initial = submit(client, trip)
    assert wait_job(client, initial["id"])["status"] == "succeeded"
    base = current(client, trip)
    job = edit(
        client,
        trip,
        base,
        [{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}],
    )
    assert job["status"] == "succeeded", job
    result = current(client, trip)
    assert any(
        issue["rule"] == "reservation" and issue["severity"] == "conflict"
        for issue in result["validation"]["issues"]
    )
    assert_unchanged(base, result, [1, 2])


def test_edited_text_constraints_are_reviewed_again_without_reusing_old_conflict(
    planned_client, services
):
    from .conftest import TRIP

    client = planned_client
    services.constraint_conflicts = [
        {"field": "requirements", "index": 0, "reason": "安排了博物馆活动"}
    ]
    trip = client.post("/api/trips", json={**TRIP, "requirements": ["不要安排博物馆"]}).json()
    _, initial = submit(client, trip)
    assert wait_job(client, initial["id"])["status"] == "succeeded"
    base = current(client, trip)
    assert base["constraint_conflicts"]
    services.constraint_conflicts = []
    operations = [
        {"op": "remove", "activity_id": day["activities"][0]["id"]} for day in base["days"]
    ]
    job = edit(client, trip, base, operations)
    assert job["status"] == "succeeded", job
    result = current(client, trip)
    assert result["constraint_conflicts"] == []
    assert any(
        issue["rule"] == "explicit_constraint" and issue["severity"] == "unknown"
        for issue in result["validation"]["issues"]
    )
    assert not any(
        issue["rule"] == "explicit_constraint" and issue["severity"] == "conflict"
        for issue in result["validation"]["issues"]
    )
    review = [item for item in services.contexts if item["operation"] == "review_constraints"][-1]
    assert review["requirements"] == ["不要安排博物馆"]
    assert "personal_material" not in review and "preferences" not in review


def test_retained_booking_note_is_visible_to_edit_constraint_review(planned_client, monkeypatch):
    from .conftest import TRIP

    client = planned_client
    model = client.app.state.services.model
    original_generate = model.generate
    booking_note = "请提前通过官方渠道预约，目前未预约。"
    reviews = []

    async def generate(messages, schema, **kwargs):
        output = await original_generate(messages, schema, **kwargs)
        if schema is PlanDraft:
            output.assumptions.append(booking_note)
        elif schema is ConstraintReview:
            context = json.loads(messages[-1]["content"])
            reviews.append(context)
            if booking_note not in context.get("assumptions", []):
                return ConstraintReview.model_validate(
                    {
                        "conflicts": [
                            {"field": "known_conditions", "index": 0, "reason": "缺少预约提示"}
                        ]
                    }
                )
        return output

    monkeypatch.setattr(model, "generate", generate)
    trip = client.post(
        "/api/trips", json={**TRIP, "known_conditions": ["请在行程说明中保留预约提示"]}
    ).json()
    _, job = submit(client, trip)
    assert wait_job(client, job["id"])["status"] == "succeeded"
    base = current(client, trip)
    job = edit(
        client,
        trip,
        base,
        [{"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}],
    )
    assert job["status"] == "succeeded", job
    result = current(client, trip)
    assert result["validation"]["status"] != "blocked"
    assert result["constraint_conflicts"] == []
    assert booking_note in result["assumptions"]
    assert reviews[-1]["assumptions"] == result["assumptions"]
    assert reviews[-1]["notices"] == result["notices"]
    assert_unchanged(base, result, [1, 2])


def test_seven_day_other_city_edit_preserves_all_other_dates(planned_client):
    from .conftest import TRIP

    client = planned_client
    trip = client.post(
        "/api/trips",
        json={**TRIP, "destination": "杭州", "end_date": "2026-12-07", "budget_cents": 1000000},
    ).json()
    _, initial = submit(client, trip)
    assert wait_job(client, initial["id"])["status"] == "succeeded"
    base = current(client, trip)
    target = base["days"][3]["activities"][0]
    job = edit(client, trip, base, [{"op": "remove", "activity_id": target["id"]}])
    assert job["status"] == "succeeded", job
    result = current(client, trip)
    assert len(result["days"]) == 7
    assert_unchanged(base, result, [0, 1, 2, 4, 5, 6])


def test_missing_config_after_restart_keeps_history_readable_and_rejects_edit(settings, services):
    cfg = config(settings)
    with TestClient(create_app(cfg, provider_transport=httpx.MockTransport(services))) as client:
        trip, initial = submit(client)
        assert wait_job(client, initial["id"])["status"] == "succeeded"
        base = current(client, trip)
    with TestClient(create_app(settings)) as client:
        assert current(client, trip) == base
        response = client.patch(
            f"/api/trips/{trip['id']}/itinerary",
            json={
                "expected_revision": 1,
                "expected_request_revision": 1,
                "idempotency_key": "missing-config",
                "operations": [
                    {"op": "remove", "activity_id": base["days"][0]["activities"][0]["id"]}
                ],
            },
        )
        assert response.status_code == 503
        assert current(client, trip) == base


def test_activity_introduction_and_flexible_meal_survive_local_edit(planned_client, original):
    trip, base = original
    target = base["days"][0]["activities"][1]
    description = "本地面食与传统点心风味丰富，可在附近自由品尝。"
    job = edit(
        planned_client,
        trip,
        base,
        [
            {
                "op": "replace",
                "activity_id": target["id"],
                "activity": activity_input(target, description=description, flexible_meal=True),
            }
        ],
    )
    assert job["status"] == "succeeded"
    result = current(planned_client, trip)
    edited = next(a for a in result["days"][0]["activities"] if a["id"] == target["id"])
    assert edited["description"] == description and edited["flexible_meal"]
    assert_unchanged(base, result, [1, 2])
