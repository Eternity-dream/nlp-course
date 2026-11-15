import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from travel_planner.domain.models import PreferencePut, TripFields
from travel_planner.maintenance.errors import DataError
from travel_planner.planner.models import Itinerary, ItineraryEdit, PlanDraft, trip_dates
from travel_planner.storage.database import SCHEMA_VERSION

DATABASE_NAME = "app.sqlite3"
RESTORE_MARKER = ".restore-in-progress"
CONTROL_FILES = {".instance.lock", "app.sqlite3-wal", "app.sqlite3-shm"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def managed_path(name: str) -> bool:
    if name == DATABASE_NAME:
        return True
    parts = PurePosixPath(name).parts
    return (
        len(parts) == 3
        and parts[0] == "sources"
        and parts[2] == "raw"
        and re.fullmatch(r"[0-9a-f]{32}", parts[1]) is not None
        and str(PurePosixPath(name)) == name
        and "\\" not in name
    )


def inventory(root: Path, *, restoring: bool = False) -> dict[str, Path]:
    if not root.is_dir() or root.is_symlink():
        raise DataError("invalid_data_dir", "数据目录不存在或不是普通目录")
    if (root / RESTORE_MARKER).exists() and not restoring:
        raise DataError("restore_incomplete", "上次恢复未完成，请向另一个空目录或空卷重新恢复")
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DataError("invalid_data_path", "数据目录中存在符号链接，请检查原资料目录")
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            if name != "sources" and not re.fullmatch(r"sources/[0-9a-f]{32}", name):
                raise DataError("unexpected_data_file", "数据目录包含无法识别的目录")
            continue
        if not path.is_file():
            raise DataError("invalid_data_path", "数据目录包含非常规文件")
        if name in CONTROL_FILES or (restoring and name == RESTORE_MARKER):
            continue
        if not managed_path(name):
            raise DataError(
                "unexpected_data_file", "数据目录包含应用数据之外的文件，请将配置与备份放在目录外"
            )
        files[name] = path
    if DATABASE_NAME not in files:
        raise DataError("database_missing", "数据目录缺少 app.sqlite3")
    return files


def snapshot_database(source: Path, target: Path):
    """将停止的数据库（含 WAL 已提交内容）汇总为单文件副本。"""
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(target)) as snapshot:
            original.backup(snapshot)
            snapshot.execute("PRAGMA journal_mode=DELETE")


