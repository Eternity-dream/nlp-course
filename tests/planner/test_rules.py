from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError
from travel_planner.domain.models import TripFields
from travel_planner.planner.evidence import build_evidence, minute_present
from travel_planner.planner.models import (
    ExtractedFacts,
    FactClaim,
    FactEvidence,
    PlanDraft,
    TransportLeg,
)
from travel_planner.planner.validation import validate_plan
from travel_planner.providers.models import Place, Route, SearchSource

from .conftest import TRIP


@pytest.fixture
def rule_data():
    request = TripFields.model_validate(TRIP).model_dump(mode="json")
    places = {
        "p": Place(
            id="p",
            provider_id="p",
            name="博物馆",
            address="一号路1号",
            longitude=108.95,
            latitude=34.25,
            city="西安市",
            citycode="029",
            adcode="610103",
            queried_at=datetime.now(UTC),
        )
    }
    days = []
    for index in range(3):
        activities = [
            {
                "id": f"d{index}a{i}",
                "place_id": "p",
                "kind": "meal" if i == 1 else "sightseeing",
                "period": ["morning", "noon", "evening"][i],
                "start_minute": [540, 720, 1080][i],
                "end_minute": [660, 780, 1200][i],
                "costs": [{"category": "meal" if i == 1 else "ticket", "amount_cents": None}],
            }
            for i in range(3)
        ]
        days.append({"on_date": f"2026-12-0{index + 1}", "activities": activities})
    draft = PlanDraft.model_validate({"days": days})
    transport = []
    for day in draft.days:
        for first, second in zip(day.activities, day.activities[1:], strict=False):
            transport.append(
                TransportLeg(
                    id=first.id + "leg",
                    from_activity_id=first.id,
                    to_activity_id=second.id,
                    route=Route(
                        origin_place_id="p",
                        destination_place_id="p",
                        mode="walking",
                        distance_meters=1000,
                        duration_seconds=600,
                        status="estimated",
                        queried_at=datetime.now(UTC),
                    ),
                )
            )
    return request, draft, places, transport


def evidence(**changes):
    base = {
        "id": "f",
        "place_id": "p",
        "source_id": "s",
        "source_url": "https://example.com",
        "on_date": "2026-12-01",
        "kind": "opening_hours",
        "quote": "公开事实",
        "queried_at": datetime.now(UTC),
        "fetched_at": datetime.now(UTC),
        "status": "supported",
        "reason": "引用支持",
        "windows": [{"start_minute": 480, "end_minute": 1320}],
    }
    return FactEvidence.model_validate({**base, **changes})


def test_budget_per_person_group_fixed_unknown_and_no_budget(rule_data):
    request, draft, places, transport = rule_data
    first, second, third = draft.days[0].activities
    first.costs[0].amount_cents, first.costs[0].evidence_id = 10000, "ticket"
    second.costs[0].amount_cents, second.costs[0].basis, second.costs[0].evidence_id = (
        3000,
        "per_group",
        "meal",
    )
    third.costs[0].amount_cents, third.costs[0].basis = 500, "fixed"
    facts = [
        evidence(id="ticket", kind="ticket_price", amount_cents=10000),
        evidence(id="meal", kind="meal_price", amount_cents=3000, basis="per_group"),
    ]
    result = validate_plan(request, draft, places, facts, transport)
    assert result.known_cost_cents == 23000
    assert result.estimated_cost_cents == 500
    assert result.unknown_cost_count == 6
    assert "budget_incomplete" in {issue.rule for issue in result.issues}
    request["budget_cents"] = 22000
    assert "budget_exceeded" in {
        i.rule for i in validate_plan(request, draft, places, facts, transport).issues
    }
    request["budget_cents"] = 23200
    assert "budget_estimate_exceeded" in {
        i.rule for i in validate_plan(request, draft, places, facts, transport).issues
    }
    request["budget_cents"] = None
    assert all(
        not i.rule.startswith("budget")
        for i in validate_plan(request, draft, places, facts, transport).issues
    )


def test_transit_per_person_and_car_per_group_counted_once(rule_data):
    request, draft, places, transport = rule_data
    transport[0].route.mode = "transit"
    transport[0].route.fare_cents = 500
    transport[0].route.fare_basis = "per_person"
    transport[1].route.mode = "driving"
    transport[1].route.fare_cents = 3000
    transport[1].route.fare_basis = "per_group"
    result = validate_plan(request, draft, places, [], transport)
    assert result.estimated_cost_cents == 4000


