from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, ValidationInfo, model_validator

from travel_planner.domain.models import Model, PositiveInt, ShortText, TextList, iso_date
from travel_planner.providers.models import Place, Route, SearchSource

Minute = Annotated[int, Field(strict=True, ge=0, le=2880)]
Cents = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]


class JobSubmit(Model):
    expected_revision: PositiveInt
    idempotency_key: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")]


class JobResume(Model):
    expected_revision: PositiveInt


class JobEvent(Model):
    stage: str
    status: str
    recorded_at: datetime


class PlanJob(Model):
    id: str
    trip_id: str
    request_revision: int
    kind: Literal["plan", "edit"] = "plan"
    base_revision: int | None = None
    status: Literal["queued", "running", "awaiting_input", "succeeded", "failed", "interrupted"]
    stage: str
    retry_of: str | None
    result_revision: int | None
    questions: list[str]
    error: dict | None
    created_at: datetime
    updated_at: datetime
    events: list[JobEvent]
    progress_estimate: int = 0


class OpeningWindow(Model):
    start_minute: Minute
    end_minute: Minute

    @model_validator(mode="after")
    def ordered(self):
        if self.start_minute >= 1440 or self.end_minute <= self.start_minute:
            raise ValueError("营业区间结束需要晚于开始，跨午夜以次日分钟表示")
        return self


class FactClaim(Model):
    place_id: ShortText
    on_date: date
    kind: Literal["opening_hours", "ticket_price", "meal_price", "reservation"]
    source_id: ShortText
    quote: Annotated[str, Field(min_length=1, max_length=2000)]
    windows: list[OpeningWindow] = Field(default_factory=list, max_length=8)
    closed: bool = False
    last_entry_minute: Minute | None = None
    amount_cents: Cents | None = None
    basis: Literal["per_person", "per_group", "fixed"] = "per_person"
    identity_quote: str = Field(default="", max_length=2000)
    applicability: "FactApplicability | None" = None
    price_scope: str = Field(default="", max_length=300)
    reservation_required: bool | None = None
    reservation_note: str = Field(default="", max_length=500)


class FactApplicability(Model):
    quote: Annotated[str, Field(min_length=1, max_length=2000)]
    mode: Literal["exact", "range", "recurring", "unspecified"]
    start_date: date | None = None
    end_date: date | None = None
    # 月日用于年度季节规则，支持跨年；星期采用 ISO 1（周一）至 7（周日）。
    start_month_day: Annotated[str, Field(pattern=r"^\d{2}-\d{2}$")] | None = None
    end_month_day: Annotated[str, Field(pattern=r"^\d{2}-\d{2}$")] | None = None
    weekdays: list[Annotated[int, Field(ge=1, le=7)]] = Field(default_factory=list, max_length=7)
    excluded_dates: list[date] = Field(default_factory=list, max_length=30)
    has_exceptions: bool = False


class FactCheck(Model):
    claim_index: Annotated[int, Field(ge=0)]
    identity: Literal["matched", "mismatch", "uncertain"]
    interpretation: Literal["supported", "unsupported", "uncertain"]
    authority: Literal["official", "secondary", "uncertain"]
    reason: ShortText


class FactChecks(Model):
    checks: list[FactCheck] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def indices(self, info: ValidationInfo):
        count = (info.context or {}).get("claim_count")
        indices = [check.claim_index for check in self.checks]
        if len(indices) != len(set(indices)) or (
            count is not None and set(indices) != set(range(count))
        ):
            raise ValueError("每条提取事实需要独立审核且索引不能重复")
        return self


class ExtractedFacts(Model):
    claims: list[FactClaim] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def references(self, info: ValidationInfo):
        context = info.context or {}
        if context:
            for claim in self.claims:
                if (
                    claim.place_id not in context["place_ids"]
                    or claim.source_id not in context["source_ids"]
                ):
                    raise ValueError("事实引用的地点或来源不存在")
                if claim.on_date not in context["dates"]:
                    raise ValueError("事实适用日期超出本次行程")
        return self


class FactEvidence(FactClaim):
    id: str
    source_url: str
    queried_at: datetime
    fetched_at: datetime | None
    status: Literal["supported", "estimated", "conflict", "unknown"]
    reason: str
    verification: dict = Field(default_factory=dict)


class ActivityCost(Model):
    category: Literal["ticket", "meal"]
    amount_cents: Cents | None = None
    basis: Literal["per_person", "per_group", "fixed"] = "per_person"
    evidence_id: str | None = None


class ActivityInput(Model):
    place_id: ShortText
    kind: Literal["sightseeing", "meal", "leisure"]
    period: Literal["morning", "noon", "evening"]
    start_minute: Annotated[int, Field(strict=True, ge=0, lt=1440)]
    end_minute: Minute
    transport_mode: Literal["walking", "transit", "driving"] = "walking"
    costs: list[ActivityCost] = Field(default_factory=list, max_length=6)
    description: Annotated[str, Field(max_length=200)] = ""
    flexible_meal: bool = False

    @model_validator(mode="after")
    def duration(self):
        if self.end_minute <= self.start_minute:
            raise ValueError("活动结束需要晚于开始")
        if self.flexible_meal and self.kind != "meal":
            raise ValueError("自由用餐标记仅用于餐饮活动")
        if self.flexible_meal and any(cost.category != "meal" for cost in self.costs):
            raise ValueError("自由用餐不计入景点门票")
        return self


