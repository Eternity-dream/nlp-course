"""引用存在性和日期适用计算；语义对应由独立模型审阅后再采用。"""

import re
import unicodedata
from datetime import date


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def has_quote(quote, text):
    return (
        bool(quote)
        and normalized(quote) in normalized(text or "")
        and "[正文节选分隔]" not in quote
    )


def applicable_on(claim):
    rule = claim.applicability
    if not rule or rule.mode == "unspecified":
        return "uncertain", "来源未明确日期适用规则"
    day = claim.on_date
    if rule.start_date and day < rule.start_date or rule.end_date and day > rule.end_date:
        return "outside", "事实超出来源生效日期"
    if rule.start_date and rule.end_date and rule.end_date < rule.start_date:
        return "outside", "来源生效区间无效"
    if rule.mode == "exact" and (rule.start_date != day or rule.end_date not in {None, day}):
        return "outside", "特定日期公告不适用于当天"
    if rule.mode == "range" and not (rule.start_date and rule.end_date):
        return "uncertain", "日期范围不完整"
    if bool(rule.start_month_day) != bool(rule.end_month_day):
        return "uncertain", "季节范围不完整"
    if rule.start_month_day:
        try:
            date.fromisoformat("2000-" + rule.start_month_day)
            date.fromisoformat("2000-" + rule.end_month_day)
        except ValueError:
            return "outside", "季节日期无效"
        month_day = day.strftime("%m-%d")
        start, end = rule.start_month_day, rule.end_month_day
        inside = (
            start <= month_day <= end if start <= end else month_day >= start or month_day <= end
        )
        if not inside:
            return "outside", "活动日期不在来源季节范围内"
    if rule.weekdays and day.isoweekday() not in rule.weekdays:
        return "outside", "活动日期不在来源适用星期内"
    if day in rule.excluded_dates:
        return "outside", "活动日期属于来源排除日期"
    if rule.has_exceptions or any(
        word in rule.quote for word in ("节假日", "节日除外", "另行通知", "特殊情况")
    ):
        return "uncertain", "来源包含例外条件，需补查当天公告"
    return "applicable", "日期符合来源适用规则"


def verify_claim(claim, source, place, check):
    """返回身份、日期、解释、来源四项结果，任何缺口不自动提升为核实。"""
    checks = {
        "identity": "unknown",
        "date": "unknown",
        "interpretation": "unknown",
        "authority": "unknown",
    }
    if not has_quote(claim.quote, source.text) or not source.fetched_at:
        return "unknown", "事实引用未在抓取正文中找到", checks
    if not check:
        return "unknown", "事实语义审核尚未完成", checks
    checks.update(
        identity=check.identity, interpretation=check.interpretation, authority=check.authority
    )
    if not has_quote(claim.identity_quote, source.text):
        return "unknown", "缺少可定位的地点身份引用", checks
    if check.identity != "matched":
        return "unknown", check.reason, checks
    if check.interpretation != "supported":
        return "unknown", check.reason, checks
    if not claim.applicability or not has_quote(claim.applicability.quote, source.text):
        return "estimated", "数值有来源，日期适用依据不足", checks
    status, reason = applicable_on(claim)
    checks["date"] = status
    if status == "outside":
        return "unknown", reason, checks
    if status != "applicable":
        return "estimated", reason, checks
    if check.authority != "official":
        return "estimated", "依据为第三方来源，仍需官方信息确认", checks
    return "supported", "地点、原文与日期规则已对应", checks