def validate_database(connection: sqlite3.Connection) -> dict:
    connection.row_factory = sqlite3.Row
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if not 1 <= version <= SCHEMA_VERSION:
        raise DataError(
            "unsupported_schema", "数据库版本不受当前应用支持，请使用对应版本的应用与备份"
        )
    if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise DataError("database_corrupt", "SQLite 完整性检查失败")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise DataError("broken_references", "数据库存在缺失的关联记录")
    connection.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES ('integrity-check')")
    chunks = connection.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
    indexed = connection.execute("SELECT count(*) FROM knowledge_fts").fetchone()[0]
    if (
        chunks != indexed
        or connection.execute("""SELECT c.id FROM knowledge_chunks c
        LEFT JOIN knowledge_fts f ON f.chunk_id=c.id
        GROUP BY c.id HAVING count(f.chunk_id)!=1""").fetchone()
    ):
        raise DataError("index_inconsistent", "全文索引与资料片段不一致")
    if (
        connection.execute("""SELECT s.id FROM knowledge_sources s LEFT JOIN knowledge_chunks c
        ON c.source_id=s.id GROUP BY s.id HAVING
        (s.status='ready' AND count(c.id)=0) OR (s.status!='ready' AND count(c.id)>0)""").fetchone()
    ):
        raise DataError("source_inconsistent", "资料状态与可检索片段不一致")
    requests = {}
    for row in connection.execute("SELECT * FROM trip_revisions"):
        requests[(row["trip_id"], row["revision"])] = TripFields.model_validate_json(row["payload"])
    for row in connection.execute("SELECT * FROM trips"):
        if (row["id"], row["revision"]) not in requests:
            raise DataError("broken_references", "当前行程要求版本不存在")
    preference = connection.execute("SELECT * FROM preferences WHERE id=1").fetchone()
    if preference is None:
        raise DataError("preferences_missing", "偏好档案记录缺失")
    PreferencePut(
        expected_revision=preference["revision"], preferences=json.loads(preference["payload"])
    )
    counts = {
        "trips": connection.execute("SELECT count(*) FROM trips").fetchone()[0],
        "request_revisions": len(requests),
        "knowledge_sources": connection.execute(
            "SELECT count(*) FROM knowledge_sources"
        ).fetchone()[0],
        "chunks": chunks,
        "jobs": 0,
        "itinerary_revisions": 0,
        "current_itineraries": 0,
    }
    busy = connection.execute(
        "SELECT count(*) FROM knowledge_sources WHERE status IN ('pending','processing')"
    ).fetchone()[0]
    busy += connection.execute("SELECT count(*) FROM file_cleanup").fetchone()[0]
    if version >= 3:
        jobs = {row["id"]: row for row in connection.execute("SELECT * FROM plan_jobs")}
        counts["jobs"] = len(jobs)
        busy += sum(row["status"] in {"running", "queued"} for row in jobs.values())
        for row in jobs.values():
            if (row["trip_id"], row["submission_revision"]) not in requests:
                raise DataError("broken_references", "任务的提交要求版本不存在")
            questions = json.loads(row["questions"])
            error = json.loads(row["error"]) if row["error"] else None
            if (
                not isinstance(questions, list)
                or any(not isinstance(q, str) for q in questions)
                or (error is not None and not isinstance(error, dict))
            ):
                raise DataError("invalid_record", "任务状态内容无法读取")
            if version >= 4 and row["kind"] == "edit":
                edit = ItineraryEdit.model_validate_json(row["edit_payload"])
                if (
                    edit.expected_revision != row["base_revision"]
                    or edit.expected_request_revision != row["request_revision"]
                ):
                    raise DataError("broken_references", "编辑任务的基础版本与操作不一致")
                if not connection.execute(
                    "SELECT 1 FROM itinerary_revisions WHERE trip_id=? AND revision=?",
                    (row["trip_id"], row["base_revision"]),
                ).fetchone():
                    raise DataError("broken_references", "编辑任务的基础草案不存在")
            if (
                row["status"] == "succeeded"
                and not connection.execute(
                    "SELECT 1 FROM itinerary_revisions WHERE trip_id=? AND revision=? AND job_id=?",
                    (row["trip_id"], row["result_revision"], row["id"]),
                ).fetchone()
            ):
                raise DataError("broken_references", "已完成任务的行程结果不存在")
        for row in connection.execute("SELECT * FROM itinerary_revisions"):
            itinerary = Itinerary.model_validate_json(row["payload"])
            request = requests[(row["trip_id"], row["request_revision"])].model_dump(mode="json")
            job = jobs.get(row["job_id"])
            if (
                not job
                or (
                    itinerary.trip_id,
                    itinerary.id,
                    itinerary.revision,
                    itinerary.request_revision,
                    itinerary.job_id,
                )
                != (
                    row["trip_id"],
                    row["trip_id"],
                    row["revision"],
                    row["request_revision"],
                    row["job_id"],
                )
                or job["status"] != "succeeded"
                or job["result_revision"] != row["revision"]
                or (job["trip_id"], job["request_revision"])
                != (row["trip_id"], row["request_revision"])
            ):
                raise DataError("broken_references", "行程结果与任务版本不一致")
            PlanDraft.model_validate(
                {
                    "days": [day.model_dump() for day in itinerary.days],
                    "assumptions": [],
                    "constraint_conflicts": itinerary.constraint_conflicts,
                },
                context={
                    "dates": trip_dates(request),
                    "place_ids": {place.id for place in itinerary.places},
                    "facts": {fact.id: fact for fact in itinerary.evidence},
                    "request": request,
                    "allow_empty_days": True,
                },
            )
            counts["itinerary_revisions"] += 1
        counts["current_itineraries"] = connection.execute(
            "SELECT count(*) FROM current_itineraries"
        ).fetchone()[0]
        if connection.execute("""SELECT r.trip_id FROM itinerary_revisions r
            LEFT JOIN current_itineraries c ON c.trip_id=r.trip_id GROUP BY r.trip_id
            HAVING c.revision IS NULL OR c.revision!=max(r.revision)""").fetchone():
            raise DataError("broken_references", "当前草案引用与已保存的最新结果不一致")
    return {"schema_version": version, "counts": counts, "idle": not busy}


def inspect_data(root: Path, *, restoring: bool = False) -> dict:
    files = inventory(root, restoring=restoring)
    try:
        with tempfile.TemporaryDirectory(prefix="travel-verify-") as directory:
            snapshot = Path(directory) / DATABASE_NAME
            snapshot_database(files[DATABASE_NAME], snapshot)
            with closing(sqlite3.connect(snapshot)) as connection:
                report = validate_database(connection)
                refs = set()
                for row in connection.execute("SELECT id,file_ref,sha256 FROM knowledge_sources"):
                    name = "sources/" + row["file_ref"]
                    if not managed_path(name) or row["file_ref"] != row["id"] + "/raw":
                        raise DataError("invalid_data_path", "资料文件引用不是应用生成的本地路径")
                    path = files.get(name)
                    if path is None:
                        raise DataError("source_file_missing", "资料元数据引用的原始文件缺失")
                    if sha256_file(path) != row["sha256"]:
                        raise DataError("source_hash_mismatch", "原始资料与保存的内容摘要不一致")
                    refs.add(name)
                report["orphan_raw_files"] = len(set(files) - refs - {DATABASE_NAME})
                report["raw_files"] = len(files) - 1
                return report
    except (sqlite3.DatabaseError, ValidationError, ValueError, TypeError, KeyError) as exc:
        raise DataError(
            "invalid_database", "数据库、索引或业务记录校验失败，请使用完整备份检查数据"
        ) from exc
