import json

import httpx
import pytest
from pydantic import ValidationInfo, model_validator
from travel_planner.domain.models import Model
from travel_planner.providers.errors import ProviderError
from travel_planner.providers.llm import ChatModel

from .conftest import configured, make_http

pytestmark = pytest.mark.anyio


class Answer(Model):
    source_ids: list[str]

    @model_validator(mode="after")
    def references(self, info: ValidationInfo):
        if set(self.source_ids) - info.context["source_ids"]:
            raise ValueError("来源引用不存在")
        return self


def completion(content, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}


async def test_compatible_base_url_model_and_json_contract(settings):
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == "https://model.example.com/custom/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer model-secret"
        body = json.loads(request.content)
        assert body["model"] == "custom-model"
        assert "response_format" not in body
        assert set(body) == {"model", "messages", "stream"}
        assert "JSON Schema" in body["messages"][0]["content"]
        return httpx.Response(200, json=completion('{"source_ids":["s1"]}'))

    client, http = make_http(handle)
    async with client:
        result = await ChatModel(configured(settings), http).generate(
            [{"role": "user", "content": "公开样本"}], Answer, context={"source_ids": {"s1"}}
        )
        assert result.source_ids == ["s1"]
    assert len(requests) == 1


@pytest.mark.parametrize(
    "bad",
    [
        completion(""),
        completion("not JSON"),
        completion('{"source_ids":[]}', "length"),
        completion('{"source_ids":["invented"]}'),
        completion('{"source_ids":'),
        {"choices": []},
        completion(None),
        completion('{"source_ids":[],"unexpected":true}'),
    ],
)
async def test_output_repairs_bounded_and_unknown_references_rejected(settings, bad):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=bad)

    client, http = make_http(handle)
    async with client:
        with pytest.raises(ProviderError) as error:
            await ChatModel(configured(settings), http).generate(
                [], Answer, context={"source_ids": set()}
            )
    assert error.value.reason == "model_invalid_output"
    assert len(calls) == 3


async def test_repair_can_succeed(settings):
    replies = iter([completion("oops"), completion('{"source_ids":["s1"]}')])
    client, http = make_http(lambda request: httpx.Response(200, json=next(replies)))
    async with client:
        result = await ChatModel(configured(settings), http).generate(
            [], Answer, context={"source_ids": {"s1"}}
        )
        assert result.source_ids == ["s1"]


async def test_explicitly_unsupported_json_mode_retries_without_option(settings):
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if "response_format" in payload:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "param": "response_format",
                        "code": "unsupported_parameter",
                        "message": "response_format unsupported model-secret",
                    }
                },
            )
        return httpx.Response(200, json=completion('{"source_ids":[]}'))

    client, http = make_http(handle)
    async with client:
        model = ChatModel(configured(settings, llm_json_mode=True), http)
        await model.generate([], Answer, context={"source_ids": set()})
        await model.generate([], Answer, context={"source_ids": set()})
    assert len(calls) == 3
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert all("response_format" not in call for call in calls[1:])


async def test_invalid_model_parameters_stop_without_retry(settings):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"param": "model", "message": "secret"}})

    client, http = make_http(handle)
    async with client:
        with pytest.raises(ProviderError) as error:
            await ChatModel(configured(settings), http).generate([], Answer)
    assert error.value.reason == "request_invalid"
    assert "secret" not in str(error.value.payload())
    assert len(calls) == 1
