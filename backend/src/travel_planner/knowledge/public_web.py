"""公开网页下载：逐跳校验 DNS，并将连接固定到校验后的地址。"""

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import quote, urljoin, urlsplit

from travel_planner.domain.errors import AppError

MAX_BYTES = 20 * 1024 * 1024


def validate_url(url: str):
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.port == 0
            or any(ord(char) < 32 for char in url)
            or "\\" in url
            or len(url) > 4096
        ):
            raise ValueError
        return parts
    except ValueError as exc:
        raise AppError("invalid_url", "资料 URL 需要有效的公开 HTTP(S) 地址", 422) from exc


def resolve_public(host: str, port: int) -> list[str]:
    try:
        addresses = list(
            dict.fromkeys(
                info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            )
        )
    except OSError as exc:
        raise AppError(
            "url_dns_failed", "网页域名无法解析，请稍后重试或粘贴正文", retryable=True
        ) from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or getattr(ip, "ipv4_mapped", None) is not None:
            raise AppError("private_url", "资料 URL 只能访问公网地址，请粘贴正文", 422)
    if not addresses:
        raise AppError("url_dns_failed", "网页域名没有可用地址", retryable=True)
    return addresses


def _connection(parts, address: str):
    port = parts.port or (443 if parts.scheme == "https" else 80)
    # 保留原始 Host 和 TLS SNI，底层 socket 仅连接已校验的数值地址。
    connection = http.client.HTTPConnection(parts.hostname, port, timeout=10)
    sock = socket.create_connection((address, port), timeout=10)
    try:
        if parts.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        connection.sock = sock
        return connection
    except BaseException:
        sock.close()
        raise


def fetch_public(url: str) -> tuple[bytes, str]:
    current = url
    try:
        for _ in range(6):
            parts = validate_url(current)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            addresses = resolve_public(parts.hostname, port)
            connection = _connection(parts, addresses[0])
            try:
                path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
                if parts.query:
                    path += "?" + quote(parts.query, safe="%=&;:+,/?@!$'()*-._~")
                connection.request(
                    "GET",
                    path,
                    headers={
                        "User-Agent": "TravelPlanner/0.1",
                        "Accept": "text/html,text/plain",
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise AppError("url_redirect", "网页跳转缺少目标地址，请粘贴正文")
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise AppError(
                        "url_fetch_failed",
                        "网页无法读取，请粘贴正文或上传文件",
                        retryable=response.status >= 500 or response.status == 429,
                    )
                content_type = (response.getheader("Content-Type") or "").split(";")[0].lower()
                if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise AppError("url_content_type", "链接需要网页或纯文本，PDF 请直接上传")
                if response.getheader("Content-Encoding", "identity") != "identity":
                    raise AppError("url_encoding", "网页返回压缩内容，请粘贴正文")
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise AppError("source_too_large", "网页超过 20 MB，请粘贴所需正文", 413)
                return data, content_type
            finally:
                connection.close()
        raise AppError("url_redirect", "网页跳转次数超过上限，请粘贴正文")
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise AppError(
            "url_fetch_failed", "网页连接失败，请稍后重试或粘贴正文", retryable=True
        ) from exc
