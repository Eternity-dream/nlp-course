import asyncio
import json
import time
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from travel_planner.main import create_app

TRIP = {
    "destination": "西安",
    "start_date": "2026-12-01",
    "end_date": "2026-12-03",
    "travelers": 2,
    "budget_cents": 100000,
}


def config(settings, **changes):
    return type(settings)(
        **{
            **settings.model_dump(),
            "_env_file": None,
            "llm_base_url": "https://model.example.com/v1",
            "llm_api_key": "model-secret",
            "llm_model": "test-model",
            "amap_api_key": "map-secret",
            "exa_api_key": "exa-secret",
            "search_providers": "exa",
            "provider_timeout_seconds": 3,
            **changes,
        }
    )


def wait_job(client, job_id, expected=None, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/api/jobs/" + job_id)
        assert response.status_code == 200, response.text
        job = response.json()
        if (expected and job["status"] == expected) or (
            not expected
            and job["status"] in {"succeeded", "failed", "awaiting_input", "interrupted"}
        ):
            return job
        time.sleep(0.01)
    pytest.fail(f"任务未结束：{job}")


def submit(client, trip=None, key="first"):
    if trip is None:
        response = client.post("/api/trips", json=TRIP)
        assert response.status_code == 201, response.text
        trip = response.json()
    response = client.post(
        f"/api/trips/{trip['id']}/jobs",
        json={"expected_revision": trip["revision"], "idempotency_key": key},
    )
    assert response.status_code == 202, response.text
    return trip, response.json()


class TravelServices:
    """经过真实适配器的固定供应商响应，覆盖完整规划数据流。"""

    def __init__(self):
        self.requests = []
        self.operations = []
        self.city = "西安"
        self.days = [date(2026, 12, 1) + timedelta(days=i) for i in range(3)]
        self.break_draft = None
        self.no_sources = False
        self.empty_facts = False
        self.candidate_questions = []
        self.block = None
        self.entered = None
        self.fail_model = False
        self.closed = False
        self.long_route = False
        self.bad_first_draft = False
        self.draft_calls = 0
        self.contexts = []
        self.extra_places = []
        self.prices = {"P1": 10000, "P2": 3000, "P3": 0}
        self.block_operation = "plan_action"
        self.constraint_conflicts = []
        self.quality_findings = []
        self.agent_decisions = []
        self.transform_result = None

    def places(self):
        return [
            {
                "id": f"P{i}",
                "name": name,
                "address": f"{self.city}路{i}号",
                "location": f"108.{950 + i}000,34.250000",
                "cityname": self.city + "市",
                "citycode": "029" if self.city == "西安" else "0571",
                "adcode": "610103",
            }
            for i, name in enumerate(["博物馆", "餐馆", "公园"], 1)
        ] + self.extra_places

    def quote(self, place, day):
        if self.closed and place["id"] == "P1":
            return f"{place['name']} {place['address']} {day} 全天闭馆。"
        amount = self.prices[place["id"]] / 100
        return f"{place['name']} {place['address']} {day} 开放时间08:00-22:00，每人{amount:g}元。"

    async def __call__(self, request):
        self.requests.append(request)
        if request.url.host == "model.example.com":
            if self.fail_model:
                return httpx.Response(401, text="secret")
            data = json.loads(request.content)
            context = json.loads(
                next(item["content"] for item in data["messages"] if item["role"] == "user")
            )
            operation = context["operation"]
            self.contexts.append(context)
            self.operations.append(operation)
            if operation == self.block_operation:
                if self.entered:
                    self.entered.set()
                if self.block:
                    while not self.block.is_set():
                        await asyncio.sleep(0.01)
            if operation == "plan_action":
                self.city = context["request"]["destination"]
                start, end = (
                    date.fromisoformat(context["request"]["start_date"]),
                    date.fromisoformat(context["request"]["end_date"]),
                )
                self.days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
                if self.agent_decisions:
                    action = self.agent_decisions.pop(0)
                elif self.candidate_questions:
                    action = {"kind": "ask", "questions": self.candidate_questions}
                elif not context["intent"]:
                    action = {"kind": "interpret", "intent": {"summary": "按用户条件安排旅行"}}
                elif not context["places"]:
                    action = {
                        "kind": "places",
                        "queries": [
                            {"query": "博物馆", "purpose": "sightseeing"},
                            {"query": "餐馆", "purpose": "meal"},
                            {"query": "公园", "purpose": "leisure"},
                        ],
                    }
                elif not any(row["action"] == "research" for row in context["recent_actions"]):
                    action = {
                        "kind": "research",
                        "queries": [
                            {"place_id": place["id"], "topic": "开放时间 票价 官方"}
                            for place in context["places"]
                        ],
                    }
                elif not context["current_draft"] or (
                    context["validation"]
                    and context["validation"]["status"] == "blocked"
                    and context["remaining"]["drafts"] > 0
                ):
                    action = {
                        "kind": "draft",
                        "instructions": "按审核调整行程",
                        "selected_place_ids": [place["id"] for place in context["places"]],
                    }
                else:
                    action = {"kind": "finish"}
                result = {"purpose": "继续规划", "action": action}
            elif operation == "plan_research":
                result = {
                    "queries": [
                        {"place_id": place["id"], "topic": "开放时间 票价 官方"}
                        for place in context["places"]
                    ]
                }
            elif operation == "review_itinerary":
                result = {"findings": self.quality_findings}
            elif operation == "introductions":
                result = {
                    "introductions": [
                        {
                            "activity_id": a["activity_id"],
                            "description": "本地面食与点心风味丰富。"
                            if a["kind"] == "meal"
                            else "这里可以了解城市的历史与文化。",
                        }
                        for a in context["activities"]
                    ]
                }
            elif operation == "verify_facts":
                result = {
                    "checks": [
                        {
                            "claim_index": claim["claim_index"],
                            "identity": "matched",
                            "interpretation": "supported",
                            "authority": "official",
                            "reason": "测试官方正文与地点和日期一致",
                        }
                        for claim in context["claims"]
                    ]
                }
            elif operation == "extract_facts":
                result = {"claims": []}
                if not self.empty_facts:
                    for place in context["places"]:
                        source = next(
                            source
                            for source in context["sources"]
                            if place["name"] in source["text"]
                        )
                        for day in [date.fromisoformat(day) for day in context["dates"]]:
                            raw = next(p for p in self.places() if p["id"] == place["provider_id"])
                            closed = self.closed and place["provider_id"] == "P1"
                            base = {
                                "place_id": place["id"],
                                "on_date": day.isoformat(),
                                "source_id": source["id"],
                                "quote": self.quote(raw, day),
                                "identity_quote": self.quote(raw, day),
                                "applicability": {
                                    "mode": "exact",
                                    "start_date": day.isoformat(),
                                    "quote": self.quote(raw, day),
                                },
                            }
                            result["claims"].append(
                                {
                                    **base,
                                    "kind": "opening_hours",
                                    "closed": closed,
                                    "windows": []
                                    if closed
                                    else [{"start_minute": 480, "end_minute": 1320}],
                                }
                            )
                            if not closed:
                                result["claims"].append(
                                    {
                                        **base,
                                        "kind": "meal_price"
                                        if place["provider_id"] == "P2"
                                        else "ticket_price",
                                        "amount_cents": self.prices[place["provider_id"]],
                                    }
                                )
            elif operation == "review_constraints":
                result = {"conflicts": self.constraint_conflicts}
            else:
                self.draft_calls += 1
                if self.break_draft:
                    return httpx.Response(
                        200,
                        json={
                            "choices": [
                                {"finish_reason": "stop", "message": {"content": self.break_draft}}
                            ]
                        },
                    )
                days = []
                for index, day in enumerate(self.days):
                    activities = []
                    for i, period in enumerate(["morning", "noon", "evening"], 1):
                        place_id = f"amap:P{i}"
                        kind = "meal" if i == 2 else "sightseeing"
                        fact = next(
                            (
                                fact
                                for fact in context["evidence"]
                                if fact["place_id"] == place_id
                                and fact["on_date"] == day.isoformat()
                                and fact["kind"] != "opening_hours"
                            ),
                            None,
                        )
                        cost = {
                            "category": "meal" if i == 2 else "ticket",
                            "amount_cents": fact["amount_cents"] if fact else None,
                            "evidence_id": fact["id"] if fact else None,
                        }
                        activities.append(
                            {
                                "id": f"d{index}a{i}",
                                "place_id": place_id,
                                "kind": kind,
                                "period": period,
                                "start_minute": [540, 720, 1080][i - 1],
                                "end_minute": [660, 780, 1200][i - 1],
                                "costs": [cost],
                            }
                        )
                    if self.bad_first_draft and self.draft_calls == 1:
                        activities[1]["start_minute"] = 650
                    days.append({"on_date": day.isoformat(), "activities": activities})
                result = {
                    "days": days,
                    "assumptions": ["按分钟安排弹性时段"],
                    "constraint_conflicts": self.constraint_conflicts,
                }
            if self.transform_result:
                result = self.transform_result(context, result)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(result)}}
                    ]
                },
            )
        if request.url.host == "restapi.amap.com":
            if request.url.path.endswith("text"):
                assert request.url.params["region"] == self.city
                keyword = request.url.params["keywords"]
                return httpx.Response(
                    200,
                    json={
                        "status": "1",
                        "pois": [p for p in self.places() if p["name"] == keyword],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "route": {
                        "paths": [
                            {
                                "distance": "1000",
                                "cost": {"duration": "20000" if self.long_route else "600"},
                            }
                        ]
                    },
                },
            )
        if request.url.host == "api.exa.ai":
            if self.no_sources:
                return httpx.Response(503)
            data = json.loads(request.content)
            query = data["query"]
            place = next(p for p in self.places() if p["name"] in query)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": f"https://example.com/{place['id']}",
                            "title": place["name"],
                            "text": "\n".join(self.quote(place, day) for day in self.days),
                        }
                    ]
                },
            )
        raise AssertionError(str(request.url))


@pytest.fixture
def services():
    return TravelServices()


@pytest.fixture
def planned_client(settings, services):
    with TestClient(
        create_app(config(settings), provider_transport=httpx.MockTransport(services))
    ) as client:
        yield client
