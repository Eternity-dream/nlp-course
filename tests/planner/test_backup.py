import httpx
import pytest
from conftest import wait_source
from fastapi.testclient import TestClient
from travel_planner.main import create_app
from travel_planner.maintenance.archive import backup, restore, verify
from travel_planner.maintenance.errors import DataError
from travel_planner.storage.database import Database

from .conftest import config, submit, wait_job
from .test_editing import current, edit


def test_backup_restores_versions_jobs_materials_preferences_and_allows_further_edit(
    settings, services, tmp_path
):
    with TestClient(
        create_app(config(settings), provider_transport=httpx.MockTransport(services))
    ) as client:
        client.put("/api/preferences", json={"expected_revision": 1, "preferences": {"节奏": "慢"}})
        source = client.post(
            "/api/knowledge", json={"title": "西安笔记", "text": "西安碑林博物馆参观笔记。"}
        ).json()
        assert wait_source(client, source["id"])["status"] == "ready"
        trip, planned = submit(client)
        assert wait_job(client, planned["id"])["status"] == "succeeded"
        original = current(client, trip)
        edited = edit(
            client,
            trip,
            original,
            [{"op": "remove", "activity_id": original["days"][0]["activities"][0]["id"]}],
        )
        assert edited["status"] == "succeeded", edited
        result = current(client, trip)
        incomplete = client.post("/api/trips", json={"destination": "杭州"}).json()
        _, pending = submit(client, incomplete, key="needs-input")
        assert wait_job(client, pending["id"])["status"] == "awaiting_input"
        # 历史草案可继续引用旧要求；维护不要求它对应最新要求。
        patched = client.patch(
            f"/api/trips/{trip['id']}", json={"expected_revision": 1, "requirements": ["慢节奏"]}
        ).json()
        saved_jobs = client.get(f"/api/trips/{trip['id']}/jobs").json()
        preferences = client.get("/api/preferences").json()
        hits = client.get("/api/knowledge/search", params={"q": "碑林"}).json()
        source_details = client.get(f"/api/knowledge/{source['id']}").json()
        assert client.get("/api/runtime").json()["idle"] is True
    archive = tmp_path / "full.zip"
    report = backup(settings.data_dir, archive)
    assert report["counts"]["jobs"] == 3
    assert report["counts"]["itinerary_revisions"] == 2
    assert report["counts"]["request_revisions"] == 3
    assert report["raw_files"] == 1
    target = tmp_path / "restored"
    restored = restore(target, archive)
    assert restored["counts"] == report["counts"]
    assert verify(target)["idle"] is True
    assert (target / "sources" / source["id"] / "raw").read_text() == "西安碑林博物馆参观笔记。"
    with TestClient(
        create_app(
            config(settings, data_dir=target), provider_transport=httpx.MockTransport(services)
        )
    ) as client:
        assert current(client, trip) == result
        assert client.get(f"/api/trips/{trip['id']}/itinerary?revision=1").json() == original
        assert client.get(f"/api/trips/{trip['id']}").json() == patched
        assert client.get(f"/api/trips/{trip['id']}/jobs").json() == saved_jobs
        assert client.get("/api/preferences").json() == preferences
        assert client.get("/api/knowledge/search", params={"q": "碑林"}).json() == hits
        assert client.get(f"/api/knowledge/{source['id']}").json() == source_details
        assert client.get(f"/api/jobs/{pending['id']}").json()["status"] == "awaiting_input"
        _, replanned = submit(client, patched, key="restored-plan")
        assert wait_job(client, replanned["id"])["status"] == "succeeded"
        latest = current(client, trip)
        job = edit(
            client,
            trip,
            latest,
            [{"op": "remove", "activity_id": latest["days"][0]["activities"][0]["id"]}],
            key="restored-edit",
        )
        assert job["status"] == "succeeded", job
        assert current(client, trip)["revision"] == 4
    with Database(target / "app.sqlite3").connect(write=True) as connection:
        connection.execute("UPDATE current_itineraries SET revision=1")
    with pytest.raises(DataError) as error:
        verify(target)
    assert error.value.code == "broken_references"
