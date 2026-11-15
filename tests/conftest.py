import time

import pytest
from fastapi.testclient import TestClient
from travel_planner.config import Settings
from travel_planner.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        web_dist_dir=tmp_path / "web",
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        amap_api_key="",
        exa_api_key="",
        tavily_api_key="",
        anysearch_api_key="",
        search_providers="exa,tavily,anysearch",
        search_allow_anonymous=True,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def wait_source(client, source_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/knowledge/{source_id}")
        assert response.status_code == 200, response.text
        source = response.json()
        if source["status"] in {"ready", "failed"}:
            return source
        time.sleep(0.025)
    pytest.fail(f"导入未在 {timeout} 秒内结束")
