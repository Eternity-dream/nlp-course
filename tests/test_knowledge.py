import io
import json
import subprocess
import threading

from conftest import wait_source
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from travel_planner.knowledge import service as service_module
from travel_planner.main import create_app


def import_text(
    client, text="西安碑林博物馆适合慢慢参观。\n杭州西湖可以步行游览。", title="旅行笔记"
):
    response = client.post("/api/knowledge", json={"title": title, "text": text})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def make_pdf(pages=1, text="Xi'an travel guide"):
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=600, height=800)
        if index == 0 and text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 750 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_chinese_short_words_local_search_and_delete(client, settings):
    source_id = import_text(client)
    source = wait_source(client, source_id)
    assert source["status"] == "ready", source
    for query in ["西安", "碑林", "碑林博物馆", '西安" OR *']:
        hits = client.get("/api/knowledge/search", params={"q": query}).json()
        assert hits and hits[0]["source_id"] == source_id
        assert hits[0]["paragraph"] == 1
        assert hits[0]["offset"] == 0
    ids = [chunk["id"] for chunk in source["chunks"]]
    assert client.app.state.knowledge.live_chunks(ids)
    assert (settings.data_dir / "sources" / source_id / "raw").exists()
    original = client.get(f"/api/knowledge/{source_id}/original")
    assert original.status_code == 200
    assert "碑林" in original.text
    assert original.headers["content-type"] == "application/octet-stream"
    assert original.headers["content-disposition"].startswith("attachment;")
    assert client.delete(f"/api/knowledge/{source_id}").status_code == 204
    assert client.get(f"/api/knowledge/{source_id}").status_code == 404
    assert client.get(f"/api/knowledge/{source_id}/original").status_code == 404
    assert client.get("/api/knowledge/search", params={"q": "碑林"}).json() == []
    assert client.app.state.knowledge.live_chunks(ids) == []
    assert not (settings.data_dir / "sources" / source_id).exists()
    with client.app.state.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0] == 0


def test_duplicate_and_failed_sources_not_searchable(client):
    source_id = import_text(client, "西安碑林")
    duplicate = client.post("/api/knowledge", json={"title": "副本", "text": "西安碑林"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["object_id"] == source_id
    response = client.post(
        "/api/knowledge/files",
        data={"title": "坏文件"},
        files={
            "file": ("bad.txt", b"\xff\xfe", "text/plain"),
        },
    )
    assert response.status_code == 202
    failed = wait_source(client, response.json()["id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "text_encoding"
    assert failed["chunks"] == []


def test_pdf_text_and_page_locations(client):
    response = client.post(
        "/api/knowledge/files",
        data={"title": "PDF"},
        files={
            "file": ("guide.pdf", make_pdf(), "application/pdf"),
        },
    )
    assert response.status_code == 202
    source = wait_source(client, response.json()["id"])
    assert source["status"] == "ready", source
    assert source["chunks"][0]["page"] == 1
    assert "travel guide" in source["chunks"][0]["text"]


def test_scanned_and_oversize_pdf_have_specific_errors(client):
    for pages, text, code in [(1, "", "pdf_no_text"), (201, "", "pdf_page_limit")]:
        response = client.post(
            "/api/knowledge/files",
            data={"title": "PDF"},
            files={
                "file": ("guide.pdf", make_pdf(pages, text), "application/pdf"),
            },
        )
        source = wait_source(client, response.json()["id"])
        assert source["status"] == "failed", source
        assert source["error"]["code"] == code
        assert source["chunks"] == []


def test_file_size_and_count_limits(client, monkeypatch):
    monkeypatch.setattr(service_module, "MAX_SOURCES", 1)
    import_text(client)
    too_many = client.post("/api/knowledge", json={"title": "更多", "text": "另一个资料"})
    assert too_many.status_code == 409
    assert too_many.json()["error"]["code"] == "source_count_limit"
    too_large = client.post(
        "/api/knowledge/files",
        data={"title": "过大"},
        files={
            "file": ("big.txt", b"x" * (20 * 1024 * 1024 + 1)),
        },
    )
    assert too_large.status_code == 413


def test_knowledge_persists_and_installations_are_isolated(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        source_id = import_text(client)
        assert wait_source(client, source_id)["status"] == "ready"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/knowledge/search", params={"q": "碑林"}).json()
    other = settings.model_copy(update={"data_dir": tmp_path / "other"})
    with TestClient(create_app(other)) as client:
        assert client.get("/api/knowledge/search", params={"q": "碑林"}).json() == []
        assert client.get("/api/knowledge").json() == []


def test_deleting_while_parser_runs_cannot_restore_source(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def fake_run(args, **kwargs):
        entered.set()
        assert release.wait(5)
        from pathlib import Path

        Path(args[-2]).write_text(json.dumps({"sha256": "test", "segments": [{"text": "碑林"}]}))
        Path(args[-1]).write_bytes(b"web content")

    monkeypatch.setattr(service_module.subprocess, "run", fake_run)
    source_id = import_text(client)
    assert entered.wait(5)
    assert client.delete(f"/api/knowledge/{source_id}").status_code == 204
    release.set()
    client.app.state.knowledge.close()
    assert client.get("/api/knowledge/search", params={"q": "碑林"}).json() == []
    assert not (client.app.state.knowledge.root / source_id).exists()


def test_timeout_does_not_leave_processing_source(client, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("parse", 30)

    monkeypatch.setattr(service_module.subprocess, "run", timeout)
    source_id = import_text(client)
    source = wait_source(client, source_id)
    assert source["status"] == "failed"
    assert source["error"]["code"] == "parse_timeout"


def test_private_urls_fail_without_becoming_searchable(client):
    for kind in ["url", "bookmark"]:
        response = client.post(
            "/api/knowledge",
            json={
                "kind": kind,
                "title": kind,
                "url": "http://127.0.0.1/" + kind,
            },
        )
        assert response.status_code == 202
        source = wait_source(client, response.json()["id"])
        assert source["status"] == "failed", source
        assert source["error"]["code"] == "private_url"
