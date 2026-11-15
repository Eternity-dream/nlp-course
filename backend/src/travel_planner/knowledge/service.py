import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from travel_planner.domain.errors import AppError
from travel_planner.knowledge.public_web import MAX_BYTES
from travel_planner.knowledge.text import split_chunks, tokens
from travel_planner.storage.database import Database
from travel_planner.storage.repository import new_id, now, require

logger = logging.getLogger(__name__)
MAX_SOURCES = 100
PARSE_TIMEOUT = 30


def source_dict(row) -> dict:
    return {
        key: (json.loads(row[key]) if key == "error" and row[key] else row[key])
        for key in (
            "id",
            "kind",
            "title",
            "url",
            "status",
            "sha256",
            "error",
            "created_at",
            "updated_at",
        )
    }


class KnowledgeService:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.root = data_dir / "sources"
        self.root.mkdir(parents=True, exist_ok=True)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="knowledge-import", daemon=True)

    def start(self):
        error = AppError(
            "import_interrupted", "上次导入被中断，请删除后重新导入", retryable=True
        ).payload()
        with self.db.connect(write=True) as connection:
            connection.execute(
                "UPDATE knowledge_sources SET status='failed', error=?, updated_at=? "
                "WHERE status='processing'",
                (json.dumps(error, ensure_ascii=False), now()),
            )
            references = {
                row[0] for row in connection.execute("SELECT file_ref FROM knowledge_sources")
            }
        for directory in self.root.iterdir():
            if directory.is_dir() and f"{directory.name}/raw" not in references:
                shutil.rmtree(directory)
        self.cleanup_files()
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join()

    def create(self, kind: str, title: str, raw: bytes, url: str | None = None) -> dict:
        if len(raw) > MAX_BYTES:
            raise AppError("source_too_large", "单份资料最多 20 MB", 413)
        if not raw:
            raise AppError("empty_source", "资料内容不能为空", 422)
        digest = hashlib.sha256(raw).hexdigest()
        input_kind = "url" if kind == "bookmark" else kind
        input_digest = hashlib.sha256(input_kind.encode() + b"\0" + raw).hexdigest()
        source_id, timestamp = new_id(), now()
        file_ref = f"{source_id}/raw"
        with self.db.connect(write=True) as connection:
            duplicate = connection.execute(
                "SELECT id FROM knowledge_sources WHERE input_sha256=?", (input_digest,)
            ).fetchone()
            if duplicate:
                raise AppError(
                    "duplicate_source",
                    "相同资料已导入，请查看已有资料；失败项可删除后重试",
                    409,
                    object_id=duplicate["id"],
                )
            count = connection.execute("SELECT count(*) FROM knowledge_sources").fetchone()[0]
            if count >= MAX_SOURCES:
                raise AppError("source_count_limit", "资料数量上限为 100 份，请删除后再导入", 409)
            directory = self.root / source_id
            directory.mkdir()
            try:
                (directory / "raw").write_bytes(raw)
                connection.execute(
                    """INSERT INTO knowledge_sources
                    (id,kind,title,url,file_ref,input_sha256,sha256,status,error,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,'pending',NULL,?,?)""",
                    (
                        source_id,
                        kind,
                        title,
                        url,
                        file_ref,
                        input_digest,
                        digest,
                        timestamp,
                        timestamp,
                    ),
                )
            except BaseException:
                shutil.rmtree(directory)
                raise
        self.wake_event.set()
        return {
            "id": source_id,
            "kind": kind,
            "title": title,
            "url": url,
            "status": "pending",
            "sha256": digest,
            "error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

    def list_sources(self, limit: int, offset: int) -> list[dict]:
        with self.db.connect() as connection:
            return [
                source_dict(row)
                for row in connection.execute(
                    "SELECT * FROM knowledge_sources ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def get(self, source_id: str) -> dict:
        with self.db.connect() as connection:
            row = require(
                connection.execute(
                    "SELECT * FROM knowledge_sources WHERE id=?", (source_id,)
                ).fetchone(),
                source_id,
            )
            chunks = (
                [
                    dict(chunk)
                    for chunk in connection.execute(
                        "SELECT * FROM knowledge_chunks WHERE source_id=? ORDER BY ordinal",
                        (source_id,),
                    )
                ]
                if row["status"] == "ready"
                else []
            )
            return {**source_dict(row), "chunks": chunks}

    def delete(self, source_id: str):
        with self.db.connect(write=True) as connection:
            row = require(
                connection.execute(
                    "SELECT file_ref FROM knowledge_sources WHERE id=?", (source_id,)
                ).fetchone(),
                source_id,
            )
            connection.execute(
                "INSERT OR IGNORE INTO file_cleanup VALUES (?, ?)", (row["file_ref"], now())
            )
            connection.execute("DELETE FROM knowledge_sources WHERE id=?", (source_id,))
        self.cleanup_files()

    def original(self, source_id: str) -> tuple[Path, str]:
        with self.db.connect() as connection:
            row = require(
                connection.execute(
                    "SELECT file_ref,kind,status FROM knowledge_sources WHERE id=?", (source_id,)
                ).fetchone(),
                source_id,
            )
            path = self.root / row["file_ref"]
            if not path.is_file():
                raise AppError(
                    "original_missing", "原始资料文件缺失，请重新导入", 410, object_id=source_id
                )
            suffix = (
                "pdf"
                if row["kind"] == "pdf"
                else (
                    "html"
                    if row["kind"] in {"url", "bookmark"} and row["status"] == "ready"
                    else "txt"
                )
            )
            return path, f"{source_id}.{suffix}"

    def cleanup_files(self):
        with self.db.connect() as connection:
            rows = connection.execute("SELECT file_ref FROM file_cleanup").fetchall()
        for row in rows:
            path = self.root / row["file_ref"]
            try:
                path.unlink(missing_ok=True)
                if path.parent.exists():
                    path.parent.rmdir()
                with self.db.connect(write=True) as connection:
                    connection.execute(
                        "DELETE FROM file_cleanup WHERE file_ref=?", (row["file_ref"],)
                    )
            except OSError:
                logger.warning("knowledge_file_cleanup_pending")

    def search(self, query: str, limit: int = 8) -> list[dict]:
        words = tokens(query)[:64]
        if not words:
            return []
        expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, s.title, s.url, bm25(knowledge_fts) AS score
                FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.chunk_id
                JOIN knowledge_sources s ON s.id=c.source_id
                WHERE knowledge_fts MATCH ? AND s.status='ready'
                ORDER BY score, c.id LIMIT 64""",
                (expression,),
            ).fetchall()
            hits, seen = [], set()
            for row in rows:
                digest = hashlib.sha256(row["text"].encode()).hexdigest()
                if digest not in seen:
                    seen.add(digest)
                    hits.append(dict(row))
                if len(hits) >= limit:
                    break
            return hits

    def live_chunks(self, chunk_ids: list[str]) -> list[dict]:
        """模型调用前重新查询，返回当前仍可使用的片段。"""
        with self.db.connect() as connection:
            result = []
            for chunk_id in dict.fromkeys(chunk_ids):
                row = connection.execute(
                    """SELECT c.* FROM knowledge_chunks c JOIN knowledge_sources s
                    ON s.id=c.source_id WHERE c.id=? AND s.status='ready'""",
                    (chunk_id,),
                ).fetchone()
                if row:
                    result.append(dict(row))
            return result

    def _claim(self):
        with self.db.connect(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_sources WHERE status='pending' "
                "ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE knowledge_sources SET status='processing',updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
                return dict(row)
        return None

    def _run(self):
        while not self.stop_event.is_set():
            try:
                row = self._claim()
                if row:
                    self._process(row)
                    continue
                self.cleanup_files()
            except Exception:
                logger.error("knowledge_worker_error")
            self.wake_event.wait(1)
            self.wake_event.clear()

    def _process(self, source: dict):
        try:
            with tempfile.TemporaryDirectory(prefix="travel-parse-") as directory:
                output, downloaded = Path(directory) / "result.json", Path(directory) / "raw"
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "travel_planner.knowledge.parse",
                        source["kind"],
                        str((self.root / source["file_ref"]).resolve()),
                        str(output),
                        str(downloaded),
                    ],
                    timeout=PARSE_TIMEOUT,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                result = json.loads(output.read_text(encoding="utf-8"))
                if "error" in result:
                    self._fail(source["id"], result["error"])
                    return
                chunks = split_chunks(result["segments"])
                indexed = [
                    (new_id(), index, chunk, " ".join(tokens(chunk["text"])))
                    for index, chunk in enumerate(chunks)
                ]
                with self.db.connect(write=True) as connection:
                    row = connection.execute(
                        "SELECT status FROM knowledge_sources WHERE id=?", (source["id"],)
                    ).fetchone()
                    if not row or row["status"] != "processing":
                        return
                    if downloaded.exists():
                        # 文件与数据库在同一持久化目录；重启可识别中断的导入。
                        shutil.copyfile(downloaded, self.root / source["file_ref"])
                    for chunk_id, ordinal, chunk, index_text in indexed:
                        connection.execute(
                            "INSERT INTO knowledge_chunks VALUES (?,?,?,?,?,?,?)",
                            (
                                chunk_id,
                                source["id"],
                                ordinal,
                                chunk["text"],
                                chunk["page"],
                                chunk["paragraph"],
                                chunk["offset"],
                            ),
                        )
                        connection.execute(
                            "INSERT INTO knowledge_fts VALUES (?,?)", (chunk_id, index_text)
                        )
                    connection.execute(
                        "UPDATE knowledge_sources SET status='ready',sha256=?,updated_at=? "
                        "WHERE id=?",
                        (result["sha256"], now(), source["id"]),
                    )
        except subprocess.TimeoutExpired:
            self._fail(
                source["id"],
                AppError(
                    "parse_timeout", "资料解析超时，请拆分或粘贴正文", retryable=True
                ).payload(),
            )
        except Exception:
            self._fail(
                source["id"],
                AppError(
                    "parse_failed", "资料解析失败，请拆分或提供文字版本", retryable=True
                ).payload(),
            )

    def _fail(self, source_id: str, error: dict):
        with self.db.connect(write=True) as connection:
            connection.execute(
                "UPDATE knowledge_sources SET status='failed',error=?,updated_at=? WHERE id=?",
                (
                    json.dumps({**error, "object_id": source_id}, ensure_ascii=False),
                    now(),
                    source_id,
                ),
            )
