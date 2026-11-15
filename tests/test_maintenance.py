import errno
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from conftest import wait_source
from fastapi.testclient import TestClient
from travel_planner.domain.models import TripFields
from travel_planner.knowledge.service import KnowledgeService
from travel_planner.main import create_app
from travel_planner.maintenance import archive as maintenance
from travel_planner.maintenance.__main__ import main
from travel_planner.maintenance.archive import backup, restore, verify
from travel_planner.maintenance.errors import DataError
from travel_planner.maintenance.validation import RESTORE_MARKER, sha256_file
from travel_planner.planner.models import JobSubmit
from travel_planner.planner.storage import JobStore
from travel_planner.storage.database import MIGRATIONS, Database
from travel_planner.storage.instance_lock import instance_lock
from travel_planner.storage.repository import Repository


@pytest.fixture
def data_dir(settings):
    database = Database(settings.data_dir / "app.sqlite3")
    database.migrate()
    repository = Repository(database)
    repository.initialize()
    repository.create_trip(TripFields(destination="西安"))
    return settings.data_dir


@pytest.fixture
def archive_file(data_dir, tmp_path):
    path = tmp_path / "snapshot.zip"
    backup(data_dir, path)
    return path


@pytest.fixture
def populated_data(settings):
    with TestClient(create_app(settings)) as client:
        source = client.post(
            "/api/knowledge", json={"title": "旅行笔记", "text": "西安碑林博物馆参观笔记。"}
        ).json()
        assert wait_source(client, source["id"])["status"] == "ready"
    return settings.data_dir, source["id"]


def rewrite_archive(source, target, change):
    with zipfile.ZipFile(source) as original:
        members = {name: original.read(name) for name in original.namelist()}
    manifest = json.loads(members.pop("manifest.json"))
    change(manifest, members)
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in members.items():
            archive.writestr(name, content)
    return target


def test_verify_preserves_source_and_reports_counts(data_dir):
    digest = sha256_file(data_dir / "app.sqlite3")
    report = verify(data_dir)
    assert report["schema_version"] == 4
    assert report["counts"]["trips"] == report["counts"]["request_revisions"] == 1
    assert report["raw_files"] == report["orphan_raw_files"] == 0
    assert report["idle"] is True
    assert sha256_file(data_dir / "app.sqlite3") == digest


@pytest.mark.parametrize("command", ["verify", "backup", "restore"])
def test_maintenance_refuses_active_app(data_dir, archive_file, tmp_path, command):
    with instance_lock(data_dir), pytest.raises(DataError) as error:
        if command == "verify":
            verify(data_dir)
        elif command == "backup":
            backup(data_dir, tmp_path / "other.zip")
        else:
            restore(data_dir, archive_file)
    assert error.value.code == "data_in_use"


@pytest.mark.parametrize("state", ["queued", "running", "pending", "processing", "cleanup"])
def test_backup_requires_idle_data(data_dir, tmp_path, state):
    database = Database(data_dir / "app.sqlite3")
    if state in {"queued", "running"}:
        trip = Repository(database).list_trips(1, 0)[0]
        job = JobStore(database).submit(
            trip["id"], JobSubmit(expected_revision=1, idempotency_key="backup")
        )
        with database.connect(write=True) as connection:
            connection.execute("UPDATE plan_jobs SET status=? WHERE id=?", (state, job["id"]))
    elif state in {"pending", "processing"}:
        source = KnowledgeService(database, data_dir).create("text", "待导入", b"notes")
        with database.connect(write=True) as connection:
            connection.execute(
                "UPDATE knowledge_sources SET status=? WHERE id=?", (state, source["id"])
            )
    else:
        with database.connect(write=True) as connection:
            connection.execute("INSERT INTO file_cleanup VALUES ('old/raw','2026-11-15')")
    assert verify(data_dir)["idle"] is False
    with pytest.raises(DataError) as error:
        backup(data_dir, tmp_path / "busy.zip")
    assert error.value.code == "data_not_idle"
    assert not (tmp_path / "busy.zip").exists()