@pytest.mark.parametrize(
    "fact,rule",
    [
        ({"closed": True, "windows": []}, "closed"),
        ({"windows": [{"start_minute": 600, "end_minute": 1000}]}, "opening_hours"),
        ({"last_entry_minute": 539}, "last_entry"),
    ],
)
def test_closing_hours_and_last_admission_conflicts(rule_data, fact, rule):
    request, draft, places, transport = rule_data
    result = validate_plan(request, draft, places, [evidence(**fact)], transport)
    assert result.status == "blocked"
    assert rule in {issue.rule for issue in result.issues}


def test_cross_midnight_opening_and_next_day_overlap(rule_data):
    request, draft, places, transport = rule_data
    activity = draft.days[0].activities[-1]
    activity.start_minute, activity.end_minute = 1380, 1530
    fact = evidence(windows=[{"start_minute": 1320, "end_minute": 1560}])
    result = validate_plan(request, draft, places, [fact], transport)
    assert not any(
        issue.rule == "opening_hours"
        and activity.id in issue.object_ids
        and issue.severity == "conflict"
        for issue in result.issues
    )
    draft.days[1].activities[0].start_minute = 30
    assert "overnight_overlap" in {
        i.rule for i in validate_plan(request, draft, places, [fact], transport).issues
    }


@pytest.mark.parametrize(
    "kind,minute,place_name,rule",
    [
        ("arrival", 600, None, "arrival"),
        ("departure", 1000, None, "departure"),
        ("reservation", 530, "博物馆", "reservation"),
    ],
)
def test_structured_arrival_departure_reservation_are_checked(
    rule_data, kind, minute, place_name, rule
):
    request, draft, places, transport = rule_data
    if kind == "reservation":
        # 预约地点在当天唯一对应。
        places["meal"] = places["p"].model_copy(update={"id": "meal", "name": "餐馆"})
        places["park"] = places["p"].model_copy(update={"id": "park", "name": "公园"})
        draft.days[0].activities[1].place_id = "meal"
        draft.days[0].activities[2].place_id = "park"
    request["time_constraints"] = [
        {
            "kind": kind,
            "on_date": "2026-12-01",
            "minute": minute,
            "place_name": place_name,
            "end_minute": None,
        }
    ]
    result = validate_plan(request, draft, places, [], transport)
    assert any(issue.rule == rule and issue.severity == "conflict" for issue in result.issues)


def test_unknown_hours_route_and_source_conflicts_are_not_passed(rule_data):
    request, draft, places, transport = rule_data
    transport[0].route.duration_seconds = None
    result = validate_plan(request, draft, places, [evidence(status="conflict")], transport)
    assert result.status == "needs_review"
    assert {"evidence_conflict", "route_unknown", "budget_incomplete"} <= {
        i.rule for i in result.issues
    }


def test_travel_time_and_overlapping_activities_are_conflicts(rule_data):
    request, draft, places, transport = rule_data
    transport[0].route.duration_seconds = 10000
    result = validate_plan(request, draft, places, [], transport)
    assert any(
        issue.rule == "travel_time" and issue.severity == "conflict" for issue in result.issues
    )
    draft.days[0].activities[1].start_minute = 650
    assert "activity_overlap" in {
        i.rule for i in validate_plan(request, draft, places, [], transport).issues
    }


@pytest.mark.parametrize(
    "change",
    [
        {"place_id": "invented"},
        {"id": "duplicate"},
    ],
)
def test_invalid_place_and_duplicate_activity_ids_rejected(rule_data, change):
    request, draft, places, _ = rule_data
    raw = draft.model_dump(mode="json")
    raw["days"][0]["activities"][0].update(change)
    if "id" in change:
        raw["days"][1]["activities"][0]["id"] = "duplicate"
    with pytest.raises(ValidationError):
        PlanDraft.model_validate(
            raw,
            context={
                "dates": [date(2026, 12, i) for i in (1, 2, 3)],
                "place_ids": set(places),
                "facts": {},
                "request": request,
            },
        )


def test_extracted_facts_require_real_source_and_date(rule_data):
    request, draft, places, _ = rule_data
    claim = {
        "place_id": "p",
        "source_id": "invented",
        "on_date": "2026-12-01",
        "kind": "opening_hours",
        "quote": "t",
    }
    context = {"place_ids": set(places), "source_ids": {"s"}, "dates": {date(2026, 12, 1)}}
    with pytest.raises(ValidationError):
        ExtractedFacts.model_validate({"claims": [claim]}, context=context)
    claim.update({"source_id": "s", "on_date": "2026-12-02"})
    with pytest.raises(ValidationError):
        ExtractedFacts.model_validate({"claims": [claim]}, context=context)


