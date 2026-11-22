import re
from datetime import date, timedelta

from travel_planner.planner.models import (
    PlanDraft,
    TransportLeg,
    ValidationIssue,
    ValidationResult,
)
from travel_planner.providers.models import Place

ARRIVAL_BUFFER_MINUTES = 60
DEPARTURE_BUFFER_MINUTES = 90
HUB_TYPES = ("火车站", "高铁站", "铁路客运站", "机场", "航站楼")


def _is_transport_hub(place: Place) -> bool:
    category = place.category or ""
    return (
        (place.category_code or "").startswith(("1501", "1502"))
        or any(part.strip().endswith(HUB_TYPES) for part in re.split(r"[;|/]", category))
        or place.name.endswith(HUB_TYPES)
        or ("交通设施服务" in category and place.name.endswith(("站", "交通枢纽")))
    )


def validate_plan(
    request: dict, draft: PlanDraft, places: dict, facts: list, transport: list[TransportLeg]
) -> ValidationResult:
    issues = []
    known, estimated, unknown = 0, 0, 0
    by_id = {fact.id: fact for fact in facts}
    legs = {(leg.from_activity_id, leg.to_activity_id): leg for leg in transport}

    def issue(rule, severity, message, ids=None, evidence=None):
        issues.append(
            ValidationIssue(
                rule=rule,
                severity=severity,
                message=message,
                object_ids=ids or [],
                evidence_ids=evidence or [],
            )
        )

    for day in draft.days:
        activities = day.activities
        available_start, available_end = 0, 1440
        for constraint in request["time_constraints"]:
            relative = (
                date.fromisoformat(constraint["on_date"]) - day.on_date
            ).days * 1440 + constraint["minute"]
            if constraint["kind"] == "arrival":
                available_start = max(available_start, relative + ARRIVAL_BUFFER_MINUTES)
            elif constraint["kind"] == "departure":
                available_end = min(available_end, relative - DEPARTURE_BUFFER_MINUTES)
        expected_periods = {
            period
            for period, start, end in (
                ("morning", 480, 720),
                ("noon", 720, 1020),
                ("evening", 1020, 1320),
            )
            if min(end, available_end) - max(start, available_start) > 60
        }
        if not expected_periods <= {activity.period for activity in activities}:
            issue("day_coverage", "unknown", f"{day.on_date} 未覆盖完整早中晚安排")
        if not any(activity.kind == "meal" for activity in activities):
            issue("meal_coverage", "unknown", f"{day.on_date} 缺少餐饮安排")
        for activity in activities:
            place = places[activity.place_id]
            period_matches = {
                "morning": activity.start_minute < 720,
                "noon": 660 <= activity.start_minute < 1080,
                "evening": activity.start_minute >= 1020,
            }
            if not period_matches[activity.period]:
                issue("activity_period", "unknown", "早中晚分组与计划开始时间不一致", [activity.id])
            if (
                place.longitude is None
                or place.latitude is None
                or not place.address
                or not place.citycode
            ):
                issue(
                    "place_location", "unknown", "地点地址、坐标或城市尚未完整确认", [activity.id]
                )
            hours = [
                fact
                for fact in facts
                if fact.place_id == place.id
                and fact.on_date == day.on_date
                and fact.kind == "opening_hours"
            ]
            flexible_meal = activity.kind == "meal" and activity.flexible_meal
            if flexible_meal:
                hours = []
            supported = [fact for fact in hours if fact.status == "supported"]
            conflicts = [fact for fact in hours if fact.status == "conflict"]
            if conflicts:
                issue(
                    "evidence_conflict",
                    "unknown",
                    "营业安排来源冲突，请确认适用规则",
                    [activity.id],
                    [fact.id for fact in conflicts],
                )
            elif supported:
                fact = supported[0]
                if fact.closed:
                    issue("closed", "conflict", "该地点在行程当日闭馆", [activity.id], [fact.id])
                elif not any(
                    activity.start_minute >= w.start_minute and activity.end_minute <= w.end_minute
                    for w in fact.windows
                ):
                    issue(
                        "opening_hours",
                        "conflict",
                        "活动未完整落在营业区间内",
                        [activity.id],
                        [fact.id],
                    )
                if (
                    fact.last_entry_minute is not None
                    and activity.start_minute > fact.last_entry_minute
                ):
                    issue(
                        "last_entry",
                        "conflict",
                        "活动开始晚于停止检票／入场时间",
                        [activity.id],
                        [fact.id],
                    )
            elif not flexible_meal and not (
                activity.kind == "leisure" and _is_transport_hub(place)
            ):
                issue(
                    "opening_hours",
                    "unknown",
                    "当日营业安排待核对，未知营业时间未判为通过",
                    [activity.id],
                    [fact.id for fact in hours],
                )
            reservations = [
                fact
                for fact in facts
                if fact.place_id == place.id
                and fact.on_date == day.on_date
                and fact.kind == "reservation"
                and fact.reservation_required
                and fact.status in {"supported", "estimated"}
            ]
            if reservations and not flexible_meal:
                confirmed = any(
                    item["kind"] == "reservation"
                    and item["on_date"] == day.on_date.isoformat()
                    and item.get("place_name") == place.name
                    for item in request["time_constraints"]
                )
                if not confirmed:
                    issue(
                        "reservation_required",
                        "unknown",
                        "需提前预约",
                        [activity.id],
                        [fact.id for fact in reservations],
                    )
            needed = "meal" if activity.kind == "meal" else "ticket"
            if not any(cost.category == needed for cost in activity.costs):
                unknown += 1
                issue("cost_missing", "unknown", "餐费或门票费用缺失", [activity.id])
            for cost in activity.costs:
                fact = by_id.get(cost.evidence_id)
                if fact and fact.status == "conflict":
                    issue(
                        "cost_evidence_conflict",
                        "unknown",
                        "费用来源存在冲突，请核对适用价格",
                        [activity.id],
                        [
                            item.id
                            for item in facts
                            if item.place_id == fact.place_id
                            and item.on_date == fact.on_date
                            and item.kind == fact.kind
                            and item.status == "conflict"
                        ],
                    )
                if cost.amount_cents is None:
                    unknown += 1
                    issue("cost_unknown", "unknown", "费用未知，保留已知小计", [activity.id])
                    continue
                amount = cost.amount_cents * (
                    request["travelers"] if cost.basis == "per_person" else 1
                )
                if fact and fact.status == "supported":
                    known += amount
                else:
                    estimated += amount
                    issue(
                        "cost_estimated",
                        "unknown",
                        "费用为估算或来源尚待核对",
                        [activity.id],
                        [cost.evidence_id] if cost.evidence_id else [],
                    )
            for constraint in request["time_constraints"]:
                minute, kind = constraint["minute"], constraint["kind"]
                absolute = date.fromisoformat(constraint["on_date"]).toordinal() * 1440 + minute
                if (
                    kind == "arrival"
                    and day.on_date.toordinal() * 1440 + activity.start_minute < absolute
                ):
                    issue("arrival", "conflict", "活动早于明确抵达时间", [activity.id])
                if (
                    kind == "departure"
                    and day.on_date.toordinal() * 1440 + activity.end_minute > absolute
                ):
                    issue("departure", "conflict", "活动晚于明确离开时间", [activity.id])
        for first, second in zip(activities, activities[1:], strict=False):
            leg = legs.get((first.id, second.id))
            if second.start_minute < first.end_minute:
                issue(
                    "activity_overlap",
                    "conflict",
                    "相邻活动重叠或顺序不符合时间",
                    [first.id, second.id],
                )
            if not leg or leg.route.duration_seconds is None:
                issue("route_unknown", "unknown", "相邻活动交通时间尚未确认", [first.id, second.id])
            else:
                if first.end_minute * 60 + leg.route.duration_seconds > second.start_minute * 60:
                    issue(
                        "travel_time",
                        "conflict",
                        "前项结束加交通时间晚于后项开始",
                        [first.id, second.id],
                    )
            mode = leg.route.mode if leg else second.transport_mode
            if mode != "walking":
                if not leg or leg.route.fare_cents is None:
                    unknown += 1
                    issue(
                        "transport_cost",
                        "unknown",
                        "当地交通费用待确认",
                        [leg.id] if leg else [first.id, second.id],
                    )
                else:
                    estimated += leg.route.fare_cents * (
                        request["travelers"] if leg.route.fare_basis == "per_person" else 1
                    )
                    issue(
                        "transport_cost_estimated", "unknown", "当地交通费用为查询时估算", [leg.id]
                    )
        for constraint in request["time_constraints"]:
            if (
                constraint["kind"] != "reservation"
                or constraint["on_date"] != day.on_date.isoformat()
            ):
                continue
            matched = [
                activity
                for activity in activities
                if places[activity.place_id].name == constraint["place_name"]
            ]
            if len(matched) != 1:
                issue(
                    "reservation",
                    "conflict" if not matched else "unknown",
                    "当天未安排已知预约地点"
                    if not matched
                    else "预约地点无法唯一对应，请确认具体地点",
                )
            else:
                activity = matched[0]
                end = constraint["end_minute"] or constraint["minute"] + 1
                if activity.start_minute > constraint["minute"] or activity.end_minute < end:
                    issue("reservation", "conflict", "活动时间未覆盖已知预约", [activity.id])
    for first, second in zip(draft.days, draft.days[1:], strict=False):
        if not first.activities or not second.activities:
            continue
        end = first.activities[-1]
        start = second.activities[0]
        if (
            first.on_date + timedelta(days=1) == second.on_date
            and end.end_minute > 1440 + start.start_minute
        ):
            issue("overnight_overlap", "conflict", "跨午夜活动与次日活动重叠", [end.id, start.id])
    for field in ("requirements", "known_conditions"):
        for index, condition in enumerate(request[field]):
            detected = [
                item
                for item in draft.constraint_conflicts
                if item.field == field and item.index == index
            ]
            if detected:
                issue("explicit_constraint", "conflict", f"{condition}：{detected[0].reason}")
            else:
                issue("explicit_constraint", "unknown", f"请核对文字条件：{condition}")
    budget = request["budget_cents"]
    if budget is not None:
        if known > budget:
            issue("budget_exceeded", "conflict", f"全团全程已知费用 {known} 分超过预算 {budget} 分")
        elif known + estimated > budget:
            issue(
                "budget_estimate_exceeded",
                "conflict",
                f"已知及估算费用 {known + estimated} 分超过预算 {budget} 分",
            )
        elif unknown:
            issue("budget_incomplete", "unknown", "部分费用未知，全程预算尚不能判为满足")
    status = (
        "blocked"
        if any(item.severity == "conflict" for item in issues)
        else "needs_review"
        if issues
        else "passed"
    )
    return ValidationResult(
        status=status,
        issues=issues,
        known_cost_cents=known,
        estimated_cost_cents=estimated,
        unknown_cost_count=unknown,
    )
