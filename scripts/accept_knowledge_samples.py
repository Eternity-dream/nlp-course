"""用已排版的中文 PDF、纯图扫描件、长文和公开链接验收资料导入与清理。"""

import argparse
import hashlib
import sqlite3
import time
from pathlib import Path

from accept_agent_live import live_server
from acceptance_support import require, run_acceptance, save
from pypdf import PdfReader


def digest(content):
    return hashlib.sha256(content).hexdigest()


def check_private_url(server):
    source = server.request(
        "POST",
        "/api/knowledge",
        expected=202,
        json={"kind": "url", "url": "http://127.0.0.1/", "title": "本机地址拒绝验收"},
    ).json()
    result = server.source(source["id"])
    require(
        result["status"] == "failed"
        and result["error"]["code"] == "private_url"
        and not result["chunks"],
        "本机地址未被拒绝或留下索引",
    )
    return {"status": result["status"], "error": result["error"], "chunks": len(result["chunks"])}


def scenario(args, report):
    server = live_server(args.output_dir)
    files = {"pdf": args.pdf, "scan": args.scan, "text": args.text}
    scan = PdfReader(args.scan)
    require(
        all(not p.extract_text().strip() and p["/Resources"].get("/XObject") for p in scan.pages),
        "扫描样本须为纯图片页",
    )
    report["samples"] = {}
    try:
        server.start()
        report["private_url"] = check_private_url(server)
        for kind in ("pdf", "scan", "text", "url", "bookmark"):
            started = time.monotonic()
            if kind in files:
                path = files[kind]
                raw = path.read_bytes()
                title = path.stem
                item = server.upload(path.name, raw, title)
            else:
                url = args.url if kind == "url" else args.bookmark
                raw = url.encode()
                item = server.request(
                    "POST",
                    "/api/knowledge",
                    expected=202,
                    json={
                        "kind": kind,
                        "url": url,
                        "title": "西安场馆导览" if kind == "url" else "收藏的博物馆",
                    },
                ).json()
            source = server.source(item["id"])
            chunks = source["chunks"]
            original = server.request("GET", f"/api/knowledge/{item['id']}/original").content
            record = report["samples"][kind] = {
                "id": item["id"],
                "status": source["status"],
                "error": source["error"],
                "input_bytes": len(raw),
                "original_bytes": len(original),
                "input_sha256": digest(raw),
                "download_sha256": digest(original),
                "stored_sha256": source["sha256"],
                "chunks": len(chunks),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            require(digest(original) == source["sha256"], "原文下载与资料摘要不符")
            if kind in files:
                require(original == raw, "上传与下载的原文不一致")
            if kind == "pdf":
                pages = len(PdfReader(args.pdf).pages)
                record["page_count"] = pages
                record["indexed_pages"] = sorted({c["page"] for c in chunks})
                require(record["indexed_pages"] == list(range(1, pages + 1)), "PDF 索引页码不完整")
            elif kind == "scan":
                require(
                    source["status"] == "failed"
                    and not chunks
                    and source["error"]["code"] == "pdf_no_text",
                    "扫描件状态不符",
                )
                require("文字版本" in source["error"]["message"], "扫描件缺少处理提示")
            elif kind == "text":
                record["characters"] = len(raw.decode())
                require(
                    source["status"] == "ready" and chunks and all(c["paragraph"] for c in chunks),
                    "长文段落定位失败",
                )
            else:
                record["url"] = source["url"]
                require(
                    (source["status"] == "ready" and bool(chunks))
                    or (
                        source["status"] == "failed"
                        and bool(source["error"]["message"])
                        and not chunks
                    ),
                    "链接导入缺少可读结果",
                )
            save(args.output_dir / (kind + ".json"), source)
        probes = {}
        for term, kind, page in [
            (args.pdf_term, "pdf", args.pdf_page),
            ("西安", "text", None),
            ("碑林", "text", None),
        ]:
            hits = server.get("/api/knowledge/search", params={"q": term})
            located = [
                h
                for h in hits
                if h["source_id"] == report["samples"][kind]["id"] and term in h["text"]
            ]
            probes[term] = [
                {"id": h["id"], "page": h["page"], "paragraph": h["paragraph"]} for h in located
            ]
            require(
                located and (page is None or any(h["page"] == page for h in located)),
                f"短词未定位到原资料：{term}",
            )
        report["hits"] = probes
        item_id = report["samples"]["pdf"]["id"]
        detail = server.get("/api/knowledge/" + item_id)
        ids = [c["id"] for c in detail["chunks"]]

        def counts():
            with sqlite3.connect(
                (server.data / "app.sqlite3").as_uri() + "?mode=ro", uri=True
            ) as db:
                placeholders = ",".join("?" for _ in ids)
                return {
                    "chunks": db.execute(
                        "SELECT count(*) FROM knowledge_chunks WHERE source_id=?", (item_id,)
                    ).fetchone()[0],
                    "fts": db.execute(
                        f"SELECT count(*) FROM knowledge_fts WHERE chunk_id IN ({placeholders})",
                        ids,
                    ).fetchone()[0],
                }

        report["delete_before"] = counts()
        require(report["delete_before"]["fts"] == len(ids) > 0, "删除前 FTS 记录缺失")
        server.request("DELETE", "/api/knowledge/" + item_id, expected=204)
        after = report["delete_after"] = counts()
        after["hits"] = len(server.get("/api/knowledge/search", params={"q": args.pdf_term}))
        after["original_directory_exists"] = (server.data / "sources" / item_id).exists()
        response = server.request("GET", f"/api/knowledge/{item_id}/original", expected=404)
        require(not any(after.values()), "删除后文件或索引残留")
        after["original_http_status"] = response.status_code
    finally:
        server.close()
        report["peak_bytes"] = server.peaks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    for name in ("pdf", "scan", "text"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--pdf-term", required=True, help="PDF 中的专属短词")
    parser.add_argument("--pdf-page", required=True, type=int, help="专属词所在的实际页码")
    parser.add_argument("--url", default="https://www.sxhm.com/guide.html")
    parser.add_argument("--bookmark", default="https://www.bmy.com.cn/guide.html")
    raise SystemExit(run_acceptance(parser.parse_args(), scenario))
