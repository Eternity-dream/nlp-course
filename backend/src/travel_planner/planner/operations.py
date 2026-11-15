from pydantic import ValidationError

from travel_planner.domain.errors import AppError
from travel_planner.planner.models import (
    Activity,
    AddActivity,
    ConstraintConflict,
    Itinerary,
    ItineraryEdit,
    MoveActivity,
    PlanDraft,
    RemoveActivity,
    ReplaceActivity,
)
from travel_planner.storage.repository import new_id


def edit_error(message: str, object_id: str | None = None):
    return AppError("invalid_edit", message, 422, object_id=object_id)


def apply_operations(base: Itinerary, body: ItineraryEdit, request: dict):
    """按操作顺序修改副本；引用活动与插入锚点始终使用稳定 ID。"""
    days = [day.model_copy(deep=True) for day in base.days]
    by_date = {day.on_date: day for day in days}
    places = {place.id for place in base.places}
    queries, affected = {}, set()

    def day_on(on_date):
        if on_date not in by_date:
            raise edit_error("目标日期需要在当前行程内")
        return by_date[on_date]

    def locate(activity_id):
        for day in days:
            for index, activity in enumerate(day.activities):
                if activity.id == activity_id:
                    return day, index, activity
        raise edit_error("活动不存在或已被前序操作移除", activity_id)

    def insert(day, activity, before_id):
        if before_id is None:
            day.activities.append(activity)
            return
        if before_id == activity.id:
            raise edit_error("活动不能以自身作为插入锚点", before_id)
        anchor_day, index, _ = locate(before_id)
        if anchor_day.on_date != day.on_date:
            raise edit_error("插入锚点必须属于目标日期", before_id)
        day.activities.insert(index, activity)

    def make_activity(spec, activity_id):
        if spec.place_query:
            if spec.place_id in queries and queries[spec.place_id] != spec.place_query:
                raise edit_error("同一地点 ID 使用了不同检索词", spec.place_id)
            queries[spec.place_id] = spec.place_query
        elif spec.place_id not in places and spec.place_id not in queries:
            raise edit_error("新增地点需要 place_query 和地图返回的 place_id", spec.place_id)
        return Activity.model_validate(
            {**spec.model_dump(exclude={"place_query"}), "id": activity_id}
        )

    for operation in body.operations:
        if isinstance(operation, AddActivity):
            day = day_on(operation.on_date)
            insert(day, make_activity(operation.activity, new_id()), operation.before_activity_id)
            affected.add(day.on_date)
        elif isinstance(operation, RemoveActivity):
            day, index, _ = locate(operation.activity_id)
            day.activities.pop(index)
            affected.add(day.on_date)
        elif isinstance(operation, ReplaceActivity):
            day, index, _ = locate(operation.activity_id)
            day.activities[index] = make_activity(operation.activity, operation.activity_id)
            affected.add(day.on_date)
        elif isinstance(operation, MoveActivity):
            day, index, activity = locate(operation.activity_id)
            target = day_on(operation.target_date)
            if operation.before_activity_id == activity.id:
                raise edit_error("活动不能以自身作为插入锚点", activity.id)
            if day.on_date != target.on_date:
                # 原日期的费用依据不能直接作为目标日期的依据。
                for cost in activity.costs:
                    cost.evidence_id = None
            day.activities.pop(index)
            insert(target, activity, operation.before_activity_id)
            affected.update({day.on_date, target.on_date})
    facts = {fact.id: fact for fact in base.evidence}
    conflicts = list(base.constraint_conflicts)
    if not conflicts:
        # 版本 3 早期草案仅保存文字冲突问题，按原要求恢复引用以保留已知冲突。
        for field in ("requirements", "known_conditions"):
            for index, text in enumerate(request[field]):
                for issue in base.validation.issues:
                    if (
                        issue.rule == "explicit_constraint"
                        and issue.severity == "conflict"
                        and issue.message.startswith(text + "：")
                    ):
                        conflicts.append(
                            ConstraintConflict(
                                field=field, index=index, reason=issue.message[len(text) + 1 :]
                            )
                        )
    context = {
        "request": request,
        "dates": [day.on_date for day in base.days],
        "place_ids": places | set(queries),
        "facts": facts,
        "allow_empty_days": True,
    }
    try:
        draft = PlanDraft.model_validate(
            {
                "days": [day.model_dump() for day in days],
                "assumptions": [],
                "constraint_conflicts": conflicts,
            },
            context=context,
        )
    except ValidationError as exc:
        raise edit_error("编辑后的活动、费用依据或每天活动数量无效") from exc
    return draft, affected, queries


def check_saved_scope(base: Itinerary, body: ItineraryEdit, request: dict, payload: dict):
    expected, affected, _ = apply_operations(base, body, request)
    candidate_days = payload["days"]
    if [day["on_date"] for day in candidate_days] != [day.on_date.isoformat() for day in base.days]:
        raise AppError("edit_scope_violation", "编辑结果改变了行程日期，原草案已保留", 409)
    for old, candidate in zip(base.days, candidate_days, strict=True):
        if old.on_date not in affected and old.model_dump(mode="json") != candidate:
            raise AppError("edit_scope_violation", "编辑结果改变了其他日期，原草案已保留", 409)
    original_ids = {a.id for day in base.days for a in day.activities}
    expected_ids = {a.id for day in expected.days for a in day.activities} & original_ids
    result_ids = [a["id"] for day in candidate_days for a in day["activities"]]
    if (
        set(result_ids) & original_ids != expected_ids
        or len(result_ids) != len(set(result_ids))
        or len(result_ids) != sum(len(day.activities) for day in expected.days)
    ):
        raise AppError("edit_scope_violation", "编辑结果未保留指定活动身份，原草案已保留", 409)
