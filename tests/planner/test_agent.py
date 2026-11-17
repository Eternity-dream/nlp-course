import json

import pytest
from pydantic import ValidationError
from travel_planner.planner.agent_models import AgentDecision, AgentState, QualityReview
from travel_planner.planner.workflow import effective_request

from .conftest import TRIP, submit, wait_job


def test_model_review_drives_new_query_and_rewrites_draft(planned_client, services):
    """第一次草案重复，审核反馈促使模型补查，再用新候选修改草案。"""
    services.extra_places = [
        {
            "id": "P4",
            "name": "另一座博物馆",
            "address": "西安路4号",
            "location": "108.96,34.26",
            "cityname": "西安市",
            "citycode": "029",
            "adcode": "610103",
            "type": "风景名胜;博物馆",
        }
    ]
    services.prices["P4"] = 5000

    def transform(context, result):
        operation = context["operation"]
        if operation == "plan_action" and (context.get("review") or {}).get("findings"):
            if not any(p["id"] == "amap:P4" for p in context["places"]):
                result["action"] = {
                    "kind": "places",
                    "queries": [{"query": "另一座博物馆", "purpose": "sightseeing"}],
                }
        if operation == "draft" and any(p["id"] == "amap:P4" for p in context["places"]):
            activity = result["days"][1]["activities"][0]
            activity["place_id"] = "amap:P4"
            activity["costs"] = [{"category": "ticket", "amount_cents": None}]
        if (
            operation == "review_itinerary"
            and context["draft"]["days"][1]["activities"][0]["place_id"] == "amap:P1"
        ):
            result["findings"] = [
                {
                    "category": "repetition",
                    "severity": "conflict",
                    "activity_ids": ["d1a1"],
                    "message": "第二天重复参观同一博物馆",
                    "suggestion": "补查另一座博物馆并替换第二天活动",
                }
            ]
        return result

    services.transform_result = transform
    trip, job = submit(planned_client)
    done = wait_job(planned_client, job["id"])
    assert done["status"] == "succeeded", done
    result = planned_client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert result["days"][1]["activities"][0]["place_id"] == "amap:P4"
    trace = result["planning_context"]["trace"]
    actions = [row["action"] for row in trace]
    first_draft = actions.index("draft")
    assert "places" in actions[first_draft + 1 :]
    assert actions.count("draft") == 2
    draft_contexts = [row for row in services.contexts if row["operation"] == "draft"]
    assert draft_contexts[1]["current_draft"] is not None
    assert draft_contexts[1]["review"]["findings"][0]["category"] == "repetition"
    assert not any(row["rule"] == "quality_repetition" for row in result["validation"]["issues"])
    assert done["progress_estimate"] == 100


def test_loop_limit_preserves_reviewed_draft(planned_client, services, monkeypatch):
    monkeypatch.setattr("travel_planner.planner.workflow.MAX_STEPS", 4)
    trip, job = submit(planned_client)
    done = wait_job(planned_client, job["id"])
    assert done["status"] == "succeeded"
    result = planned_client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert result["days"]
    assert result["validation"]["status"] == "needs_review"
    assert any(row["rule"] == "planning_incomplete" for row in result["validation"]["issues"])
    assert "review_itinerary" in services.operations


def test_no_draft_limit_returns_retryable_failure(planned_client, services, monkeypatch):
    monkeypatch.setattr("travel_planner.planner.workflow.MAX_STEPS", 2)
    _, job = submit(planned_client)
    done = wait_job(planned_client, job["id"])
    assert done["status"] == "failed"
    assert done["error"]["retryable"]
    checkpoint = planned_client.app.state.jobs.checkpoint(done)
    assert checkpoint["places"]
    assert checkpoint["intent"]


def test_retry_reuses_public_results_and_has_a_new_budget(planned_client, services, monkeypatch):
    monkeypatch.setattr("travel_planner.planner.workflow.MAX_STEPS", 2)
    trip, job = submit(planned_client)
    failed = wait_job(planned_client, job["id"])
    assert failed["status"] == "failed"
    calls = len(
        [request for request in services.requests if request.url.path.endswith("/place/text")]
    )
    monkeypatch.setattr("travel_planner.planner.workflow.MAX_STEPS", 12)
    retry = planned_client.post(
        f"/api/jobs/{job['id']}/retry",
        json={
            "expected_revision": trip["revision"],
            "idempotency_key": "retry-with-checkpoint",
        },
    ).json()
    done = wait_job(planned_client, retry["id"])
    assert done["status"] == "succeeded"
    assert (
        len([request for request in services.requests if request.url.path.endswith("/place/text")])
        == calls
    )
    state = AgentState.model_validate(planned_client.app.state.jobs.checkpoint(done))
    assert state.review is not None