def test_runtime_includes_jobs_imports_and_cleanup(client):
    assert client.get("/api/runtime").json() == {
        "jobs": {},
        "imports": {},
        "cleanup": {"pending": 0},
        "idle": True,
    }
    client.app.state.knowledge.close()
    source = client.post("/api/knowledge", json={"title": "等待", "text": "西安"}).json()
    # 直接保存任务状态，避免后台执行速度影响运行状态观察。
    with client.app.state.db.connect(write=True) as connection:
        connection.execute("INSERT INTO file_cleanup VALUES ('old/raw','2026-11-15')")
    status = client.get("/api/runtime").json()
    assert status["imports"] == {"pending": 1}
    assert status["cleanup"] == {"pending": 1} and status["idle"] is False
    assert source["status"] == "pending"


def test_backup_includes_committed_wal_and_excludes_config_and_control(data_dir, tmp_path):
    (tmp_path / ".env").write_text("LLM_API_KEY=secret-value")
    with closing(sqlite3.connect(data_dir / "app.sqlite3")) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE preferences SET revision=2,payload=?", ('{"节奏":"慢"}',))
        writer.commit()
        assert (data_dir / "app.sqlite3-wal").stat().st_size > 0
        output = tmp_path / "wal.zip"
        result = backup(data_dir, output)
        assert result["archive_sha256"] == sha256_file(output)
        with zipfile.ZipFile(output) as archive:
            assert set(archive.namelist()) == {"manifest.json", "app.sqlite3"}
            manifest = json.loads(archive.read("manifest.json"))
            assert (
                manifest["files"][0]["sha256"]
                == hashlib.sha256(archive.read("app.sqlite3")).hexdigest()
            )
            assert "secret-value" not in archive.read("manifest.json").decode()
        target = tmp_path / "restored"
        restore(target, output)
    assert Repository(Database(target / "app.sqlite3")).get_preferences()["preferences"] == {
        "节奏": "慢"
    }


def test_backup_and_restore_preserve_orphan_raw_files(data_dir, tmp_path):
    raw = data_dir / "sources" / ("a" * 32) / "raw"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"unreferenced original")
    path = tmp_path / "orphan.zip"
    assert backup(data_dir, path)["orphan_raw_files"] == 1
    target = tmp_path / "restored"
    assert restore(target, path)["orphan_raw_files"] == 1
    assert (target / raw.relative_to(data_dir)).read_bytes() == raw.read_bytes()


def test_backup_never_overwrites_archive_or_writes_inside_data(data_dir, archive_file):
    digest = sha256_file(archive_file)
    with pytest.raises(DataError) as error:
        backup(data_dir, archive_file)
    assert error.value.code == "archive_exists"
    assert sha256_file(archive_file) == digest
    with pytest.raises(DataError) as error:
        backup(data_dir, data_dir / "nested" / "backup.zip")
    assert error.value.code == "invalid_archive_path"
    assert not (data_dir / "nested").exists()


def test_backup_works_without_hardlinks(data_dir, tmp_path, monkeypatch):
    def unsupported(*args):
        raise OSError(errno.ENOTSUP, "hardlink unsupported")

    monkeypatch.setattr(maintenance.os, "link", unsupported)
    archive = tmp_path / "portable.zip"
    backup(data_dir, archive)
    target = tmp_path / "restored"
    assert restore(target, archive)["counts"]["trips"] == 1


def test_archive_publication_copy_fallback_cleans_partial_file_and_preserves_existing(
    tmp_path, monkeypatch
):
    def unsupported(*args):
        raise OSError(errno.ENOTSUP, "hardlink unsupported")

    def full_disk(source, destination):
        destination.write(b"partial")
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(maintenance.os, "link", unsupported)
    monkeypatch.setattr(maintenance.shutil, "copyfileobj", full_disk)
    temporary = tmp_path / "temporary.zip"
    temporary.write_bytes(b"complete")
    output = tmp_path / "failed.zip"
    with pytest.raises(OSError):
        maintenance.publish_archive(temporary, output)
    assert not output.exists()
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"preserved")
    with pytest.raises(DataError) as error:
        maintenance.publish_archive(output, existing)
    assert error.value.code == "archive_exists"
    assert existing.read_bytes() == b"preserved"


