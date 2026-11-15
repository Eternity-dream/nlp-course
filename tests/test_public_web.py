import socket

import pytest
from travel_planner.domain.errors import AppError
from travel_planner.knowledge import public_web
from travel_planner.knowledge.parse import extract


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "http://user:pass@example.com",
        "http://example.com:0/",
        "http://example.com:invalid/",
        "http://example.com/\nsecret",
        "http://example.com\\@127.0.0.1/",
    ],
)
def test_invalid_urls_rejected(url):
    with pytest.raises(AppError, match="HTTP"):
        public_web.validate_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "192.168.1.1",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
    ],
)
def test_nonpublic_dns_answers_rejected(monkeypatch, address):
    monkeypatch.setattr(
        public_web.socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)),
        ],
    )
    with pytest.raises(AppError) as error:
        public_web.resolve_public("example.com", 80)
    assert error.value.code == "private_url"


def test_mixed_public_private_dns_answers_rejected(monkeypatch):
    monkeypatch.setattr(
        public_web.socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
            for ip in ["93.184.216.34", "127.0.0.1"]
        ],
    )
    with pytest.raises(AppError):
        public_web.resolve_public("example.com", 80)


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"test body"):
        self.status = status
        self.headers = headers or {"Content-Type": "text/plain"}
        self.body = body

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size):
        return self.body[:size]


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_each_redirect_is_resolved_and_validated(monkeypatch):
    calls = []

    def resolve(host, port):
        calls.append(host)
        if host == "127.0.0.1":
            raise AppError("private_url", "禁止私网")
        return ["93.184.216.34"]

    connection = FakeConnection(FakeResponse(302, {"Location": "http://127.0.0.1/private"}))
    monkeypatch.setattr(public_web, "resolve_public", resolve)
    monkeypatch.setattr(public_web, "_connection", lambda *a: connection)
    with pytest.raises(AppError):
        public_web.fetch_public("https://example.com/")
    assert calls == ["example.com", "127.0.0.1"]
    assert connection.closed


def test_download_pins_validated_address_and_enforces_size(monkeypatch):
    connection = FakeConnection(FakeResponse(body=b"valid page"))
    monkeypatch.setattr(public_web, "resolve_public", lambda *a: ["93.184.216.34"])
    pinned = []

    def connect(parts, address):
        pinned.append((parts.hostname, address))
        return connection

    monkeypatch.setattr(public_web, "_connection", connect)
    assert public_web.fetch_public("https://example.com/正文")[0] == b"valid page"
    assert pinned == [("example.com", "93.184.216.34")]
    assert connection.requests[0][0][1] == "/%E6%AD%A3%E6%96%87"
    monkeypatch.setattr(public_web, "MAX_BYTES", 3)
    with pytest.raises(AppError) as error:
        public_web.fetch_public("https://example.com")
    assert error.value.code == "source_too_large"


def test_html_body_extraction():
    html = (
        "<html><body><article><h1>西安游记</h1><p>"
        + "西安碑林博物馆有丰富的碑刻藏品，可以安排半天参观。" * 15
        + "</p></article></body></html>"
    ).encode()
    segments = extract("html", html)
    assert any("碑林" in segment["text"] for segment in segments)
    assert all("paragraph" in segment for segment in segments)
