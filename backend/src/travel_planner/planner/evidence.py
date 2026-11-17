import re
from collections import defaultdict

from travel_planner.planner.fact_verification import normalized, verify_claim
from travel_planner.planner.models import ExtractedFacts, FactChecks, FactClaim, FactEvidence
from travel_planner.providers.models import Place, SearchSource
from travel_planner.storage.repository import new_id


def compact(text: str) -> str:
    return normalized(text)


def minute_present(minute: int, text: str) -> bool:
    hour, remainder = divmod(minute % 1440, 60)
    forms = {
        f"{hour:02}:{remainder:02}",
        f"{hour}:{remainder:02}",
        f"{hour}时{remainder:02}分",
        f"{hour}点{remainder:02}分",
    }
    if remainder == 0:
        forms |= {f"{hour}时", f"{hour}点"}
    if minute == 1440:
        forms.add("24:00")
    return any(re.search(r"(?<!\d)" + re.escape(form) + r"(?!\d)", text) for form in forms)


def interval_present(window, text: str) -> bool:
    start_hour, start_minute = divmod(window.start_minute, 60)
    end_hour, end_minute = divmod(window.end_minute % 1440, 60)
    start = rf"(?<!\d)0?{start_hour}:0?{start_minute}"
    end = rf"0?{end_hour}:0?{end_minute}(?!\d)"
    if window.end_minute == 1440:
        end = rf"(?:{end}|24:00)"
    return bool(re.search(start + r"(?:至|到|[-—~～–])(?:次日)?" + end, text))


def date_present(claim, text: str) -> bool:
    day = claim.on_date
    return any(
        value in text
        for value in (
            day.isoformat(),
            f"{day.year}年{day.month}月{day.day}日",
            f"{day.year}年{day.month:02}月{day.day:02}日",
        )
    )


def build_evidence(
    extracted: ExtractedFacts,
    sources: dict[str, SearchSource],
    places: dict[str, Place],
    checks=None,
) -> list[FactEvidence]:
    evidence = []
    check_map = {check.claim_index: check for check in checks.checks} if checks else {}
    for index, claim in enumerate(extracted.claims):
        source, place = sources[claim.source_id], places[claim.place_id]
        text, quote = compact(source.text or ""), compact(claim.quote)
        status, reason = "supported", "正文引用、地点与日期可对应"
        verification = {}
        if claim.identity_quote or claim.applicability or checks is not None:
            status, reason, verification = verify_claim(claim, source, place, check_map.get(index))
        elif not source.text or not source.fetched_at or quote not in text:
            status, reason = "unknown", "来源缺少正文或引用片段未在正文中找到"
        elif (
            not place.address
            or compact(place.name) not in quote
            or compact(place.address) not in quote
        ):
            status, reason = "unknown", "正文未同时对应地点名称和地址，需确认具体场馆或门店"
        elif not date_present(claim, quote):
            status, reason = "estimated", "来源未明确该事实适用于行程当天，需核对日期"
        values_supported = True
        if claim.kind == "opening_hours":
            if claim.closed:
                values_supported = any(
                    marker in quote
                    for marker in ("全天闭馆", "当日闭馆", "不开放", "暂停开放", "全天休息")
                )
                values_supported |= bool(
                    re.search(
                        r"(?:\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日)[^\d:：]{0,8}闭馆", quote
                    )
                )
                if verification.get("interpretation") == "supported" and claim.applicability:
                    values_supported |= "闭馆" in quote or "休息" in quote
            else:
                values_supported = bool(claim.windows) and all(
                    minute_present(window.start_minute, quote)
                    and minute_present(window.end_minute, quote)
                    and (
                        interval_present(window, quote)
                        or (
                            verification.get("interpretation") == "supported"
                            and labelled_interval_present(window, quote)
                        )
                    )
                    for window in claim.windows
                )
                if claim.last_entry_minute is not None:
                    values_supported &= minute_present(claim.last_entry_minute, quote) and any(
                        marker in quote
                        for marker in ("停止检票", "停止入场", "停止入馆", "停止入园", "停止售票")
                    )
        elif claim.kind == "reservation":
            values_supported = claim.reservation_required is not None and bool(
                claim.reservation_note
            )
            values_supported &= any(marker in quote for marker in ("预约", "免预约", "无需预约"))
        else:
            if claim.amount_cents is None:
                values_supported = False
            elif claim.amount_cents == 0:
                values_supported = "免费" in quote or bool(re.search(r"(?<!\d)0元", quote))
            else:
                yuan = claim.amount_cents / 100
                values_supported = bool(
                    re.search(rf"(?<![\d.]){yuan:g}(?:\.00)?元|[¥￥]{yuan:g}(?![\d.])", quote)
                )
            if (
                claim.basis in {"per_group", "fixed"}
                and not any(
                    marker in quote
                    for marker in ("每组", "全团", "一组", "每车", "整车", "固定费用")
                )
                and status == "supported"
            ):
                status, reason = "estimated", "原文未确认按组或固定费用口径"
            if (
                claim.basis == "per_person"
                and any(marker in quote for marker in ("每组", "每车", "整车", "全团"))
                and not any(marker in quote for marker in ("每人", "人均", "/人"))
            ):
                status, reason = "unknown", "按组或整车价格不能当作每人费用"
            # 不将儿童或优惠价套用给所有同行者。
            if any(marker in quote for marker in ("儿童", "学生", "优惠", "老年", "半价")):
                if status == "supported":
                    status, reason = "estimated", "票价涉及优惠资格，需要确认同行者适用条件"
        if not values_supported:
            status, reason = "unknown", "提取的时间或金额未在所引原文中对应"
        evidence.append(
            FactEvidence(
                **claim.model_dump(),
                id=new_id(),
                source_url=source.url,
                queried_at=source.queried_at,
                fetched_at=source.fetched_at,
                status=status,
                reason=reason,
                verification={
                    **verification,
                    "values": "matched" if values_supported else "unmatched",
                },
            )
        )
    reconcile_conflicts(evidence)
    return evidence