class Activity(ActivityInput):
    id: ShortText


class EditActivity(ActivityInput):
    place_query: Annotated[str, Field(min_length=1, max_length=80)] | None = None


EditDate = Annotated[date, BeforeValidator(iso_date)]


class AddActivity(Model):
    op: Literal["add"]
    on_date: EditDate
    before_activity_id: ShortText | None = None
    activity: EditActivity


class RemoveActivity(Model):
    op: Literal["remove"]
    activity_id: ShortText


class ReplaceActivity(Model):
    op: Literal["replace"]
    activity_id: ShortText
    activity: EditActivity


class MoveActivity(Model):
    op: Literal["move"]
    activity_id: ShortText
    target_date: EditDate
    before_activity_id: ShortText | None = None


EditOperation = Annotated[
    AddActivity | RemoveActivity | ReplaceActivity | MoveActivity, Field(discriminator="op")
]


class ItineraryEdit(Model):
    expected_revision: PositiveInt
    expected_request_revision: PositiveInt
    idempotency_key: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")]
    operations: list[EditOperation] = Field(min_length=1, max_length=50)


class DayPlan(Model):
    on_date: date
    activities: list[Activity] = Field(max_length=15)


class ConstraintConflict(Model):
    field: Literal["requirements", "known_conditions"]
    index: Annotated[int, Field(strict=True, ge=0)]
    reason: ShortText


class ConstraintReview(Model):
    conflicts: list[ConstraintConflict] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def references(self, info: ValidationInfo):
        request = (info.context or {}).get("request")
        if request:
            for conflict in self.conflicts:
                if conflict.index >= len(request[conflict.field]):
                    raise ValueError("文字条件引用不存在")
        return self


class PlanDraft(Model):
    days: list[DayPlan] = Field(min_length=3, max_length=7)
    assumptions: TextList = Field(default_factory=list)
    constraint_conflicts: list[ConstraintConflict] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def references(self, info: ValidationInfo):
        ids = [activity.id for day in self.days for activity in day.activities]
        if len(ids) != len(set(ids)):
            raise ValueError("活动 ID 需要唯一")
        dates = [day.on_date for day in self.days]
        if dates != sorted(set(dates)):
            raise ValueError("行程日期需要唯一且升序")
        context = info.context or {}
        if not context.get("allow_empty_days") and any(not day.activities for day in self.days):
            raise ValueError("生成行程时每天需要至少一项活动")
        if context:
            if dates != context["dates"]:
                raise ValueError("行程需要覆盖本次要求的每一天")
            for day in self.days:
                for activity in day.activities:
                    if activity.place_id not in context["place_ids"]:
                        raise ValueError("活动引用不存在的地点")
                    for cost in activity.costs:
                        if cost.evidence_id:
                            fact = context["facts"].get(cost.evidence_id)
                            if (
                                not fact
                                or fact.place_id != activity.place_id
                                or fact.on_date != day.on_date
                            ):
                                raise ValueError("费用依据需要对应本地点与当天")
                            if (
                                (cost.category, fact.kind)
                                not in {("ticket", "ticket_price"), ("meal", "meal_price")}
                                or cost.amount_cents != fact.amount_cents
                                or cost.basis != fact.basis
                            ):
                                raise ValueError("费用与事实依据的口径或金额不一致")
            for conflict in self.constraint_conflicts:
                if conflict.index >= len(context["request"][conflict.field]):
                    raise ValueError("明确条件引用不存在")
        return self


class ValidationIssue(Model):
    rule: str
    severity: Literal["conflict", "unknown"]
    object_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    message: str


class ValidationResult(Model):
    status: Literal["passed", "needs_review", "blocked"]
    issues: list[ValidationIssue]
    known_cost_cents: int
    estimated_cost_cents: int
    unknown_cost_count: int


class TransportLeg(Model):
    id: str
    from_activity_id: str
    to_activity_id: str
    route: Route


class Itinerary(Model):
    id: str
    trip_id: str
    revision: int
    request_revision: int
    job_id: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    currency: Literal["CNY"] = "CNY"
    days: list[DayPlan]
    assumptions: list[str]
    places: list[Place]
    sources: list[SearchSource]
    evidence: list[FactEvidence]
    transport: list[TransportLeg]
    validation: ValidationResult
    knowledge_chunk_ids: list[str]
    notices: list[str]
    search_attempts: list[dict]
    constraint_conflicts: list[ConstraintConflict] = Field(default_factory=list)
    created_at: datetime
    planning_context: dict = Field(default_factory=dict)


def trip_dates(request: dict) -> list[date]:
    start = date.fromisoformat(str(request["start_date"]))
    end = date.fromisoformat(str(request["end_date"]))
    return [start + timedelta(days=index) for index in range((end - start).days + 1)]
