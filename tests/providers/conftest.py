import json
from pathlib import Path

import httpx
import pytest
from travel_planner.providers.http import ProviderHTTP


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def samples():
    root = Path(__file__).parents[1] / "fixtures" / "providers"
    return {name: json.loads((root / (name + ".json")).read_text()) for name in ["search", "amap"]}


def make_http(handler, timeout=5):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)
    return client, ProviderHTTP(client, timeout)


def configured(settings, **changes):
    return type(settings)(
        **{
            **settings.model_dump(),
            "llm_base_url": "https://model.example.com/custom/v1/",
            "llm_api_key": "model-secret",
            "llm_model": "custom-model",
            "exa_api_key": "exa-secret",
            "tavily_api_key": "tavily-secret",
            "anysearch_api_key": "anysearch-secret",
            "amap_api_key": "amap-secret",
            **changes,
            "_env_file": None,
        }
    )
