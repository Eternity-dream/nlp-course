import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 4
MIGRATIONS = {
    1: [
        """CREATE TABLE trips (
            id TEXT PRIMARY KEY, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        """CREATE TABLE trip_revisions (
            trip_id TEXT NOT NULL REFERENCES trips(id), revision INTEGER NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(trip_id, revision))""",
        """CREATE TABLE preferences (
            id INTEGER PRIMARY KEY CHECK(id = 1), revision INTEGER NOT NULL,
            payload TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE knowledge_sources (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, url TEXT,
            file_ref TEXT NOT NULL, input_sha256 TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','processing','ready','failed')),
            error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE knowledge_chunks (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES knowledge_sources(id)
            ON DELETE CASCADE, ordinal INTEGER NOT NULL, text TEXT NOT NULL,
            page INTEGER, paragraph INTEGER, offset INTEGER NOT NULL,
            UNIQUE(source_id, ordinal))""",
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5(chunk_id UNINDEXED, tokens)",
        """CREATE TRIGGER knowledge_chunks_delete AFTER DELETE ON knowledge_chunks
            BEGIN DELETE FROM knowledge_fts WHERE chunk_id = old.id; END""",
        """CREATE TABLE file_cleanup (
            file_ref TEXT PRIMARY KEY, created_at TEXT NOT NULL)""",
    ],
    2: [
        """CREATE TABLE service_checks (
        id TEXT PRIMARY KEY, service TEXT NOT NULL, checked_at TEXT NOT NULL, payload TEXT NOT NULL)
        """
    ],
    3: [
        """CREATE TABLE plan_jobs (
            id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id),
            request_revision INTEGER NOT NULL, submission_revision INTEGER NOT NULL,
            submission_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
              ('queued','running','awaiting_input','succeeded','failed','interrupted')),
            stage TEXT NOT NULL, retry_of TEXT REFERENCES plan_jobs(id), result_revision INTEGER,
            questions TEXT NOT NULL DEFAULT '[]', error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(trip_id,submission_key),
            FOREIGN KEY(trip_id,request_revision) REFERENCES trip_revisions(trip_id,revision))""",
        "CREATE INDEX plan_jobs_queue ON plan_jobs(status,created_at)",
        """CREATE TABLE job_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES plan_jobs(id), stage TEXT NOT NULL,
            status TEXT NOT NULL, recorded_at TEXT NOT NULL)""",
        """CREATE TABLE itinerary_revisions (
            trip_id TEXT NOT NULL REFERENCES trips(id), revision INTEGER NOT NULL,
            request_revision INTEGER NOT NULL, job_id TEXT NOT NULL REFERENCES plan_jobs(id),
            payload TEXT NOT NULL, PRIMARY KEY(trip_id,revision))""",
        """CREATE TABLE current_itineraries (
            trip_id TEXT PRIMARY KEY REFERENCES trips(id), revision INTEGER NOT NULL,
            FOREIGN KEY(trip_id,revision) REFERENCES itinerary_revisions(trip_id,revision))""",
    ],
    4: [
        "ALTER TABLE plan_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'plan' "
        "CHECK(kind IN ('plan','edit'))",
        "ALTER TABLE plan_jobs ADD COLUMN base_revision INTEGER",
        "ALTER TABLE plan_jobs ADD COLUMN edit_payload TEXT",
    ],
}


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self, *, write: bool = False):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        with self.connect(write=True) as connection:
            current = connection.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError("数据库版本高于应用版本，请使用对应版本的应用与备份")
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[version]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version={version}")