def test_actions_and_review_cannot_reference_invented_ids():
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(
            {
                "purpose": "补查",
                "action": {
                    "kind": "research",
                    "queries": [{"place_id": "invented", "topic": "票价"}],
                },
            },
            context={"place_ids": {"real"}},
        )
    with pytest.raises(ValidationError):
        QualityReview.model_validate(
            {
                "findings": [
                    {
                        "category": "repetition",
                        "severity": "conflict",
                        "activity_ids": ["invented"],
                        "message": "重复",
                        "suggestion": "替换",
                    }
                ]
            },
            context={"activity_ids": {"real"}, "fact_ids": set()},
        )


def test_interpreted_time_requires_user_quote_and_preserves_structured_input():
    request = {
        **TRIP,
        "requirements": [],
        "known_conditions": ["12月1日下午两点到"],
        "time_constraints": [
            {"kind": "arrival", "on_date": "2026-12-01", "minute": 900, "place_name": None}
        ],
    }
    from datetime import date

    decision = {
        "purpose": "整理抵达时间",
        "action": {
            "kind": "interpret",
            "intent": {
                "summary": "西安三天",
                "interpreted_times": [
                    {
                        "source_field": "known_conditions",
                        "source_index": 0,
                        "quote": "12月1日下午两点到",
                        "constraint": {"kind": "arrival", "on_date": "2026-12-01", "minute": 840},
                    }
                ],
            },
        },
    }
    intent = AgentDecision.model_validate(
        decision,
        context={
            "request": request,
            "dates": [date(2026, 12, 1)],
        },
    ).action.intent
    assert effective_request(request, intent)["time_constraints"][0]["minute"] == 900
    decision["action"]["intent"]["interpreted_times"][0]["quote"] = "上午八点到"
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(
            decision, context={"request": request, "dates": [date(2026, 12, 1)]}
        )


def test_checkpoint_does_not_store_personal_material_text(planned_client):
    trip, job = submit(planned_client)
    done = wait_job(planned_client, job["id"])
    checkpoint = planned_client.app.state.jobs.checkpoint(done)
    assert "personal_material" not in json.dumps(checkpoint)
    assert checkpoint["draft"] is not None


def test_checkpoint_can_restore_findings_and_retry_after_review(planned_client, services):
    services.quality_findings = [
        {
            "category": "repetition",
            "severity": "conflict",
            "activity_ids": ["d1a1"],
            "message": "重复游览",
            "suggestion": "更换景点",
        }
    ]
    trip, job = submit(planned_client)
    done = wait_job(planned_client, job["id"])
    state = AgentState.model_validate(planned_client.app.state.jobs.checkpoint(done))
    assert state.review.findings[0].activity_ids == ["d1a1"]
    assert state.validation.status == "blocked"
    assert done["progress_estimate"] == 100


def test_review_timeout_keeps_unknown_and_is_not_repeated_on_finish(
    planned_client, services, monkeypatch
):
    from travel_planner.providers.errors import ProviderError

    model = planned_client.app.state.services.model
    original = model.generate
    calls = []

    async def generate(messages, schema, **kwargs):
        if schema.__name__ == "QualityReview":
            calls.append(schema.__name__)
            raise ProviderError("llm", "timeout")
        return await original(messages, schema, **kwargs)

    monkeypatch.setattr(model, "generate", generate)
    trip, job = submit(planned_client)
    completed = wait_job(planned_client, job["id"])
    assert completed["status"] == "succeeded"
    result = planned_client.get(f"/api/trips/{trip['id']}/itinerary").json()
    assert len(calls) == 1
    assert any(issue["rule"] == "quality_unavailable" for issue in result["validation"]["issues"])
    assert result["validation"]["status"] == "needs_review"
