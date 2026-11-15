import json

from travel_planner.domain.errors import AppError
from travel_planner.domain.models import TripFields
from travel_planner.planner.models import Itinerary, ItineraryEdit, JobSubmit
from travel_planner.planner.operations import apply_operations, check_saved_scope
from travel_planner.storage.database import Database
from travel_planner.storage.repository import new_id, now, require


class JobStore:
    def __init__(self, db: Database):
        self.db = db

    def _job(self, connection, job_id: str) -> dict:
        row = require(
            connection.execute("SELECT * FROM plan_jobs WHERE id=?", (job_id,)).fetchone(), job_id
        )
        result = {
            key: row[key]
            for key in row.keys()
            if key not in {"submission_key", "submission_revision", "edit_payload"}
        }
        result["questions"] = json.loads(row["questions"])
        result["error"] = json.loads(row["error"]) if row["error"] else None
        result["events"] = [
            dict(event)
            for event in connection.execute(
                "SELECT stage,status,recorded_at FROM job_events WHERE job_id=? ORDER BY sequence",
                (job_id,),
            )
        ]
        return result

    def get(self, job_id: str) -> dict:
        with self.db.connect() as connection:
            return self._job(connection, job_id)

    def list_jobs(self, trip_id: str) -> list[dict]:
        with self.db.connect() as connection:
            require(
                connection.execute("SELECT id FROM trips WHERE id=?", (trip_id,)).fetchone(),
                trip_id,
            )
            return [
                self._job(connection, row[0])
                for row in connection.execute(
                    "SELECT id FROM plan_jobs WHERE trip_id=? "
                    "ORDER BY created_at DESC,rowid DESC LIMIT 50",
                    (trip_id,),
                ).fetchall()
            ]

    def submit(
        self, trip_id: str, body: JobSubmit, *, retry_of: str | None = None, configured: bool = True
    ) -> dict:
        with self.db.connect(write=True) as connection:
            if retry_of:
                original = self._job(connection, retry_of)
                if original["kind"] == "edit":
                    if (
                        original["trip_id"] != trip_id
                        or body.expected_revision != original["request_revision"]
                    ):
                        raise AppError("revision_conflict", "编辑重试需使用原行程要求版本", 409)
                    raw = connection.execute(
                        "SELECT edit_payload FROM plan_jobs WHERE id=?", (retry_of,)
                    ).fetchone()[0]
                    edit = ItineraryEdit.model_validate_json(raw).model_copy(
                        update={"idempotency_key": body.idempotency_key}
                    )
                    return self._submit_edit(connection, trip_id, edit, retry_of, configured)
            existing = connection.execute(
                "SELECT id,submission_revision,retry_of,kind FROM plan_jobs "
                "WHERE trip_id=? AND submission_key=?",
                (trip_id, body.idempotency_key),
            ).fetchone()
            if existing:
                if (
                    existing["submission_revision"] != body.expected_revision
                    or existing["retry_of"] != retry_of
                    or existing["kind"] != "plan"
                ):
                    raise AppError("idempotency_conflict", "请求标识已用于其他规划条件", 409)
                return self._job(connection, existing["id"])
            trip = require(
                connection.execute("SELECT revision FROM trips WHERE id=?", (trip_id,)).fetchone(),
                trip_id,
            )
            if trip["revision"] != body.expected_revision:
                raise AppError(
                    "revision_conflict", "行程要求已更新，请加载最新版本", 409, object_id=trip_id
                )
            if retry_of:
                original = self._job(connection, retry_of)
                if original["trip_id"] != trip_id or original["status"] not in {
                    "failed",
                    "interrupted",
                }:
                    raise AppError(
                        "job_not_retryable", "仅失败或中断任务可以重试", 409, object_id=retry_of
                    )
            snapshot = self._snapshot(connection, trip_id, body.expected_revision)
            if not configured and not self._missing(snapshot):
                raise AppError("config_incomplete", "请补齐模型、地图与搜索配置后重新提交", 503)
            job_id, timestamp = new_id(), now()
            connection.execute(
                """INSERT INTO plan_jobs
                (id,trip_id,request_revision,submission_revision,submission_key,status,stage,
                 retry_of,created_at,updated_at)
                VALUES (?,?,?,?,?,'queued','conditions',?,?,?)""",
                (
                    job_id,
                    trip_id,
                    body.expected_revision,
                    body.expected_revision,
                    body.idempotency_key,
                    retry_of,
                    timestamp,
                    timestamp,
                ),
            )
            self._event(connection, job_id, "conditions", "queued")
            return self._job(connection, job_id)

    def _itinerary(self, connection, trip_id: str, revision: int) -> Itinerary:
        row = require(
            connection.execute(
                "SELECT payload FROM itinerary_revisions WHERE trip_id=? AND revision=?",
                (trip_id, revision),
            ).fetchone(),
            trip_id,
        )
        return Itinerary.model_validate_json(row[0])

    def _check_edit_base(self, connection, trip_id: str, revision: int, request_revision: int):
        row = require(
            connection.execute("SELECT revision FROM trips WHERE id=?", (trip_id,)).fetchone(),
            trip_id,
        )
        current = connection.execute(
            "SELECT revision FROM current_itineraries WHERE trip_id=?", (trip_id,)
        ).fetchone()
        if not current or current[0] != revision:
            raise AppError(
                "revision_conflict", "行程草案已更新，请加载最新版本", 409, object_id=trip_id
            )
        base = self._itinerary(connection, trip_id, revision)
        if row[0] != request_revision or base.request_revision != request_revision:
            raise AppError(
                "request_changed", "行程要求已改变，请重新生成后编辑", 409, object_id=trip_id
            )
        return base

    def edit_input(self, job: dict):
        with self.db.connect() as connection:
            base = self._check_edit_base(
                connection, job["trip_id"], job["base_revision"], job["request_revision"]
            )
            raw = connection.execute(
                "SELECT edit_payload FROM plan_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
            return base, ItineraryEdit.model_validate_json(raw)

    def submit_edit(self, trip_id: str, body: ItineraryEdit, *, configured: bool) -> dict:
        with self.db.connect(write=True) as connection:
            return self._submit_edit(connection, trip_id, body, None, configured)

    def _submit_edit(self, connection, trip_id, body, retry_of, configured):
        existing = connection.execute(
            "SELECT id,kind,edit_payload,retry_of FROM plan_jobs "
            "WHERE trip_id=? AND submission_key=?",
            (trip_id, body.idempotency_key),
        ).fetchone()
        if existing:
            if (
                existing["kind"] != "edit"
                or existing["retry_of"] != retry_of
                or ItineraryEdit.model_validate_json(existing["edit_payload"]) != body
            ):
                raise AppError("idempotency_conflict", "请求标识已用于其他编辑或规划条件", 409)
            return self._job(connection, existing["id"])
        if retry_of:
            original = self._job(connection, retry_of)
            if original["status"] not in {"failed", "interrupted"}:
                raise AppError(
                    "job_not_retryable", "仅失败或中断任务可以重试", 409, object_id=retry_of
                )
        base = self._check_edit_base(
            connection, trip_id, body.expected_revision, body.expected_request_revision
        )
        request = self._snapshot(connection, trip_id, body.expected_request_revision)
        apply_operations(base, body, request)
        if not configured:
            raise AppError("config_incomplete", "请补齐模型、地图与搜索配置后重新核验", 503)
        job_id, timestamp = new_id(), now()
        connection.execute(
            """INSERT INTO plan_jobs
            (id,trip_id,request_revision,submission_revision,submission_key,status,stage,retry_of,
             created_at,updated_at,kind,base_revision,edit_payload)
            VALUES (?,?,?,?,?,'queued','editing',?,?,?,'edit',?,?)""",
            (
                job_id,
                trip_id,
                body.expected_request_revision,
                body.expected_request_revision,
                body.idempotency_key,
                retry_of,
                timestamp,
                timestamp,
                body.expected_revision,
                body.model_dump_json(),
            ),
        )
        self._event(connection, job_id, "editing", "queued")
        return self._job(connection, job_id)

    def resume(self, job_id: str, revision: int, *, configured: bool) -> dict:
        with self.db.connect(write=True) as connection:
            job = self._job(connection, job_id)
            if job["status"] != "awaiting_input":
                raise AppError(
                    "job_not_awaiting_input", "任务当前不等待补充", 409, object_id=job_id
                )
            current = connection.execute(
                "SELECT revision FROM trips WHERE id=?", (job["trip_id"],)
            ).fetchone()[0]
            if revision != current or revision <= job["request_revision"]:
                raise AppError(
                    "revision_conflict", "请保存补充条件后按最新版本继续", 409, object_id=job_id
                )
            snapshot = self._snapshot(connection, job["trip_id"], revision)
            if not configured and not self._missing(snapshot):
                raise AppError("config_incomplete", "请补齐模型、地图与搜索配置后继续", 503)
            connection.execute(
                "UPDATE plan_jobs SET request_revision=?,status='queued',stage='conditions',"
                "questions='[]',error=NULL,updated_at=? WHERE id=?",
                (revision, now(), job_id),
            )
            self._event(connection, job_id, "conditions", "queued")
            return self._job(connection, job_id)

    @staticmethod
    def _missing(snapshot: dict) -> list[str]:
        return [key for key in ("start_date", "end_date", "travelers") if snapshot[key] is None]

    def _snapshot(self, connection, trip_id: str, revision: int) -> dict:
        row = require(
            connection.execute(
                "SELECT payload FROM trip_revisions WHERE trip_id=? AND revision=?",
                (trip_id, revision),
            ).fetchone(),
            trip_id,
        )
        return TripFields.model_validate_json(row["payload"]).model_dump(mode="json")

    def snapshot(self, job: dict) -> dict:
        with self.db.connect() as connection:
            return self._snapshot(connection, job["trip_id"], job["request_revision"])

    def claim(self) -> dict | None:
        with self.db.connect(write=True) as connection:
            if connection.execute("SELECT 1 FROM plan_jobs WHERE status='running'").fetchone():
                return None
            row = connection.execute(
                "SELECT id FROM plan_jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1"
            ).fetchone()
            if row:
                kind = connection.execute(
                    "SELECT kind FROM plan_jobs WHERE id=?", (row["id"],)
                ).fetchone()[0]
                self._update(
                    connection, row["id"], "running", "editing" if kind == "edit" else "conditions"
                )
                return self._job(connection, row["id"])
        return None

    @staticmethod
    def _event(connection, job_id: str, stage: str, status: str):
        connection.execute(
            "INSERT INTO job_events(job_id,stage,status,recorded_at) VALUES (?,?,?,?)",
            (job_id, stage, status, now()),
        )

    def _update(
        self, connection, job_id: str, status: str, stage: str, *, error=None, questions=None
    ):
        connection.execute(
            "UPDATE plan_jobs SET status=?,stage=?,error=?,questions=?,updated_at=? WHERE id=?",
            (
                status,
                stage,
                json.dumps(error, ensure_ascii=False) if error else None,
                json.dumps(questions or [], ensure_ascii=False),
                now(),
                job_id,
            ),
        )
        self._event(connection, job_id, stage, status)

    def update(self, job_id: str, status: str, stage: str, **kwargs):
        with self.db.connect(write=True) as connection:
            row = require(
                connection.execute("SELECT status FROM plan_jobs WHERE id=?", (job_id,)).fetchone(),
                job_id,
            )
            if row["status"] == "running":
                self._update(connection, job_id, status, stage, **kwargs)

    def interrupt_unfinished(self):
        with self.db.connect(write=True) as connection:
            rows = connection.execute(
                "SELECT id,stage FROM plan_jobs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                error = AppError(
                    "job_interrupted",
                    "服务停止导致任务中断，可重新提交",
                    retryable=True,
                    object_id=row["id"],
                ).payload()
                self._update(connection, row["id"], "interrupted", row["stage"], error=error)

    def save(self, job: dict, payload: dict) -> dict:
        with self.db.connect(write=True) as connection:
            if job["kind"] == "edit":
                base = self._check_edit_base(
                    connection, job["trip_id"], job["base_revision"], job["request_revision"]
                )
                raw = connection.execute(
                    "SELECT edit_payload FROM plan_jobs WHERE id=?", (job["id"],)
                ).fetchone()[0]
                check_saved_scope(
                    base,
                    ItineraryEdit.model_validate_json(raw),
                    self._snapshot(connection, job["trip_id"], job["request_revision"]),
                    payload,
                )
            current = connection.execute(
                "SELECT revision FROM trips WHERE id=?", (job["trip_id"],)
            ).fetchone()[0]
            if current != job["request_revision"]:
                raise AppError(
                    "request_changed",
                    "规划期间行程要求已更新，请按最新条件重试",
                    409,
                    object_id=job["trip_id"],
                )
            running = connection.execute(
                "SELECT status FROM plan_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
            if running != "running":
                raise AppError(
                    "job_not_running", "任务已停止，结果未保存", 409, object_id=job["id"]
                )
            revision = connection.execute(
                "SELECT coalesce(max(revision),0)+1 FROM itinerary_revisions WHERE trip_id=?",
                (job["trip_id"],),
            ).fetchone()[0]
            itinerary = Itinerary.model_validate(
                {
                    **payload,
                    "id": job["trip_id"],
                    "trip_id": job["trip_id"],
                    "revision": revision,
                    "request_revision": job["request_revision"],
                    "job_id": job["id"],
                    "created_at": now(),
                }
            )
            connection.execute(
                "INSERT INTO itinerary_revisions VALUES (?,?,?,?,?)",
                (
                    job["trip_id"],
                    revision,
                    job["request_revision"],
                    job["id"],
                    itinerary.model_dump_json(),
                ),
            )
            connection.execute(
                "INSERT INTO current_itineraries VALUES (?,?) "
                "ON CONFLICT(trip_id) DO UPDATE SET revision=excluded.revision",
                (job["trip_id"], revision),
            )
            self._update(connection, job["id"], "succeeded", "saved")
            connection.execute(
                "UPDATE plan_jobs SET result_revision=? WHERE id=?", (revision, job["id"])
            )
            return itinerary.model_dump(mode="json")

    def itinerary(self, trip_id: str, revision: int | None = None) -> dict:
        with self.db.connect() as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT revision FROM current_itineraries WHERE trip_id=?", (trip_id,)
                ).fetchone()
                if not row:
                    raise AppError(
                        "itinerary_not_found", "尚无已保存的行程草案", 404, object_id=trip_id
                    )
                revision = row[0]
            row = require(
                connection.execute(
                    "SELECT payload FROM itinerary_revisions WHERE trip_id=? AND revision=?",
                    (trip_id, revision),
                ).fetchone(),
                trip_id,
            )
            return json.loads(row[0])