def test_restore_never_overwrites_existing_data(data_dir, archive_file):
    digest = sha256_file(data_dir / "app.sqlite3")
    with pytest.raises(DataError) as error:
        restore(data_dir, archive_file)
    assert error.value.code == "target_not_empty"
    assert sha256_file(data_dir / "app.sqlite3") == digest


@pytest.mark.parametrize("damage", ["missing", "bytes", "fts", "reference"])
def test_verify_detects_raw_index_and_reference_damage(populated_data, damage):
    root, source_id = populated_data
    raw = root / "sources" / source_id / "raw"
    if damage == "missing":
        raw.unlink()
        expected = "source_file_missing"
    elif damage == "bytes":
        raw.write_bytes(b"corrupted")
        expected = "source_hash_mismatch"
    else:
        with closing(sqlite3.connect(root / "app.sqlite3")) as connection:
            if damage == "fts":
                connection.execute("DELETE FROM knowledge_fts")
                expected = "index_inconsistent"
            else:
                connection.execute("UPDATE knowledge_chunks SET source_id='missing'")
                expected = "broken_references"
            connection.commit()
    with pytest.raises(DataError) as error:
        verify(root)
    assert error.value.code == expected


@pytest.mark.parametrize("name", [".env", "backup.zip", "unmanaged/danger"])
def test_backup_refuses_unknown_files_in_data_dir(data_dir, tmp_path, name):
    path = data_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private-value")
    with pytest.raises(DataError) as error:
        backup(data_dir, tmp_path / "unexpected.zip")
    assert error.value.code == "unexpected_data_file"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 符号链接需特殊权限")
def test_backup_refuses_symlinks(data_dir, tmp_path):
    (data_dir / "source-link").symlink_to(data_dir / "app.sqlite3")
    with pytest.raises(DataError) as error:
        backup(data_dir, tmp_path / "symlink.zip")
    assert error.value.code == "invalid_data_path"


