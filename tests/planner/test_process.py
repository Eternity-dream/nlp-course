import multiprocessing
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from travel_planner.main import create_app

from .conftest import TRIP, TravelServices, config


def serve(settings, port, block_candidates):
    services = TravelServices()
    if block_candidates:
        services.block = threading.Event()
        if isinstance(block_candidates, str):
            services.block_operation = block_candidates
    app = create_app(settings, provider_transport=httpx.MockTransport(services))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="critical", access_log=False)


def poll(client, path, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            if response.status_code == 200 and predicate(response.json()):
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.03)
    pytest.fail(f"HTTP 等待超时：{path}")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux 容器进程强制终止验证")
def test_real_http_background_planning_survives_browser_disconnect_and_recovers_after_kill(
    settings,
):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    context = multiprocessing.get_context("fork")
    cfg = config(settings)
    base = f"http://127.0.0.1:{port}"

    def start(block=False):
        process = context.Process(target=serve, args=(cfg, port, block))
        process.start()
        with httpx.Client(base_url=base, timeout=2) as client:
            poll(client, "/api/health", lambda body: body["status"] == "ok")
        return process

    process = start()
    try:
        with httpx.Client(base_url=base, timeout=2) as browser:
            trip = browser.post("/api/trips", json=TRIP).json()
            job = browser.post(
                f"/api/trips/{trip['id']}/jobs",
                json={"expected_revision": 1, "idempotency_key": "first"},
            ).json()
        # 提交后断开客户端，后台任务仍能完成。
        with httpx.Client(base_url=base, timeout=2) as reopened:
            poll(reopened, "/api/jobs/" + job["id"], lambda body: body["status"] == "succeeded")
            original = reopened.get(f"/api/trips/{trip['id']}/itinerary").json()
            edit = reopened.patch(
                f"/api/trips/{trip['id']}/itinerary",
                json={
                    "expected_revision": 1,
                    "expected_request_revision": 1,
                    "idempotency_key": "http-edit",
                    "operations": [
                        {"op": "remove", "activity_id": original["days"][0]["activities"][0]["id"]}
                    ],
                },
            ).json()
        with httpx.Client(base_url=base, timeout=2) as reopened:
            poll(reopened, "/api/jobs/" + edit["id"], lambda body: body["status"] == "succeeded")
            updated = reopened.get(f"/api/trips/{trip['id']}/itinerary").json()
            assert updated["revision"] == 2
            assert updated["days"][1:] == original["days"][1:]
            original = updated
    finally:
        process.terminate()
        process.join(8)
        if process.is_alive():
            process.kill()
            process.join(3)
    process = start(block="extract_facts")
    try:
        with httpx.Client(base_url=base, timeout=2) as client:
            running = client.patch(
                f"/api/trips/{trip['id']}/itinerary",
                json={
                    "expected_revision": 2,
                    "expected_request_revision": 1,
                    "idempotency_key": "interrupted-edit",
                    "operations": [
                        {"op": "remove", "activity_id": original["days"][0]["activities"][-1]["id"]}
                    ],
                },
            ).json()
            poll(client, "/api/jobs/" + running["id"], lambda body: body["stage"] == "evidence")
            queued = client.post(
                f"/api/trips/{trip['id']}/jobs",
                json={"expected_revision": 1, "idempotency_key": "queued"},
            ).json()
            assert queued["status"] == "queued"
        process.kill()
        process.join(5)
    finally:
        if process.is_alive():
            process.kill()
            process.join(3)
    process = start()
    try:
        with httpx.Client(base_url=base, timeout=2) as client:
            for stopped in [running, queued]:
                assert client.get("/api/jobs/" + stopped["id"]).json()["status"] == "interrupted"
            assert client.get(f"/api/trips/{trip['id']}/itinerary").json() == original
            retry = client.post(
                f"/api/jobs/{running['id']}/retry",
                json={"expected_revision": 1, "idempotency_key": "retry"},
            ).json()
            assert retry["retry_of"] == running["id"]
            assert retry["kind"] == "edit"
            poll(client, "/api/jobs/" + retry["id"], lambda body: body["status"] == "succeeded")
            assert client.get(f"/api/trips/{trip['id']}/itinerary").json()["revision"] == 3
    finally:
        process.terminate()
        process.join(8)
        if process.is_alive():
            process.kill()
            process.join(3)