@pytest.mark.parametrize(
    "change,status",
    [
        ({}, "supported"),
        ({"quote": "编造引用"}, "unknown"),
        ({"windows": [{"start_minute": 540, "end_minute": 1000}]}, "unknown"),
        ({"on_date": "2026-12-02"}, "estimated"),
        ({"closed": True, "windows": []}, "unknown"),
    ],
)
def test_evidence_needs_exact_quote_date_location_and_values(rule_data, change, status):
    _, _, places, _ = rule_data
    source = SearchSource(
        id="s",
        provider="exa",
        url="https://example.com",
        title="t",
        text="博物馆 一号路1号 2026-12-01 开放08:00-22:00，22:00闭馆。",
        queried_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )
    claim = FactClaim.model_validate(
        {
            "place_id": "p",
            "source_id": "s",
            "on_date": "2026-12-01",
            "kind": "opening_hours",
            "quote": source.text,
            "windows": [{"start_minute": 480, "end_minute": 1320}],
            **change,
        }
    )
    result = build_evidence(ExtractedFacts(claims=[claim]), {"s": source}, places)
    assert result[0].status == status


def test_conflicting_sources_retained_with_ids_and_unknown_status(rule_data):
    _, _, places, _ = rule_data
    sources, claims = {}, []
    for index, end in enumerate([17, 18]):
        source = SearchSource(
            id=f"s{index}",
            provider="exa",
            url=f"https://example.com/{index}",
            title="t",
            text=f"博物馆 一号路1号 2026-12-01 开放08:00-{end}:00。",
            queried_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )
        sources[source.id] = source
        claims.append(
            FactClaim(
                place_id="p",
                source_id=source.id,
                on_date=date(2026, 12, 1),
                kind="opening_hours",
                quote=source.text,
                windows=[{"start_minute": 480, "end_minute": end * 60}],
            )
        )
    result = build_evidence(ExtractedFacts(claims=claims), sources, places)
    assert len(result) == 2 and all(fact.status == "conflict" for fact in result)
    assert len({fact.source_url for fact in result}) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "arrival", "on_date": "2026-12-10", "minute": 100},
        {"kind": "reservation", "on_date": "2026-12-01", "minute": 100},
        {
            "kind": "reservation",
            "on_date": "2026-12-01",
            "minute": 100,
            "end_minute": 90,
            "place_name": "馆",
        },
        {"kind": "arrival", "on_date": "20261201", "minute": 100},
    ],
)
def test_invalid_structured_time_constraints_rejected(changes):
    with pytest.raises(ValidationError):
        TripFields.model_validate({**TRIP, "time_constraints": [changes]})


def test_numeric_substrings_do_not_support_wrong_fact_values(rule_data):
    _, _, places, _ = rule_data
    assert not minute_present(540, "19:00")
    source = SearchSource(
        id="s",
        provider="exa",
        url="https://example.com",
        title="t",
        text="博物馆 一号路1号 2026-12-01 每人100元。",
        queried_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )
    claim = FactClaim(
        place_id="p",
        source_id="s",
        on_date=date(2026, 12, 1),
        kind="ticket_price",
        quote=source.text,
        amount_cents=0,
    )
    assert (
        build_evidence(ExtractedFacts(claims=[claim]), {"s": source}, places)[0].status == "unknown"
    )
    claim.amount_cents = 1000
    assert (
        build_evidence(ExtractedFacts(claims=[claim]), {"s": source}, places)[0].status == "unknown"
    )


def test_partial_arrival_and_departure_days_do_not_require_full_day(rule_data):
    from travel_planner.planner.validation import validate_plan

    request, draft, places, facts_and_routes = rule_data
    request["time_constraints"] = [
        {"kind": "arrival", "on_date": "2026-12-01", "minute": 840},
        {"kind": "departure", "on_date": "2026-12-03", "minute": 900},
    ]
    draft.days[0].activities = draft.days[0].activities[1:]
    draft.days[0].activities[0].start_minute = 840
    draft.days[0].activities[0].end_minute = 900
    draft.days[-1].activities = draft.days[-1].activities[:2]
    result = validate_plan(request, draft, places, [], facts_and_routes)
    assert not any(item.rule == "day_coverage" for item in result.issues)


def test_flexible_meal_does_not_use_anchor_opening_hours(rule_data):
    request, draft, places, transport = rule_data
    meal = draft.days[0].activities[1]
    meal.flexible_meal = True
    closed = evidence(closed=True, windows=[])
    result = validate_plan(request, draft, places, [closed], transport)
    assert not any(
        meal.id in issue.object_ids and issue.rule in {"closed", "opening_hours"}
        for issue in result.issues
    )
    assert any(issue.rule == "closed" for issue in result.issues)