@pytest.mark.parametrize(
    "path",
    ["../outside", "/tmp/outside", "C:/outside", "sources/../raw", "sources/" + "a" * 32 + "\\raw"],
)
def test_restore_rejects_archive_path_traversal(archive_file, tmp_path, path):
    def unsafe(manifest, members):
        content = b"danger"
        manifest["files"].append(
            {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        )
        members[path] = content

    bad = rewrite_archive(archive_file, tmp_path / "bad.zip", unsafe)
    target = tmp_path / "restored"
    with pytest.raises(DataError) as error:
        restore(target, bad)
    assert error.value.code == "invalid_archive"
    assert {item.name for item in target.iterdir()} == {".instance.lock"}
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("damage", ["hash", "counts", "version", "extra", "duplicate", "symlink"])
def test_restore_rejects_damaged_or_inconsistent_archive(archive_file, tmp_path, damage):
    def change(manifest, members):
        if damage == "hash":
            manifest["files"][0]["sha256"] = "0" * 64
        elif damage == "counts":
            manifest["counts"]["trips"] += 1
        elif damage == "version":
            manifest["schema_version"] = 999
        elif damage == "extra":
            members[".env"] = b"secret"

    bad = rewrite_archive(archive_file, tmp_path / "bad.zip", change)
    if damage == "duplicate":
        with zipfile.ZipFile(bad, "a") as archive, pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("app.sqlite3", b"duplicate")
    elif damage == "symlink":
        with zipfile.ZipFile(archive_file) as source, zipfile.ZipFile(bad, "w") as archive:
            for item in source.infolist():
                if item.filename == "app.sqlite3":
                    item.external_attr = 0o120777 << 16
                archive.writestr(item, source.read(item))
    with pytest.raises(DataError) as error:
        restore(tmp_path / "restored", bad)
    assert error.value.code == ("archive_hash_mismatch" if damage == "hash" else "invalid_archive")


@pytest.mark.parametrize("limit", ["MAX_TOTAL_BYTES", "MAX_MANIFEST_BYTES", "MAX_FILES"])
def test_restore_enforces_resource_limits(archive_file, tmp_path, monkeypatch, limit):
    monkeypatch.setattr(maintenance, limit, 0)
    with pytest.raises(DataError) as error:
        restore(tmp_path / "restored", archive_file)
    assert error.value.code in {"archive_limit", "invalid_archive"}


def test_restore_failure_rolls_back_publication_and_prevents_startup(
    settings, archive_file, tmp_path, monkeypatch
):
    target = tmp_path / "restored"
    original = maintenance.inspect_data

    def fail_at_target(path, **kwargs):
        if path == target:
            raise OSError("private-storage-error")
        return original(path, **kwargs)

    monkeypatch.setattr(maintenance, "inspect_data", fail_at_target)
    with pytest.raises(OSError, match="private-storage-error"):
        restore(target, archive_file)
    assert not (target / "app.sqlite3").exists()
    assert not (target / "sources").exists()
    assert (target / RESTORE_MARKER).is_file()
    with pytest.raises(RuntimeError, match="恢复未完成"):
        with TestClient(create_app(settings.model_copy(update={"data_dir": target}))):
            pass
    assert not (target / "app.sqlite3").exists()
    monkeypatch.setattr(maintenance, "inspect_data", original)
    with pytest.raises(DataError) as error:
        verify(target)
    assert error.value.code == "restore_incomplete"


def test_restore_marks_in_progress_before_extracting(archive_file, tmp_path, monkeypatch):
    target = tmp_path / "restored"

    def interrupted(archive, staging):
        assert (target / RESTORE_MARKER).is_file()
        raise DataError("invalid_archive", "模拟提取失败")

    monkeypatch.setattr(maintenance, "extract_archive", interrupted)
    with pytest.raises(DataError):
        restore(target, archive_file)
    assert {item.name for item in target.iterdir()} == {".instance.lock"}


def test_restore_migrates_older_schema_without_modifying_archive(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    with closing(sqlite3.connect(root / "app.sqlite3")) as connection:
        for statement in MIGRATIONS[1]:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    repository = Repository(Database(root / "app.sqlite3"))
    repository.initialize()
    trip = repository.create_trip(TripFields(destination="西安"))
    path = tmp_path / "old.zip"
    assert backup(root, path)["schema_version"] == 1
    digest = sha256_file(path)
    target = tmp_path / "new"
    result = restore(target, path)
    assert result["restored_from_schema"] == 1 and result["schema_version"] == 4
    assert result["counts"]["trips"] == 1
    assert (
        Repository(Database(target / "app.sqlite3")).get_trip(trip["id"])["destination"] == "西安"
    )
    assert verify(root)["schema_version"] == 1
    assert sha256_file(path) == digest


def test_cli_json_exit_codes_and_error_redaction(data_dir, tmp_path, capsys, monkeypatch):
    assert main(["--data-dir", str(data_dir), "verify"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["--data-dir", str(tmp_path / "missing"), "verify"]) == 1
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_data_dir"

    def fail(*args):
        raise PermissionError("private-storage-error")

    monkeypatch.setattr("travel_planner.maintenance.__main__.verify", fail)
    assert main(["--data-dir", str(data_dir), "verify"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.err)["error"]["code"] == "data_io_error"
    assert "private-storage-error" not in output.err


def test_cli_is_available_in_installed_environment(data_dir, tmp_path):
    executable = Path(sys.executable).parent / (
        "travel-data.exe" if os.name == "nt" else "travel-data"
    )
    result = subprocess.run(
        [str(executable), "--data-dir", str(data_dir), "verify"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"]["counts"]["trips"] == 1
