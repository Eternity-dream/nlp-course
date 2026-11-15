import math
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from travel_planner.domain.errors import AppError

MESSAGES = {
    "not_configured": "服务未配置，请检查 .env 并重启",
    "auth_failed": "服务鉴权失败，请检查密钥及接口权限",
    "quota_exhausted": "服务额度已耗尽，请检查账户额度",
    "rate_limited": "服务限流，已记录冷却状态",
    "request_invalid": "服务拒绝请求参数，请检查调用参数与协议",
    "timeout": "服务调用超时，请稍后重试",
    "unavailable": "服务暂时不可用，请稍后重试",
    "invalid_response": "服务返回内容不符合协议",
    "empty_results": "服务没有返回可用来源",
    "source_unavailable": "该网页无法提取正文，请换用其他来源",
    "unsupported_tool": "MCP 未提供兼容的搜索或正文工具",
    "model_invalid_output": "模型输出仍未通过 JSON 或业务结构校验",
}


class ProviderError(AppError):
    def __init__(self, provider: str, reason: str, *, retry_at: datetime | None = None):
        super().__init__(
            f"provider_{reason}",
            MESSAGES[reason],
            422 if reason == "request_invalid" else 502,
            retryable=reason in {"rate_limited", "timeout", "unavailable"},
        )
        self.provider = provider
        self.reason = reason
        self.retry_at = retry_at

    def payload(self) -> dict:
        return {
            **super().payload(),
            "provider": self.provider,
            "retry_at": self.retry_at.isoformat() if self.retry_at else None,
        }


def retry_time(headers: dict, now: datetime) -> datetime | None:
    value = headers.get("retry-after")
    if value is not None:
        try:
            seconds = float(value)
            if math.isfinite(seconds) and 0 <= seconds <= 31536000:
                return now + timedelta(seconds=seconds)
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is not None:
                return max(now, parsed.astimezone(UTC))
        except (ValueError, TypeError, OverflowError):
            pass
    for key in ("x-ratelimit-reset", "ratelimit-reset"):
        try:
            seconds = float(headers[key])
            if math.isfinite(seconds) and 0 <= seconds <= 32503680000:
                # X-RateLimit-Reset 为 Unix 时间；RateLimit-Reset 为剩余秒数。
                reset = (
                    datetime.fromtimestamp(seconds, UTC)
                    if key.startswith("x-")
                    else now + timedelta(seconds=seconds)
                )
                return max(now, reset)
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            continue
    return None


def http_error(provider: str, status: int, headers: dict) -> ProviderError:
    reason = {
        401: "auth_failed",
        403: "auth_failed",
        402: "quota_exhausted",
        429: "rate_limited",
    }.get(status)
    if reason is None:
        reason = "request_invalid" if status in {400, 404, 405, 415, 422} else "unavailable"
    return ProviderError(
        provider,
        reason,
        retry_at=retry_time(headers, datetime.now(UTC)) if reason == "rate_limited" else None,
    )
