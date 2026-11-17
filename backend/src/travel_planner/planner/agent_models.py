"""规划动作、意图与可恢复工作状态。动作只引用已经取得的公开对象。"""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, ValidationInfo, model_validator

from travel_planner.domain.models import Model, ShortText, TextList, TimeConstraint
from travel_planner.planner.models import FactEvidence, PlanDraft, TransportLeg, ValidationResult
from travel_planner.providers.models import Place, SearchSource


class InterpretedTime(Model):
    source_field: Literal["requirements", "known_conditions"]
    source_index: Annotated[int, Field(ge=0)]
    quote: ShortText
    constraint: TimeConstraint


class DailyOutline(Model):
    on_date: date
    area: ShortText
    focus: ShortText


class TravelIntent(Model):
    summary: ShortText
    explicit_constraints: TextList = Field(default_factory=list)
    preferences: TextList = Field(default_factory=list)
    assumptions: TextList = Field(default_factory=list)
    open_tasks: TextList = Field(default_factory=list)
    interpreted_times: list[InterpretedTime] = Field(default_factory=list, max_length=30)
    outline: list[DailyOutline] = Field(default_factory=list, max_length=7)


class InterpretAction(Model):
    kind: Literal["interpret"]
    intent: TravelIntent


class PlaceQuery(Model):
    query: Annotated[str, Field(min_length=1, max_length=80)]
    purpose: Literal["sightseeing", "meal", "leisure", "transport"]


class PlacesAction(Model):
    kind: Literal["places"]
    queries: list[PlaceQuery] = Field(min_length=1, max_length=10)


class EvidenceQuery(Model):
    place_id: ShortText
    topic: Annotated[str, Field(min_length=1, max_length=100)]


class ResearchAction(Model):
    kind: Literal["research"]
    queries: list[EvidenceQuery] = Field(min_length=1, max_length=8)


class KnowledgeAction(Model):
    kind: Literal["knowledge"]
    query: ShortText


class DraftAction(Model):
    kind: Literal["draft"]
    instructions: Annotated[str, Field(min_length=1, max_length=2000)]
    selected_place_ids: list[ShortText] = Field(min_length=1, max_length=80)


class RoutesAction(Model):
    kind: Literal["routes"]
    origin_id: ShortText
    destination_id: ShortText
    mode: Literal["walking", "transit", "driving"]


class AskAction(Model):
    kind: Literal["ask"]
    questions: list[ShortText] = Field(min_length=1, max_length=2)


class FinishAction(Model):
    kind: Literal["finish"]


class AgentDecision(Model):
    purpose: ShortText
    action: Annotated[
        InterpretAction
        | PlacesAction
        | ResearchAction
        | KnowledgeAction
        | DraftAction
        | RoutesAction
        | AskAction
        | FinishAction,
        Field(discriminator="kind"),
    ]

    @model_validator(mode="after")
    def references(self, info: ValidationInfo):
        context = info.context or {}
        places = context.get("place_ids", set())
        action = self.action
        ids = []
        if isinstance(action, ResearchAction):
            ids = [query.place_id for query in action.queries]
        elif isinstance(action, DraftAction):
            ids = action.selected_place_ids
        elif isinstance(action, RoutesAction):
            ids = [action.origin_id, action.destination_id]
        if any(key not in places for key in ids):
            raise ValueError("动作只能引用已取得的地点")
        if isinstance(action, InterpretAction) and context.get("request"):
            request = context["request"]
            for item in action.intent.interpreted_times:
                original = request[item.source_field]
                if (
                    item.source_index >= len(original)
                    or item.quote not in original[item.source_index]
                ):
                    raise ValueError("抵离或预约时间必须引用用户原文")
                if item.constraint.on_date not in context["dates"]:
                    raise ValueError("解释的时间必须位于行程日期内")
            if any(day.on_date not in context["dates"] for day in action.intent.outline):
                raise ValueError("每日思路超出行程日期")
        return self


class ReviewFinding(Model):
    category: Literal["repetition", "place_suitability", "pace", "intent", "evidence", "geography"]
    severity: Literal["conflict", "unknown"]
    activity_ids: list[ShortText] = Field(default_factory=list, max_length=30)
    evidence_ids: list[ShortText] = Field(default_factory=list, max_length=30)
    message: ShortText
    suggestion: ShortText


class QualityReview(Model):
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def references(self, info: ValidationInfo):
        context = info.context or {}
        if "activity_ids" not in context:
            return self
        for finding in self.findings:
            if any(key not in context.get("activity_ids", set()) for key in finding.activity_ids):
                raise ValueError("审核引用不存在的活动")
            if any(key not in context.get("fact_ids", set()) for key in finding.evidence_ids):
                raise ValueError("审核引用不存在的事实")
        return self


class ResearchPlan(Model):
    queries: list[EvidenceQuery] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def references(self, info: ValidationInfo):
        places = (info.context or {}).get("place_ids", set())
        if any(query.place_id not in places for query in self.queries):
            raise ValueError("查询必须对应受影响日期的地点")
        return self


class AgentState(Model):
    version: Literal[1] = 1
    request_revision: int
    intent: TravelIntent | None = None
    places: dict[str, Place] = Field(default_factory=dict)
    sources: dict[str, SearchSource] = Field(default_factory=dict)
    facts: list[FactEvidence] = Field(default_factory=list)
    draft: PlanDraft | None = None
    transport: list[TransportLeg] = Field(default_factory=list)
    validation: ValidationResult | None = None
    review: QualityReview | None = None
    search_attempts: list[dict] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    completed_queries: list[str] = Field(default_factory=list)
    used_chunk_ids: list[str] = Field(default_factory=list)
    draft_count: int = 0
    steps: int = 0
    tool_calls: int = 0


class ActivityIntroduction(Model):
    activity_id: ShortText
    description: Annotated[str, Field(min_length=1, max_length=200)]


class ActivityIntroductions(Model):
    introductions: list[ActivityIntroduction] = Field(max_length=105)

    @model_validator(mode="after")
    def activity_ids(self, info: ValidationInfo):
        expected = (info.context or {}).get("activity_ids")
        ids = [item.activity_id for item in self.introductions]
        if expected is not None and (set(ids) != expected or len(ids) != len(set(ids))):
            raise ValueError("介绍需要与每项活动一一对应")
        return self
