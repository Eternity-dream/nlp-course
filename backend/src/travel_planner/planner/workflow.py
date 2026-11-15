import json
from datetime import UTC, datetime

from starlette.concurrency import run_in_threadpool

from travel_planner.domain.errors import AppError
from travel_planner.knowledge.text import relevant_to_destination
from travel_planner.planner.collection import collect_evidence
from travel_planner.planner.models import (
    CandidatePlan,
    PlanDraft,
    TransportLeg,
    trip_dates,
)
from travel_planner.planner.validation import validate_plan
from travel_planner.providers.errors import ProviderError
from travel_planner.providers.models import Route
from travel_planner.storage.repository import new_id


class AwaitingInput(Exception):
    def __init__(self, questions: list[str]):
        self.questions = questions


class Workflow:
    def __init__(self, store, repository, knowledge, services):
        self.store, self.repository, self.knowledge, self.services = (
            store,
            repository,
            knowledge,
            services,
        )

    async def stage(self, job: dict, name: str):
        await run_in_threadpool(self.store.update, job["id"], "running", name)

    async def run(self, job: dict) -> dict:
        request = await run_in_threadpool(self.store.snapshot, job)
        missing = self.store._missing(request)
        if missing:
            labels = {"start_date": "出发日期", "end_date": "结束日期", "travelers": "同行人数"}
            raise AwaitingInput([f"请补充{labels[key]}" for key in missing])
        dates = trip_dates(request)
        preferences = await run_in_threadpool(self.repository.get_preferences)
        notices, attempts = [], []
        await self.stage(job, "knowledge")
        query = " ".join([request["destination"], *request["priorities"], *request["requirements"]])
        hits = await run_in_threadpool(self.knowledge.search, query)
        chunk_ids = [
            hit["id"]
            for hit in hits
            if relevant_to_destination(hit["text"], request["destination"])
        ]
        if not chunk_ids:
            notices.append("个人资料未命中，使用本次要求、偏好和公开来源继续规划")
        used_chunks = set()

        async def messages(instruction: str, public: dict):
            current = await run_in_threadpool(self.knowledge.live_chunks, chunk_ids)
            trimmed, remaining = [], 6400
            for chunk in current:
                if remaining <= 0:
                    break
                text = chunk["text"][:remaining]
                trimmed.append(
                    {
                        "id": chunk["id"],
                        "source_id": chunk["source_id"],
                        "text": text,
                        "page": chunk["page"],
                        "paragraph": chunk["paragraph"],
                    }
                )
                used_chunks.add(chunk["id"])
                remaining -= len(text)
            data = {
                "request": request,
                "preferences": preferences["preferences"],
                "personal_material": trimmed,
                **public,
            }
            return [
                {
                    "role": "system",
                    "content": (
                        "本次明确要求优先，长期偏好辅助；个人资料只补充背景，不自动成为硬约束。"
                        "不输出个性化匹配理由。不放宽明确要求，不编造地点、来源或已核实事实。"
                        + instruction
                    ),
                },
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ]

        async def candidates_input():
            return await messages(
                "提出本城 6–12 个具体景点、餐馆或休闲地点关键词。"
                "仅对会明显改变安排的关键缺口提问，普通偏好缺失继续。",
                {"operation": "candidates"},
            )

        candidates = await self.services.model.generate(
            [], CandidatePlan, prepare_messages=candidates_input
        )
        if candidates.questions:
            raise AwaitingInput(candidates.questions)
        await self.stage(job, "places")
        places = {}
        city = request["destination"].removesuffix("市")
        keywords = list(
            dict.fromkeys(
                [
                    *candidates.place_keywords,
                    *[
                        item["place_name"]
                        for item in request["time_constraints"]
                        if item["place_name"]
                    ],
                ]
            )
        )[:12]
        for keyword in keywords:
            found = await self.services.amap.places(request["destination"], keyword, 3)
            for place in found:
                if place.city and place.city.removesuffix("市") != city:
                    notices.append("地点服务返回其他城市地点，已排除")
                    continue
                places[place.id] = place
        if not places:
            raise AppError(
                "no_places",
                "没有取得本城市可用地点，请补充具体地点或检查地图服务",
                502,
                retryable=True,
            )
        # 限定候选与上下文规模，地点身份始终来自地图响应。
        places = dict(list(places.items())[:24])
        await self.stage(job, "evidence")
        sources, facts, attempts, evidence_notices = await collect_evidence(
            self.services, request, places, dates
        )
        notices.extend(evidence_notices)
        context = {
            "request": request,
            "dates": dates,
            "place_ids": set(places),
            "facts": {fact.id: fact for fact in facts},
        }
        repair = []
        route_cache = {}
        for round_index in range(3):
            await self.stage(job, "generation" if round_index == 0 else "repair")

            async def draft_input(repair=repair):
                return await messages(
                    "按日期生成早中晚弹性安排，包含餐饮。时间为相对当天零点的分钟，跨午夜结束加1440。"
                    "仅引用提供的地点与费用依据 ID；每项注明门票或餐费，未知金额使用 null。"
                    "交通费用由路线后续检查，不在活动费用中重复填写。按人价乘同行人数，按组价计一次。"
                    "检查明确要求，如已知违反则填写 constraint_conflicts，不能以假设覆盖要求。",
                    {
                        "operation": "draft",
                        "places": [p.model_dump(mode="json") for p in places.values()],
                        "evidence": [f.model_dump(mode="json") for f in facts],
                        "repair_issues": repair,
                    },
                )

            draft = await self.services.model.generate(
                [], PlanDraft, context=context, prepare_messages=draft_input
            )
            await self.stage(job, "routes")
            transport = []
            for day in draft.days:
                for first, second in zip(day.activities, day.activities[1:], strict=False):
                    key = (first.place_id, second.place_id, second.transport_mode)
                    if key not in route_cache:
                        try:
                            route_cache[key] = await self.services.amap.route(
                                places[first.place_id],
                                places[second.place_id],
                                second.transport_mode,
                            )
                        except ProviderError as exc:
                            if exc.reason == "request_invalid":
                                raise
                            route_cache[key] = Route(
                                origin_place_id=first.place_id,
                                destination_place_id=second.place_id,
                                mode=second.transport_mode,
                                duration_seconds=None,
                                distance_meters=None,
                                queried_at=datetime.now(UTC),
                                status="unknown",
                            )
                            notices.append(exc.message)
                    transport.append(
                        TransportLeg(
                            id=new_id(),
                            from_activity_id=first.id,
                            to_activity_id=second.id,
                            route=route_cache[key],
                        )
                    )
            await self.stage(job, "validation")
            validation = validate_plan(request, draft, places, facts, transport)
            if validation.status != "blocked" or round_index == 2:
                break
            repair = [
                item.model_dump(mode="json")
                for item in validation.issues
                if item.severity == "conflict"
            ]
        # 对已保存结果分配应用生成的稳定活动 ID。
        mapping = {a.id: new_id() for day in draft.days for a in day.activities}
        for day in draft.days:
            for activity in day.activities:
                activity.id = mapping[activity.id]
        for leg in transport:
            leg.from_activity_id = mapping[leg.from_activity_id]
            leg.to_activity_id = mapping[leg.to_activity_id]
        for issue in validation.issues:
            issue.object_ids = [mapping.get(item, item) for item in issue.object_ids]
        assumptions = list(
            dict.fromkeys(
                [
                    *request["assumptions"],
                    *draft.assumptions,
                    "活动开始、结束和停留时长为排程估计；未提供抵离时间时按完整白天安排",
                ]
            )
        )
        await self.stage(job, "saving")
        return {
            "days": [day.model_dump(mode="json") for day in draft.days],
            "assumptions": assumptions,
            "places": [p.model_dump(mode="json") for p in places.values()],
            "sources": [s.model_dump(mode="json") for s in sources.values()],
            "evidence": [f.model_dump(mode="json") for f in facts],
            "transport": [leg.model_dump(mode="json") for leg in transport],
            "validation": validation.model_dump(mode="json"),
            "knowledge_chunk_ids": sorted(used_chunks),
            "notices": list(dict.fromkeys(notices)),
            "search_attempts": attempts,
            "constraint_conflicts": [
                item.model_dump(mode="json") for item in draft.constraint_conflicts
            ],
        }
