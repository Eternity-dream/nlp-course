import json
import sqlite3

import httpx
from fastapi.testclient import TestClient
from travel_planner.main import create_app
from travel_planner.storage.database import MIGRATIONS, Database

from .conftest import configured
from .test_llm import completion


def test_configuration_reads_do_not_call_services(client):
    def fail(*args, **kwargs):
        raise AssertionError("读配置不应调用模型、地图或搜索")

    client.app.state.services.model.generate = fail
    client.app.state.services.amap.places = fail
    client.app.state.services.search.scope = fail
    assert client.get("/api/config/status").status_code == 200
    assert client.get("/api/config/checks").json() == []


def test_explicit_checks_call_selected_service_record_redacted_result_and_persist(
    settings, samples
):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.host == "model.example.com":
            assert request.headers["authorization"] == "Bearer model-secret"
            return httpx.Response(200, json=completion('{"ok":true}'))
        if request.url.host == "api.exa.ai":
            return httpx.Response(503)
        if request.url.host == "api.deepseek.com":
            assert request.headers["authorization"] == "Bearer deepseek-secret"
            assert json.loads(request.content)["query"].startswith("杭州 ")
            return httpx.Response(200, json=samples["search"]["deepseek"])
        assert request.url.params["region"] == "杭州"
        return httpx.Response(200, json=samples["amap"])

    config = configured(settings)
    with TestClient(create_app(config, provider_transport=httpx.MockTransport(handle))) as client:
        assert client.get("/api/config/status").status_code == 200
        assert calls == []
        for service in ["llm", "search", "amap"]:
            result = client.post(
                "/api/config/check", json={"service": service, "destination": "杭州"}
            )
            assert result.status_code == 200
            assert result.json()["success"] is True
            assert "secret" not in result.text
        assert len(calls) == 4
        records = client.get("/api/config/checks").json()
        assert len(records) == 3
        assert records[0]["id"]
    with TestClient(create_app(config, provider_transport=httpx.MockTransport(handle))) as client:
        assert client.get("/api/config/checks").json() == records
        assert len(calls) == 4


def test_check_failure_saved_and_generated_credentials_not_leaked(settings):
    config = configured(settings, search_providers="anysearch", anysearch_api_key="")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            402, json={"message": "username=generated password=generated api_key=generated"}
        )
    )
    with TestClient(create_app(config, provider_transport=transport)) as client:
        result = client.post("/api/config/check", json={"service": "search"})
        assert result.status_code == 200
        assert result.json()["success"] is False
        assert (
            result.json()["details"]["attempts"][0]["error"]["code"] == "provider_quota_exhausted"
        )
        assert "generated" not in result.text
        assert "generated" not in client.get("/api/config/checks").text


def test_unconfigured_checks_are_specific_errors_without_network(settings):
    def fail(request):
        raise AssertionError("缺少密钥时不应调用服务")

    with TestClient(create_app(settings, provider_transport=httpx.MockTransport(fail))) as client:
        for service in ["llm", "amap"]:
            result = client.post("/api/config/check", json={"service": service})
            assert result.json()["success"] is False
            assert result.json()["details"]["error"]["code"] == "provider_not_configured"


def test_database_v1_upgrades_and_retains_data(tmp_path):
    path = tmp_path / "app.sqlite3"
    with sqlite3.connect(path) as connection:
        for statement in MIGRATIONS[1]:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        connection.execute("INSERT INTO preferences VALUES (1,1,?,?)", ('{"节奏":"慢"}', "time"))
    Database(path).migrate()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert json.loads(connection.execute("SELECT payload FROM preferences").fetchone()[0]) == {
            "节奏": "慢"
        }
        assert connection.execute("SELECT count(*) FROM service_checks").fetchone()[0] == 0
