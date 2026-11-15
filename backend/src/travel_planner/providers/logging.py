import logging


class HTTPStatusOnly(logging.Filter):
    """HTTPX 默认请求日志包含完整 URL，高德密钥位于查询参数。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg == 'HTTP Request: %s %s "%s %d %s"' and isinstance(record.args, tuple):
            method, _, _, status, _ = record.args
            record.msg = "HTTP request completed: method=%s status=%s"
            record.args = (method, status)
        return True


def sanitize_http_logs():
    logger = logging.getLogger("httpx")
    if not any(isinstance(item, HTTPStatusOnly) for item in logger.filters):
        logger.addFilter(HTTPStatusOnly())
