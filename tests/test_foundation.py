import shutil
import sqlite3

import pytest
from conftest import wait_source
from fastapi.testclient import TestClient
from travel_planner.api.middleware import BodyLimitMiddleware
from travel_planner.main import create_app
from travel_planner.storage.database import Database
from travel_planner.storage.instance_lock import instance_lock


def test_missing_config_is_readable_and_does_not_block_storage(client):
    status = client.get("/api/config/status").json()
    assert status["ready_for_planning"] is False
    assert set(status["missing"]) == {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "AMAP_API_KEY"}
    assert status["search"][0]["auth_mode"] == "anonymous"
    assert status["search"][0]["protocol"] == "mcp"
    assert status["search"][1]["auth_mode"] == "disabled"
    assert status["connectivity_checked"] is False
    assert client.get("/api/health").json() == {"status": "ok", "schema_version": 4}
    assert client.get("/api/preferences").status_code == 200


def test_config_secrets_redacted_and_key_mode_fixed(settings):
    configured = settings.model_copy(
        update={
            "llm_base_url": "http://127.0.0.1:8001/v1",
            "llm_model": "local-model",
        }
    )
    # Settings 正常校验凭据类型。
    configured = type(settings)(
        **{
            **configured.model_dump(),
            "llm_api_key": "model-secret",
            "exa_api_key": "exa-secret",
            "amap_api_key": "map-secret",
            "_env_file": None,
        }
    )
    with TestClient(create_app(configured)) as client:
        response = client.get("/api/config/status")
        assert "secret" not in response.text
        assert response.json()["ready_for_planning"] is True
        assert response.json()["search"][0]["auth_mode"] == "key"
        assert response.json()["search"][0]["protocol"] == "rest"


@pytest.mark.parametrize(
    "changes",
    [
        {"llm_base_url": "not-a-url"},
        {"llm_base_url": "https://user:secret@example.com"},
        {"search_providers": "exa,exa"},
        {"search_providers": "unknown"},
        {"search_providers": ""},
        {"search_allow_anonymous": False},
    ],
)
def test_config_format_problems_are_returned(settings, changes):
    with TestClient(create_app(settings.model_copy(update=changes))) as client:
        status = client.get("/api/config/status").json()
        assert status["ready_for_planning"] is False
        assert status["errors"]
        assert "secret" not in str(status)


def test_openapi_error_contract_and_static_fallback(client, settings):
    schema = client.get("/api/openapi.json").json()
    assert "/api/trips/{trip_id}" in schema["paths"]
    assert "/api/knowledge/files" in schema["paths"]
    for path in ["/api/not-an-api", "/missing.js"]:
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert client.get("/").json()["docs"] == "/api/docs"
    settings.web_dist_dir.mkdir()
    (settings.web_dist_dir / "index.html").write_text("<html>app</html>")
    (settings.web_dist_dir / "asset.js").write_text("const app = true;")
    assert client.get("/trips/example").text == "<html>app</html>"
    assert client.get("/asset.js").text == "const app = true;"
    assert client.get("/api/not-an-api").status_code == 404


def test_streaming_body_limit_including_missing_content_length(client):
    # 用较小阈值验证分块请求，不为测试分配额外的大文件。
    app = client.app
    app.middleware_stack = BodyLimitMiddleware(app.middleware_stack, max_bytes=32)
    response = client.post("/api/knowledge", content=iter([b"x" * 20, b"y" * 20]))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_unexpected_errors_use_safe_error_contract(settings, monkeypatch):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:

        def unavailable(*args, **kwargs):
            raise RuntimeError("private data")

        monkeypatch.setattr(client.app.state.repository, "get_trip", unavailable)
        response = client.get("/api/trips/example")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "private data" not in response.text


def test_database_rejects_newer_schema_without_modification(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="数据库版本"):
        Database(path).migrate()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
        assert connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def test_database_migration_is_transactional(tmp_path, monkeypatch):
    from travel_planner.storage import database

    monkeypatch.setitem(
        database.MIGRATIONS, 1, ["CREATE TABLE temporary_table(id TEXT)", "INVALID SQL"]
    )
    path = tmp_path / "bad.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        Database(path).migrate()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def test_data_directory_has_exclusive_process_lock(settings):
    with instance_lock(settings.data_dir), pytest.raises(RuntimeError, match="数据目录已被"):
        with instance_lock(settings.data_dir):
            pass


def test_restart_recovers_pending_and_marks_processing_failed(settings):
    with TestClient(create_app(settings)) as client:
        knowledge = client.app.state.knowledge
        knowledge.close()
        pending = knowledge.create("text", "等待导入", "碑林笔记".encode())
        interrupted = knowledge.create("text", "中断资料", "西安资料".encode())
        with client.app.state.db.connect(write=True) as connection:
            connection.execute(
                "UPDATE knowledge_sources SET status='processing' WHERE id=?", (interrupted["id"],)
            )
    with TestClient(create_app(settings)) as client:
        assert wait_source(client, pending["id"])["status"] == "ready"
        source = client.get(f"/api/knowledge/{interrupted['id']}").json()
        assert source["status"] == "failed"
        assert source["error"]["code"] == "import_interrupted"
        assert source["chunks"] == []


def test_failed_file_cleanup_is_retried(client, monkeypatch):
    source = client.post("/api/knowledge", json={"title": "待删", "text": "碑林笔记"}).json()
    wait_source(client, source["id"])
    from pathlib import Path

    original = Path.unlink

    def deny_delete(path, **kwargs):
        if path.name == "raw":
            raise PermissionError
        return original(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny_delete)
        assert client.delete(f"/api/knowledge/{source['id']}").status_code == 204
        assert client.get("/api/knowledge/search", params={"q": "碑林"}).json() == []
    client.app.state.knowledge.cleanup_files()
    assert not (client.app.state.knowledge.root / source["id"]).exists()


def test_stopped_data_directory_can_be_restored(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        trip = client.post("/api/trips", json={"destination": "西安"}).json()
        client.put("/api/preferences", json={"expected_revision": 1, "preferences": {"节奏": "慢"}})
        source = client.post(
            "/api/knowledge", json={"title": "旅行笔记", "text": "西安碑林"}
        ).json()
        wait_source(client, source["id"])
    backup = tmp_path / "restored"
    shutil.copytree(settings.data_dir, backup)
    with TestClient(create_app(settings.model_copy(update={"data_dir": backup}))) as client:
        assert client.get(f"/api/trips/{trip['id']}").status_code == 200
        assert client.get("/api/preferences").json()["preferences"] == {"节奏": "慢"}
        assert client.get("/api/knowledge/search", params={"q": "碑林"}).json()
        assert (backup / "sources" / source["id"] / "raw").read_text() == "西安碑林"
