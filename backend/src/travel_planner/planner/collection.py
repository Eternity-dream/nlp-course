import json

from travel_planner.planner.evidence import build_evidence
from travel_planner.planner.models import ExtractedFacts


async def collect_evidence(services, request: dict, places: dict, dates: list):
    """每个任务单独查询公开事实；规划与编辑共用相同提取与来源校验。"""
    attempts, notices = [], []
    scope, sources = services.search.scope(), {}
    for place in places.values():
        result = await scope.search(
            request["destination"],
            f"{place.name} {place.address or ''} {min(dates)} 至 {max(dates)} "
            "开放时间 票价 餐费 官方",
            2,
        )
        attempts.extend(attempt.model_dump(mode="json") for attempt in result.attempts)
        for source in result.sources:
            fetched = await scope.fetch(source)
            attempts.extend(attempt.model_dump(mode="json") for attempt in fetched.attempts)
            source = fetched.sources[0] if fetched.sources else source
            if not any(existing.url == source.url for existing in sources.values()):
                sources[source.id] = source.model_copy(
                    update={"text": source.text[:6000] if source.text else None}
                )
        if len(sources) >= 24:
            break
    if not sources:
        notices.append("网络搜索没有取得有效来源，营业时间和票价保留待确认")
    facts = []
    if any(source.text for source in sources.values()):
        public = {
            "operation": "extract_facts",
            "places": [p.model_dump(mode="json") for p in places.values()],
            "sources": [s.model_dump(mode="json") for s in sources.values()],
            "dates": [day.isoformat() for day in dates],
        }
        extracted = await services.model.generate(
            [
                {
                    "role": "system",
                    "content": "从所给正文提取营业时间及票价。引用原文连续片段和已有来源 ID。"
                    "逐个地点与适用日期记录；没有可靠对应内容就不输出该事实。"
                    "跨午夜的结束分钟加 1440。",
                },
                {"role": "user", "content": json.dumps(public, ensure_ascii=False)},
            ],
            ExtractedFacts,
            context={"place_ids": set(places), "source_ids": set(sources), "dates": dates},
        )
        facts = build_evidence(extracted, sources, places)
    return sources, facts, attempts, notices
