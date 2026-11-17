from datetime import UTC, date, datetime

import pytest
from travel_planner.planner.evidence import build_evidence, reconcile_conflicts
from travel_planner.planner.models import ExtractedFacts, FactChecks, FactClaim
from travel_planner.providers.models import Place, SearchSource


def sample(**changes):
    text = (
        "北京示例博物馆，地址：东路1号。\n"
        "每周二至周日开放09:00-17:00，16:00停止入馆，周一闭馆。\n"
        "普通成人门票40元/人。参观需提前预约。"
    )
    place = Place(
        id="p",
        provider_id="p",
        name="北京示例博物馆",
        address="东城区东路1号",
        city="北京市",
        citycode="010",
        adcode="110101",
        longitude=116.4,
        latitude=39.9,
        queried_at=datetime.now(UTC),
    )
    source = SearchSource(
        id="s",
        provider="exa",
        title="参观指南",
        url="https://museum.example.org/visit",
        text=text,
        queried_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )
    claim = FactClaim.model_validate(
        {
            "place_id": "p",
            "source_id": "s",
            "on_date": "2026-12-11",
            "kind": "opening_hours",
            "quote": "每周二至周日开放09:00-17:00，16:00停止入馆，周一闭馆。",
            "identity_quote": "北京示例博物馆，地址：东路1号。",
            "windows": [{"start_minute": 540, "end_minute": 1020}],
            "last_entry_minute": 960,
            "applicability": {
                "mode": "recurring",
                "quote": "每周二至周日开放09:00-17:00，16:00停止入馆，周一闭馆。",
                "weekdays": [2, 3, 4, 5, 6, 7],
            },
            **changes,
        }
    )
    return place, source, claim


def evaluate(place, source, claim, **review):
    checks = FactChecks(
        checks=[
            {
                "claim_index": 0,
                "identity": "matched",
                "interpretation": "supported",
                "authority": "official",
                "reason": "地点与原文规则对应",
                **review,
            }
        ]
    )
    return build_evidence(ExtractedFacts(claims=[claim]), {"s": source}, {"p": place}, checks)[0]


def test_independent_identity_and_weekly_rules_support_without_exact_address_or_date():
    place, source, claim = sample()
    fact = evaluate(place, source, claim)
    assert fact.status == "supported"
    assert fact.verification["identity"] == "matched"
    assert fact.verification["date"] == "applicable"


@pytest.mark.parametrize(
    "changes,review,expected",
    [
        ({"on_date": "2026-12-14"}, {}, "unknown"),
        ({"quote": "伪造09:00-17:00"}, {}, "unknown"),
        ({"identity_quote": "别处的博物馆"}, {}, "unknown"),
        ({"windows": [{"start_minute": 600, "end_minute": 1020}]}, {}, "unknown"),
        ({}, {"identity": "mismatch"}, "unknown"),
        ({}, {"interpretation": "unsupported"}, "unknown"),
        ({}, {"authority": "secondary"}, "estimated"),
    ],
)
def test_missing_or_wrong_evidence_never_becomes_supported(changes, review, expected):
    assert evaluate(*sample(**changes), **review).status == expected


def test_season_ranges_cross_year_and_expired_notices():
    place, source, claim = sample()
    source.text += "\n每年11月1日至次年3月31日开放09:00-17:00。"
    rule = claim.applicability
    rule.quote = "每年11月1日至次年3月31日开放09:00-17:00。"
    rule.start_month_day, rule.end_month_day = "11-01", "03-31"
    assert evaluate(place, source, claim).status == "supported"
    claim.on_date = date(2027, 6, 11)
    assert evaluate(place, source, claim).status == "unknown"
    claim.on_date = date(2026, 12, 11)
    rule.end_date = date(2025, 12, 31)
    assert evaluate(place, source, claim).status == "unknown"


def test_holiday_exception_needs_target_date_notice():
    place, source, claim = sample()
    source.text += "\n节假日除外。"
    claim.applicability.quote = "节假日除外。"
    assert evaluate(place, source, claim).status == "estimated"


def test_no_review_keeps_unknown_and_reservation_is_structured():
    place, source, claim = sample(
        kind="reservation",
        quote="参观需提前预约。",
        reservation_required=True,
        reservation_note="需提前预约",
    )
    assert evaluate(place, source, claim).status == "supported"
    facts = build_evidence(
        ExtractedFacts(claims=[claim]), {"s": source}, {"p": place}, FactChecks()
    )
    assert facts[0].status == "unknown"


def test_ticket_scope_separates_ticket_types_but_same_scope_conflicts():
    place, source, claim = sample(
        kind="ticket_price",
        quote="普通成人门票40元/人。",
        windows=[],
        last_entry_minute=None,
        amount_cents=4000,
        price_scope="成人普通门票",
    )
    first = evaluate(place, source, claim)
    second = first.model_copy(update={"id": "f2", "price_scope": "成人联票", "amount_cents": 8000})
    reconcile_conflicts([first, second])
    assert first.status == second.status == "supported"
    second.price_scope = first.price_scope
    reconcile_conflicts([first, second])
    assert first.status == second.status == "conflict"


def test_weekly_closed_rule_is_confirmed_only_on_matching_day():
    place, source, claim = sample(
        on_date="2026-12-14",
        closed=True,
        windows=[],
        last_entry_minute=None,
        quote="周一闭馆。",
        applicability={"mode": "recurring", "weekdays": [1], "quote": "周一闭馆。"},
    )
    assert evaluate(place, source, claim).status == "supported"
    claim.on_date = date(2026, 12, 15)
    assert evaluate(place, source, claim).status == "unknown"


def test_missing_applicability_quote_cannot_verify_dates():
    place, source, claim = sample()
    claim.applicability.quote = "每天全年开放"
    assert evaluate(place, source, claim).status == "estimated"


def test_incorrect_price_scope_or_invented_reservation_is_rejected():
    place, source, claim = sample(
        kind="ticket_price",
        quote="普通成人门票40元/人。",
        amount_cents=400,
        windows=[],
        last_entry_minute=None,
    )
    assert evaluate(place, source, claim).status == "unknown"
    place, source, claim = sample(
        kind="reservation",
        quote="普通成人门票40元/人。",
        reservation_required=True,
        reservation_note="已预约",
    )
    assert evaluate(place, source, claim).status == "unknown"


def test_labelled_opening_and_closing_times_require_semantic_review():
    place, source, claim = sample()
    source.text += "\n开放入馆时间：09:00 停止入馆时间：16:00 闭馆时间：17:00"
    claim.quote = "开放入馆时间：09:00 停止入馆时间：16:00 闭馆时间：17:00"
    assert evaluate(place, source, claim).status == "supported"
    claim.windows[0].end_minute = 960
    assert evaluate(place, source, claim).status == "unknown"


def test_time_before_label_and_last_entry_park_format():
    place, source, claim = sample()
    source.text += "\n6:30开园，21:00停止入园，22:00闭园"
    claim.quote = "6:30开园，21:00停止入园，22:00闭园"
    claim.windows[0].start_minute = 390
    claim.windows[0].end_minute = 1320
    claim.last_entry_minute = 1260
    assert evaluate(place, source, claim).status == "supported"


def test_resume_rechecks_saved_values_without_changing_evidence_id():
    from travel_planner.planner.evidence import recheck_saved_evidence

    place, source, claim = sample()
    fact = evaluate(place, source, claim)
    fact.status = "unknown"
    refreshed = recheck_saved_evidence([fact], {"s": source}, {"p": place})
    assert refreshed[0].id == fact.id
    assert refreshed[0].status == "supported"
