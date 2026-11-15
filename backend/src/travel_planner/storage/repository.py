import json
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from travel_planner.domain.errors import AppError
from travel_planner.domain.models import PreferencePut, TripFields, TripPatch
from travel_planner.storage.database import Database


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return uuid4().hex


def require(row, object_id: str):
    if row is None:
        raise AppError("not_found", "对象不存在", 404, object_id=object_id)
    return row


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def initialize(self):
        with self.db.connect(write=True) as connection:
            connection.execute("INSERT OR IGNORE INTO preferences VALUES (1, 1, '{}', ?)", (now(),))

    def create_trip(self, request: TripFields) -> dict:
        trip_id, timestamp = new_id(), now()
        with self.db.connect(write=True) as connection:
            connection.execute(
                "INSERT INTO trips VALUES (?, 1, ?, ?)", (trip_id, timestamp, timestamp)
            )
            connection.execute(
                "INSERT INTO trip_revisions VALUES (?, 1, ?)",
                (trip_id, request.model_dump_json()),
            )
            return self._trip(connection, trip_id)

    def _trip(self, connection, trip_id: str) -> dict:
        row = require(
            connection.execute(
                """SELECT t.*, r.payload FROM trips t JOIN trip_revisions r
            ON r.trip_id=t.id AND r.revision=t.revision WHERE t.id=?""",
                (trip_id,),
            ).fetchone(),
            trip_id,
        )
        payload = TripFields.model_validate_json(row["payload"]).model_dump(mode="json")
        return {
            **payload,
            "id": row["id"],
            "revision": row["revision"],
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "missing_fields": [
                key for key in ("start_date", "end_date", "travelers") if payload[key] is None
            ],
        }

    def get_trip(self, trip_id: str) -> dict:
        with self.db.connect() as connection:
            return self._trip(connection, trip_id)

    def list_trips(self, limit: int, offset: int) -> list[dict]:
        with self.db.connect() as connection:
            ids = connection.execute(
                "SELECT id FROM trips ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [self._trip(connection, row["id"]) for row in ids]

    def patch_trip(self, trip_id: str, patch: TripPatch) -> dict:
        with self.db.connect(write=True) as connection:
            trip = self._trip(connection, trip_id)
            if trip["revision"] != patch.expected_revision:
                raise AppError(
                    "revision_conflict", "行程要求已更新，请加载最新版本", 409, object_id=trip_id
                )
            fields = {key: trip[key] for key in TripFields.model_fields}
            changes = patch.model_dump(exclude_unset=True, exclude={"expected_revision"})
            if not changes:
                raise AppError("empty_patch", "请至少修改一个行程字段", 422, object_id=trip_id)
            try:
                request = TripFields.model_validate({**fields, **changes})
            except ValidationError as exc:
                raise AppError(
                    "invalid_trip",
                    "修改后的行程要求无效：" + exc.errors()[0]["msg"],
                    422,
                    object_id=trip_id,
                ) from exc
            revision = trip["revision"] + 1
            connection.execute(
                "INSERT INTO trip_revisions VALUES (?, ?, ?)",
                (trip_id, revision, request.model_dump_json()),
            )
            connection.execute(
                "UPDATE trips SET revision=?, updated_at=? WHERE id=?",
                (revision, now(), trip_id),
            )
            return self._trip(connection, trip_id)

    def get_preferences(self) -> dict:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM preferences WHERE id=1").fetchone()
            return {
                "revision": row["revision"],
                "preferences": json.loads(row["payload"]),
                "updated_at": row["updated_at"],
            }

    def save_service_check(self, result: dict) -> dict:
        record = {"id": new_id(), **result}
        with self.db.connect(write=True) as connection:
            connection.execute(
                "INSERT INTO service_checks VALUES (?,?,?,?)",
                (
                    record["id"],
                    record["service"],
                    record["checked_at"],
                    json.dumps(record, ensure_ascii=False),
                ),
            )
        return record

    def list_service_checks(self) -> list[dict]:
        with self.db.connect() as connection:
            return [
                json.loads(row["payload"])
                for row in connection.execute(
                    "SELECT payload FROM service_checks ORDER BY checked_at DESC,id LIMIT 20"
                )
            ]

    def put_preferences(self, request: PreferencePut) -> dict:
        timestamp = now()
        with self.db.connect(write=True) as connection:
            cursor = connection.execute(
                """UPDATE preferences SET payload=?, revision=revision+1, updated_at=?
                WHERE id=1 AND revision=?""",
                (
                    json.dumps(request.preferences, ensure_ascii=False),
                    timestamp,
                    request.expected_revision,
                ),
            )
            if not cursor.rowcount:
                raise AppError("revision_conflict", "偏好已更新，请加载最新版本", 409)
            return {
                "revision": request.expected_revision + 1,
                "preferences": request.preferences,
                "updated_at": timestamp,
            }
