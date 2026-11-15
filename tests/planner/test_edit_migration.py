import sqlite3

from travel_planner.domain.models import TripFields
from travel_planner.storage.database import MIGRATIONS, Database

from .conftest import TRIP


def test_v3_tasks_upgrade_to_planning_kind_without_losing_state(tmp_path):
    path = tmp_path / "v3.sqlite3"
    with sqlite3.connect(path) as connection:
        for version in (1, 2, 3):
            for statement in MIGRATIONS[version]:
                connection.execute(statement)
        connection.execute("INSERT INTO trips VALUES ('trip',1,'time','time')")
        connection.execute(
            "INSERT INTO trip_revisions VALUES ('trip',1,?)",
            (TripFields.model_validate(TRIP).model_dump_json(),),
        )
        connection.execute("""INSERT INTO plan_jobs
            (id,trip_id,request_revision,submission_revision,submission_key,status,stage,created_at,updated_at)
            VALUES ('job','trip',1,1,'key','awaiting_input','conditions','time','time')""")
        connection.execute("PRAGMA user_version=3")
    Database(path).migrate()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status,kind,base_revision,edit_payload FROM plan_jobs"
        ).fetchone()
        assert row == ("awaiting_input", "plan", None, None)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM trip_revisions").fetchone()[0] == 1
