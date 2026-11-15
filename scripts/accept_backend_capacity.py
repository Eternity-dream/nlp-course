"""通过实际 HTTP 服务验收 100 份生成资料、大小与页数边界及中断恢复。"""

import argparse
import hashlib
import io
import platform
import sys
import time
from pathlib import Path

from acceptance_support import Server, poll, require, run_acceptance, save
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

MIB = 1024 * 1024


def chinese_pdf(marker, *, pages=8, padding=0):
    writer = PdfWriter()
    descendant = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("GB1"),
                    NameObject("/Supplement"): NumberObject(4),
                }
            ),
        }
    )
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type0"),
                NameObject("/BaseFont"): NameObject("/STSong-Light"),
                NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
                NameObject("/DescendantFonts"): ArrayObject([descendant]),
            }
        )
    )
    for index in range(pages):
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        content = ["BT /F1 12 Tf 20 750 Td"]
        for line in range(12):
            text = f"{marker} 第{index + 1}页第{line + 1}段：西安碑林博物馆历史文化参观资料。"
            content.append("<" + text.encode("utf-16-be").hex() + "> Tj 0 -20 Td")
        content.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(content).encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if padding:
        stream = DecodedStreamObject()
        stream.set_data(b"x" * padding)
        writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def long_text(marker, characters):
    line = f"{marker} 西安碑林博物馆参观背景资料，记录历史文化与慢节奏游览。"
    return (line * (characters // len(line) + 1))[:characters].encode()


def wait_all(server):
    last = None

    def read():
        nonlocal last
        status = server.get("/api/runtime")
        counts = status["imports"]
        if counts.get("ready", 0) // 10 != last:
            last = counts.get("ready", 0) // 10
            print(f"imports: {counts}", flush=True)
        return status

    return poll(read, lambda result: result["idle"], timeout=300)


def verify_sources(server, records):
    latencies, chunks = [], 0
    for record in records:
        source = server.source(record["id"])
        require(source["status"] == "ready", f"资料导入失败：{record['marker']} {source['error']}")
        chunks += len(source["chunks"])
        expected_key = "page" if record["kind"] == "pdf" else "paragraph"
        require(all(item[expected_key] is not None for item in source["chunks"]), "定位信息缺失")
        started = time.monotonic()
        hits = server.get("/api/knowledge/search", params={"q": record["marker"]})
        latencies.append(time.monotonic() - started)
        require(any(item["source_id"] == record["id"] for item in hits), "专属词未召回对应资料")
        original = server.request("GET", f"/api/knowledge/{record['id']}/original").content
        require(hashlib.sha256(original).hexdigest() == record["sha256"], "原资料摘要不一致")
    for query in ("西安", "碑林", "博物馆"):
        require(server.get("/api/knowledge/search", params={"q": query}), "中文短词无结果")
    latencies.sort()
    return {
        "verified_sources": len(records),
        "chunks": chunks,
        "query_p50_ms": round(latencies[len(latencies) // 2] * 1000, 3),
        "query_p95_ms": round(latencies[int(len(latencies) * 0.95)] * 1000, 3),
    }


def boundaries(server, report):
    report["boundaries"] = {}
    too_large = server.request(
        "POST",
        "/api/knowledge/files",
        expected=413,
        data={"title": "超大文件"},
        files={"file": ("large.txt", b"x" * (20 * MIB + 1))},
    )
    require(too_large.json()["error"]["code"] == "source_too_large", "20 MiB 上限错误")
    report["boundaries"]["20_mib_plus_one"] = "source_too_large"
    too_large = server.request(
        "POST", "/api/knowledge", expected=413, content=b"x" * (21 * MIB + 1)
    )
    require(too_large.json()["error"]["code"] == "request_too_large", "HTTP 大小上限错误")
    report["boundaries"]["21_mib_request_plus_one"] = "request_too_large"
    cases = [
        ("201.pdf", chinese_pdf("pages201", pages=201), "pdf_page_limit"),
        ("over.txt", long_text("overlimit", 2_000_001), "extracted_text_limit"),
    ]
    for filename, raw, code in cases:
        source = server.upload(filename, raw, "解析边界验收")
        source = server.source(source["id"])
        require(
            source["status"] == "failed" and source["error"]["code"] == code,
            f"边界未按预期失败：{filename}",
        )
        require(source["chunks"] == [], "失败资料留下可搜索片段")
        server.request("DELETE", "/api/knowledge/" + source["id"], expected=204)
        report["boundaries"][filename] = code


def recover(server, report):
    if sys.platform != "linux":
        report["interruption"] = {"status": "skipped", "reason": "需 Linux 进程组强制终止"}
        return
    interrupted = server.upload("interrupted.txt", long_text("killmarker", 1_900_000), "中断资料")
    poll(
        lambda: server.get("/api/knowledge/" + interrupted["id"]),
        lambda source: source["status"] == "processing",
        timeout=20,
    )
    queued = server.upload("queued.txt", "queuedmarker 西安中断后恢复导入。".encode(), "排队资料")
    require(server.get("/api/knowledge/" + queued["id"])["status"] == "pending", "未形成排队")
    server.stop(kill=True)
    server.start()
    failed = server.source(interrupted["id"])
    ready = server.source(queued["id"])
    require(
        failed["status"] == "failed" and failed["error"]["code"] == "import_interrupted",
        "中断资料未记录明确失败",
    )
    require(failed["chunks"] == [], "中断资料留下索引")
    require(ready["status"] == "ready", "排队资料重启后未完成")
    require(not server.get("/api/knowledge/search", params={"q": "killmarker"}), "中断正文可搜索")
    require(server.get("/api/knowledge/search", params={"q": "queuedmarker"}), "排队正文未恢复")
    report["interruption"] = {
        "status": "passed",
        "processing": "import_interrupted",
        "pending": "ready",
        "partial_chunks": 0,
    }


def scenario(args, report):
    report["environment"] = {"python": platform.python_version(), "platform": platform.platform()}
    report["sample_kind"] = "generated_chinese_text_and_pdf"
    server = Server(args.output_dir)
    records, total_bytes = [], 0
    try:
        server.start()
        started = time.monotonic()
        for index in range(100):
            marker = f"capdoc{index:03}"
            if index < 50:
                raw = long_text(marker, 1_900_000 if index == 49 else 12_000)
                kind, name = "text", marker + ".txt"
            else:
                pages = 200 if index == 98 else 8
                raw = chinese_pdf(marker, pages=pages)
                if index == 99:
                    raw = chinese_pdf(marker, pages=8, padding=20 * MIB - len(raw) - 1024)
                    require(len(raw) <= 20 * MIB, "20 MiB PDF 样本生成超限")
                    raw = raw.ljust(20 * MIB, b" ")
                kind, name = "pdf", marker + ".pdf"
            source = server.upload(name, raw, f"容量验收 {marker}")
            total_bytes += len(raw)
            records.append(
                {
                    "id": source["id"],
                    "marker": marker,
                    "kind": kind,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        full = server.request(
            "POST",
            "/api/knowledge",
            expected=409,
            json={"title": "第101份", "text": "capacity101 超出数量"},
        )
        require(full.json()["error"]["code"] == "source_count_limit", "第101份未被拒绝")
        status = wait_all(server)
        require(status["imports"] == {"ready": 100}, f"100份资料未全部完成：{status}")
        report["import_seconds"] = round(time.monotonic() - started, 3)
        report["import"] = {
            "text": 50,
            "pdf": 50,
            "total_bytes": total_bytes,
            "ready": 100,
            "file_count_limit": "source_count_limit",
            "largest_file_bytes": max(item["bytes"] for item in records),
            "long_text_characters": 1_900_000,
            "largest_pdf_pages": 200,
        }
        report["search"] = verify_sources(server, records)
        save(args.output_dir / "samples.json", records)
        # 腾出两个名额，验证失败项清理和真正的进程中断。
        for record in records[:2]:
            server.request("DELETE", "/api/knowledge/" + record["id"], expected=204)
            require(
                not server.get("/api/knowledge/search", params={"q": record["marker"]}),
                "删除后全文索引未清理",
            )
            require(not (server.data / "sources" / record["id"]).exists(), "删除后原资料未清理")
        boundaries(server, report)
        recover(server, report)
        require(server.get("/api/runtime")["idle"], "验收结束时任务未空闲")
        server.stop()
        report["verify"] = server.maintenance("verify")
        archive = args.output_dir / "backup.zip"
        report["backup"] = server.maintenance("backup", "--output", archive)
        target = args.output_dir / "restored"
        report["restore"] = server.maintenance("restore", "--archive", archive, data=target)
        server.start(target)
        report["restored_search"] = verify_sources(server, records[2:])
        require(report["verify"]["counts"] == report["restore"]["counts"], "恢复前后数量不一致")
        report["restored_http_verified"] = True
    finally:
        server.close()
        report["sampled_peak_resources"] = {
            **server.peaks,
            "sample_interval_seconds": 0.05,
            "method": "Linux /proc RSS; process group sum includes shared pages per process",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="新的验收输出目录")
    raise SystemExit(run_acceptance(parser.parse_args(), scenario))