def labelled_interval_present(window, text):
    def labelled(markers, minute):
        for marker in markers:
            for match in re.finditer(marker + r"(?:时间)?[:：]?", text):
                if minute_present(minute, text[match.end() : match.end() + 8]):
                    return True
                if not re.match(r"\d{1,2}[:时点]", text[match.end() :]) and minute_present(
                    minute, text[max(0, match.start() - 8) : match.start()]
                ):
                    return True
        return False

    return labelled(
        ("开放入馆", "开放", "开馆", "开园", "营业开始"), window.start_minute
    ) and labelled(("闭馆", "闭园", "关门", "营业结束"), window.end_minute)


def reconcile_conflicts(evidence):
    grouped = defaultdict(list)
    for item in evidence:
        if item.status in {"supported", "estimated", "conflict"}:
            grouped[(item.place_id, item.on_date, item.kind, item.price_scope)].append(item)
    for group in grouped.values():
        values = {
            (
                item.closed,
                tuple((w.start_minute, w.end_minute) for w in item.windows),
                item.last_entry_minute,
                item.amount_cents,
                item.basis,
                item.reservation_required,
            )
            for item in group
        }
        if len(values) > 1:
            for item in group:
                item.status, item.reason = (
                    "conflict",
                    "同一地点与日期的来源取值不一致，保留各来源待确认",
                )


def recheck_saved_evidence(facts, sources, places):
    """恢复任务时重算原文与日期校验，复用已保存的独立语义审核。"""
    result = []
    for fact in facts:
        review = fact.verification
        if review.get("identity") not in {"matched", "mismatch", "uncertain"}:
            result.append(fact)
            continue
        if fact.source_id not in sources or fact.place_id not in places:
            result.append(fact)
            continue
        checks = FactChecks(
            checks=[
                {
                    "claim_index": 0,
                    "identity": review["identity"],
                    "interpretation": review["interpretation"],
                    "authority": review["authority"],
                    "reason": fact.reason,
                }
            ]
        )
        claim = FactClaim.model_validate(
            {key: getattr(fact, key) for key in FactClaim.model_fields}
        )
        refreshed = build_evidence(ExtractedFacts(claims=[claim]), sources, places, checks)[0]
        refreshed.id = fact.id
        result.append(refreshed)
    reconcile_conflicts(result)
    return result
