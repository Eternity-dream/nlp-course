from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
TextList = Annotated[list[ShortText], Field(max_length=100)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]


def iso_date(value):
    if value is None or type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError("日期需要使用 YYYY-MM-DD 格式")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期需要使用 YYYY-MM-DD 格式") from exc
    if parsed.isoformat() != value:
        raise ValueError("日期需要使用 YYYY-MM-DD 格式")
    return parsed


TripDate = Annotated[date | None, BeforeValidator(iso_date)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeConstraint(Model):
    kind: Literal["arrival", "departure", "reservation"]
    on_date: date
    minute: Annotated[int, Field(strict=True, ge=0, lt=1440)]
    end_minute: Annotated[int, Field(strict=True, ge=1, le=2880)] | None = None
    place_name: ShortText | None = None

    @field_validator("on_date", mode="before")
    @classmethod
    def date_format(cls, value):
        if value is None:
            raise ValueError("请提供时间约束日期")
        return iso_date(value)

    @model_validator(mode="after")
    def reservation(self):
        if self.end_minute is not None and self.end_minute <= self.minute:
            raise ValueError("预约结束需要晚于开始")
        if self.kind == "reservation" and not self.place_name:
            raise ValueError("预约需要地点名称")
        return self


class Clarification(Model):
    job_id: ShortText
    request_revision: PositiveInt
    questions: TextList
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class TripFields(Model):
    destination: ShortText
    start_date: TripDate = None
    end_date: TripDate = None
    travelers: Annotated[int, Field(strict=True, ge=1, le=1000)] | None = None
    budget_cents: Annotated[int, Field(strict=True, ge=0)] | None = None
    requirements: TextList = Field(default_factory=list)
    priorities: TextList = Field(default_factory=list)
    known_conditions: TextList = Field(default_factory=list)
    assumptions: TextList = Field(default_factory=list)
    time_constraints: list[TimeConstraint] = Field(default_factory=list, max_length=30)
    reference_ids: TextList = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date and self.end_date:
            days = (self.end_date - self.start_date).days + 1
            if not 3 <= days <= 7:
                raise ValueError("起止日期均计入天数，行程需要为 3–7 天")
            if any(
                not self.start_date <= item.on_date <= self.end_date
                for item in self.time_constraints
            ):
                raise ValueError("时间约束日期需要在行程日期内")
        return self


class TripPatch(Model):
    expected_revision: PositiveInt
    destination: ShortText | None = None
    start_date: TripDate = None
    end_date: TripDate = None
    travelers: Annotated[int, Field(strict=True, ge=1, le=1000)] | None = None
    budget_cents: Annotated[int, Field(strict=True, ge=0)] | None = None
    requirements: TextList | None = None
    priorities: TextList | None = None
    known_conditions: TextList | None = None
    assumptions: TextList | None = None
    time_constraints: list[TimeConstraint] | None = Field(default=None, max_length=30)
    reference_ids: TextList | None = None
    clarifications: list[Clarification] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def check_nulls(self):
        for key in {
            "destination",
            "requirements",
            "priorities",
            "known_conditions",
            "assumptions",
            "time_constraints",
            "clarifications",
            "reference_ids",
        }:
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} 不能为 null")
        return self


class Trip(TripFields):
    id: str
    revision: int
    currency: Literal["CNY"] = "CNY"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    missing_fields: list[str]
    created_at: datetime
    updated_at: datetime


class PreferencePut(Model):
    expected_revision: PositiveInt
    preferences: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=100)
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)] | None = None


class PreferenceProfile(Model):
    revision: int
    preferences: dict[str, str]
    notes: str
    updated_at: datetime


class KnowledgeCreate(Model):
    kind: Literal["text", "url", "bookmark"] = "text"
    title: ShortText
    text: Annotated[str, Field(max_length=20 * 1024 * 1024)] | None = None
    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4096)] | None = None

    @model_validator(mode="after")
    def check_content(self):
        if self.kind == "text":
            if not self.text or not self.text.strip():
                raise ValueError("请提供资料正文")
            if self.url is not None:
                raise ValueError("文本资料不接受 URL，请使用 url 或 bookmark 类型")
        else:
            if not self.url:
                raise ValueError("链接和收藏需要 URL")
            if self.text is not None:
                raise ValueError("链接和收藏的正文由导入器提取")
        return self


class KnowledgeSource(Model):
    id: str
    kind: Literal["text", "pdf", "url", "bookmark"]
    title: str
    url: str | None
    status: Literal["pending", "processing", "ready", "failed"]
    sha256: str
    error: dict | None
    created_at: datetime
    updated_at: datetime


class KnowledgeChunk(Model):
    id: str
    source_id: str
    ordinal: int
    text: str
    page: int | None
    paragraph: int | None
    offset: int


class KnowledgeDetail(KnowledgeSource):
    chunks: list[KnowledgeChunk]


class KnowledgeHit(KnowledgeChunk):
    title: str
    url: str | None
    score: float
