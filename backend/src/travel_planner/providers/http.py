import asyncio
import json

import httpx

from travel_planner.providers.errors import ProviderError, http_error
from travel_planner.providers.logging import sanitize_http_logs

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ProviderHTTP:
    def __init__(self, client: httpx.AsyncClient, timeout: float = 30):
        sanitize_http_logs()
        self.client = client
        self.timeout = timeout

    async def json(self, provider: str, method: str, url: str, **kwargs) -> dict:
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream(
                    method, url, timeout=self.timeout, follow_redirects=False, **kwargs
                ) as response:
                    # 不将供应商错误正文放入异常或日志；AnySearch 402 可能包含新凭据。
                    if not response.is_success:
                        if provider == "anysearch" and response.status_code == 422:
                            body = await self._read(provider, response)
                            if body.get("error_code") == "extract_failed":
                                raise ProviderError(provider, "source_unavailable")
                        if provider == "llm" and response.status_code in {400, 422}:
                            try:
                                body = await self._read(provider, response)
                            except ProviderError:
                                raise http_error(
                                    provider, response.status_code, dict(response.headers)
                                ) from None
                            error = body.get("error", {})
                            if (
                                isinstance(error, dict)
                                and error.get("param") == "response_format"
                                and "response_format" in kwargs.get("json", {})
                            ):
                                code = error.get("code")
                                message = str(error.get("message", "")).lower()
                                if code in {"unsupported_parameter", "unsupported_value"} or (
                                    "not support" in message or "unsupported" in message
                                ):
                                    raise UnsupportedJSONMode()
                        raise http_error(provider, response.status_code, dict(response.headers))
                    return await self._read(provider, response)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderError(provider, "timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(provider, "unavailable") from exc

    async def _read(self, provider: str, response: httpx.Response) -> dict:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ProviderError(provider, "invalid_response")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise ProviderError(provider, "invalid_response") from exc
        if not isinstance(value, dict):
            raise ProviderError(provider, "invalid_response")
        return value


class UnsupportedJSONMode(Exception):
    """服务明确拒绝可选 JSON 模式参数。"""
