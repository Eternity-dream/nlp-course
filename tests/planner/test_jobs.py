import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient
from travel_planner.main import create_app
from travel_planner.planner.models import JobSubmit

from .conftest import TRIP, config, submit, wait_job


def test_parallel_repeated_submissions_create_one_task(planned_client, services):
    client = planned_client
    services.block, services.entered = threading.Event(), threading.Event()
    trip = client.post("/api/trips", json=TRIP).json()
    try:

        def send(_):
            return client.post(
                f"/api/trips/{trip['id']}/jobs",
                json={"expected_revision": 1, "idempotency_key": "same-key"},
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(send, range(4)))
        assert all(response.status_code == 202 for response in responses)
        assert len({response.json()["id"] for response in responses}) == 1
        assert len(client.get(f"/api/trips/{trip['id']}/jobs").json()) == 1
        changed = client.patch(
            f"/api/trips/{trip['id']}", json={"expected_revision": 1, "budget_cents": 200000}
        ).json()
        collision = client.post(
            f"/api/trips/{trip['id']}/jobs",
            json={"expected_revision": changed["revision"], "idempotency_key": "same-key"},
        )
        assert collision.status_code == 409
        assert collision.json()["error"]["code"] == "idempotency_conflict"
    finally:
        services.block.set()


def test_restart_marks_queued_running_interrupted_preserves_waiting_and_finished(
    settings, services
):
    cfg = config(settings)
    with TestClient(create_app(cfg, provider_transport=httpx.MockTransport(services))) as client:
        trip, succeeded = submit(client)
        assert wait_job(client, succeeded["id"])["status"] == "succeeded"
        original = client.get(f"/api/trips/{trip['id']}/itinerary").json()
        waiting_trip = client.post("/api/trips", json={"destination": "西安"}).json()
        _, waiting = submit(client, waiting_trip)
        assert wait_job(client, waiting["id"])["status"] == "awaiting_input"
        # 模拟启动时遗留的任务记录，检查恢复规则和状态持久化。
        client.portal.call(client.app.state.planner.close)
        store = client.app.state.jobs
        running = store.submit(
            trip["id"], JobSubmit(expected_revision=1, idempotency_key="running")
        )
        assert store.claim()["id"] == running["id"]
        queued = store.submit(trip["id"], JobSubmit(expected_revision=1, idempotency_key="queued"))
    with TestClient(create_app(cfg, provider_transport=httpx.MockTransport(services))) as client:
        assert client.get("/api/jobs/" + running["id"]).json()["status"] == "interrupted"
        assert client.get("/api/jobs/" + queued["id"]).json()["status"] == "interrupted"
        assert client.get("/api/jobs/" + waiting["id"]).json()["status"] == "awaiting_input"
        assert client.get("/api/jobs/" + succeeded["id"]).json()["status"] == "succeeded"
        assert client.get(f"/api/trips/{trip['id']}/itinerary").json() == original
        response = client.post(
            f"/api/jobs/{running['id']}/retry",
            json={"expected_revision": 1, "idempotency_key": "retry-interrupted"},
        )
        assert response.status_code == 202
        assert response.json()["id"] != running["id"]
        assert response.json()["retry_of"] == running["id"]
        assert wait_job(client, response.json()["id"])["status"] == "succeeded"


def test_failure_is_redacted_and_other_queued_jobs_continue(planned_client, services):
    client = planned_client
    services.fail_model = True
    trip, failed = submit(client)
    result = wait_job(client, failed["id"])
    assert result["status"] == "failed"
    assert result["error"]["code"] == "provider_auth_failed"
    assert "secret" not in str(result)
    services.fail_model = False
    _, second = submit(client, trip, "second")
    assert wait_job(client, second["id"])["status"] == "succeeded"


def test_model_critical_question_does_not_occupy_executor(planned_client, services):
    services.candidate_questions = ["请补充影响购票的同行者年龄信息"]
    trip, job = submit(planned_client)
    result = wait_job(planned_client, job["id"])
    assert result["status"] == "awaiting_input"
    assert result["questions"] == services.candidate_questions
    services.candidate_questions = []
    _, second = submit(planned_client, key="second")
    assert wait_job(planned_client, second["id"])["status"] == "succeeded"
    assert planned_client.get(f"/api/trips/{trip['id']}/itinerary").status_code == 404


def test_clarification_appends_answer_and_resumes_same_job(planned_client, services):
    client = planned_client
    services.candidate_questions = ["两项预约时间重叠，请选择保留哪一项？"]
    trip = client.post("/api/trips", json={**TRIP, "known_conditions": ["住南稍门附近"]}).json()
    _, job = submit(client, trip)
    assert wait_job(client, job["id"])["status"] == "awaiting_input"
    answer = {
        "job_id": job["id"],
        "request_revision": trip["revision"],
        "questions": services.candidate_questions,
        "answer": "保留上午的博物馆预约",
    }
    updated = client.patch(
        f"/api/trips/{trip['id']}",
        json={
            "expected_revision": trip["revision"],
            "known_conditions": [*trip["known_conditions"], answer["answer"]],
            "clarifications": [answer],
        },
    ).json()
    assert updated["clarifications"] == [answer]
    services.candidate_questions = []
    assert (
        client.post(
            f"/api/jobs/{job['id']}/resume", json={"expected_revision": updated["revision"]}
        ).status_code
        == 202
    )
    assert wait_job(client, job["id"])["status"] == "succeeded"
    assert len(client.get(f"/api/trips/{trip['id']}/jobs").json()) == 1
    context = next(
        item for item in reversed(services.contexts) if item["operation"] == "plan_action"
    )
    assert context["request"]["clarifications"] == [answer]
    assert context["request"]["known_conditions"] == ["住南稍门附近", answer["answer"]]


def test_failed_save_rolls_back_revision_and_current_pointer(planned_client, services):
    client = planned_client
    trip, first = submit(client)
    assert wait_job(client, first["id"])["status"] == "succeeded"
    original = client.get(f"/api/trips/{trip['id']}/itinerary").json()
    with client.app.state.db.connect(write=True) as connection:
        connection.execute("""CREATE TRIGGER reject_itinerary_update
            BEFORE UPDATE ON current_itineraries
            BEGIN SELECT RAISE(ABORT, 'test save failure'); END""")
    _, second = submit(client, trip, "second")
    failed = wait_job(client, second["id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "planning_failed"
    assert client.get(f"/api/trips/{trip['id']}/itinerary").json() == original
    with client.app.state.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM itinerary_revisions").fetchone()[0] == 1
