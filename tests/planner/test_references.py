from conftest import wait_source

from planner.conftest import TRIP, submit, wait_job


def test_selected_references_and_natural_preferences_reach_model(planned_client, services):
    client = planned_client
    sources = []
    for marker in ["SELECTED_PRIVATE", "UNSELECTED_PRIVATE"]:
        source = client.post(
            "/api/knowledge",
            json={
                "title": marker,
                "text": f"西安博物馆 {marker}",
            },
        ).json()
        assert wait_source(client, source["id"])["status"] == "ready"
        sources.append(source["id"])
    profile = client.put(
        "/api/preferences",
        json={
            "expected_revision": 1,
            "notes": "喜欢历史建筑，节奏宽松，不吃辣",
        },
    ).json()
    trip = client.post(
        "/api/trips",
        json={
            **TRIP,
            "reference_ids": sources[:1],
            "requirements": ["本次第二天紧凑一些"],
        },
    ).json()
    _, job = submit(client, trip)
    assert wait_job(client, job["id"])["status"] == "succeeded"
    context = services.contexts[0]
    assert "SELECTED_PRIVATE" in str(context["personal_material"])
    assert "UNSELECTED_PRIVATE" not in str(context["personal_material"])
    assert context["preference_notes"] == profile["notes"]
    assert client.get("/api/preferences").json() == profile
    services.contexts.clear()
    updated = client.patch(
        f"/api/trips/{trip['id']}",
        json={
            "expected_revision": trip["revision"],
            "reference_ids": [],
        },
    ).json()
    _, job = submit(client, updated, key="without-references")
    assert wait_job(client, job["id"])["status"] == "succeeded"
    assert all(not context.get("personal_material") for context in services.contexts)


def test_failed_reference_blocks_planning_and_can_retry(planned_client, services):
    client = planned_client
    source = client.post("/api/knowledge", json={"title": "笔记", "text": "西安旅行"}).json()
    wait_source(client, source["id"])
    with client.app.state.repository.db.connect(write=True) as connection:
        connection.execute(
            "UPDATE knowledge_sources SET status='failed' WHERE id=?", (source["id"],)
        )
    trip = client.post("/api/trips", json={**TRIP, "reference_ids": [source["id"]]}).json()
    _, job = submit(client, trip)
    result = wait_job(client, job["id"])
    assert result["status"] == "failed"
    assert result["error"]["code"] == "reference_failed"
    assert services.contexts == []
    assert client.post(f"/api/knowledge/{source['id']}/retry").status_code == 200
    assert wait_source(client, source["id"])["status"] == "ready"
    response = client.post(
        f"/api/jobs/{job['id']}/retry",
        json={
            "expected_revision": trip["revision"],
            "idempotency_key": "retry-reference",
        },
    )
    assert response.status_code == 202
    assert wait_job(client, response.json()["id"])["status"] == "succeeded"


def test_reference_validation_and_deleted_reference(planned_client):
    client = planned_client
    assert client.post("/api/trips", json={**TRIP, "reference_ids": ["missing"]}).status_code == 422
    source = client.post("/api/knowledge", json={"title": "参考", "text": "西安旅行"}).json()
    wait_source(client, source["id"])
    trip = client.post("/api/trips", json={**TRIP, "reference_ids": [source["id"]]}).json()
    assert client.delete(f"/api/knowledge/{source['id']}").status_code == 204
    _, job = submit(client, trip)
    assert wait_job(client, job["id"])["error"]["code"] == "reference_missing"
    assert (
        client.patch(
            f"/api/trips/{trip['id']}",
            json={
                "expected_revision": trip["revision"],
                "reference_ids": [],
            },
        ).status_code
        == 200
    )
