import errno
import json
import os
import shutil
import tempfile
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from travel_planner.maintenance.errors import DataError
from travel_planner.maintenance.validation import (
    DATABASE_NAME,
    RESTORE_MARKER,
    inspect_data,
    inventory,
    managed_path,
    sha256_file,
    snapshot_database,
)
from travel_planner.storage.database import Database
from travel_planner.storage.instance_lock import instance_lock

MANIFEST = "manifest.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_FILES = 10000


@contextmanager
def offline(root: Path, *, create: bool = False):
    if not create and not root.is_dir():
        raise DataError("invalid_data_dir", "数据目录不存在")
    if root.is_symlink():
        raise DataError("invalid_data_dir", "数据目录不能是符号链接")
    with ExitStack() as stack:
        try:
            stack.enter_context(instance_lock(root))
        except RuntimeError as exc:
            raise DataError(
                "data_in_use", "数据目录正由应用或维护命令使用，请停服并等待进程退出"
            ) from exc
        yield


def verify(root: Path) -> dict:
    with offline(root):
        return inspect_data(root)


def publish_archive(temporary: Path, output: Path):
    try:
        os.link(temporary, output)
        return
    except FileExistsError as exc:
        raise DataError("archive_exists", "备份文件已存在，请使用新文件名") from exc
    except OSError as exc:
        if exc.errno not in {errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP, errno.EXDEV}:
            raise
    # 部分宿主机绑定目录不支持硬链接；仍以独占创建避免覆盖旧备份。
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise DataError("archive_exists", "备份文件已存在，请使用新文件名") from exc
    try:
        with os.fdopen(descriptor, "wb") as destination, temporary.open("rb") as source:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def backup(root: Path, output: Path) -> dict:
    root, output = root.absolute(), output.absolute()
    if output.resolve().is_relative_to(root.resolve()):
        raise DataError("invalid_archive_path", "备份文件需要保存在数据目录之外")
    if output.exists() or output.is_symlink():
        raise DataError("archive_exists", "备份文件已存在，请使用新文件名")
    output.parent.mkdir(parents=True, exist_ok=True)
    with offline(root), tempfile.TemporaryDirectory(prefix="travel-backup-") as directory:
        files = inventory(root)
        staged = Path(directory)
        snapshot_database(files[DATABASE_NAME], staged / DATABASE_NAME)
        for name, path in files.items():
            if name != DATABASE_NAME:
                destination = staged / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
        report = inspect_data(staged)
        if not report["idle"]:
            raise DataError(
                "data_not_idle", "存在未结束的规划、导入或文件清理，请先完成处理再停服备份"
            )
        staged_files = inventory(staged)
        entries = [
            {"path": name, "size": path.stat().st_size, "sha256": sha256_file(path)}
            for name, path in sorted(staged_files.items())
        ]
        if len(entries) > MAX_FILES or sum(entry["size"] for entry in entries) > MAX_TOTAL_BYTES:
            raise DataError("archive_limit", "数据超过备份格式的 8 GiB 或文件数上限")
        manifest = {
            "format": 1,
            "application_version": version("travel-planner"),
            "created_at": datetime.now(UTC).isoformat(),
            "schema_version": report["schema_version"],
            "counts": report["counts"],
            "files": entries,
        }
        # 在目标目录写临时文件，再以独占硬链接发布，避免覆盖已有备份。
        temporary = tempfile.NamedTemporaryFile(
            prefix=".travel-backup-", suffix=".zip", dir=output.parent, delete=False
        )
        temporary.close()
        temporary_path = Path(temporary.name)
        try:
            with zipfile.ZipFile(
                temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False))
                for entry in entries:
                    archive.write(staged_files[entry["path"]], entry["path"])
            publish_archive(temporary_path, output)
        finally:
            temporary_path.unlink(missing_ok=True)
        return {**report, "archive": str(output), "archive_sha256": sha256_file(output)}


def extract_archive(archive_path: Path, staging: Path) -> dict:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            if (
                len(members) > MAX_FILES + 1
                or len(names) != len(set(names))
                or MANIFEST not in names
            ):
                raise DataError("invalid_archive", "备份目录重复、缺少清单或文件数超限")
            manifest_info = archive.getinfo(MANIFEST)
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise DataError("invalid_archive", "备份清单超过大小上限")
            manifest = json.loads(archive.read(MANIFEST))
            if (
                not isinstance(manifest, dict)
                or type(manifest.get("format")) is not int
                or manifest["format"] != 1
            ):
                raise DataError("invalid_archive", "备份格式版本不受支持")
            files = manifest.get("files")
            if not isinstance(files, list) or not files or len(files) > MAX_FILES:
                raise DataError("invalid_archive", "备份文件清单无效")
            seen, total = set(), 0
            for entry in files:
                if not isinstance(entry, dict):
                    raise DataError("invalid_archive", "备份清单项无效")
                name, size, digest = entry.get("path"), entry.get("size"), entry.get("sha256")
                if (
                    not isinstance(name, str)
                    or not managed_path(name)
                    or name in seen
                    or type(size) is not int
                    or size < 0
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                ):
                    raise DataError("invalid_archive", "备份包含不安全路径、重复项或无效摘要")
                seen.add(name)
                total += size
                if total > MAX_TOTAL_BYTES:
                    raise DataError("archive_limit", "备份展开后超过 8 GiB")
            if seen != set(names) - {MANIFEST} or DATABASE_NAME not in seen:
                raise DataError("invalid_archive", "备份文件与清单不一致")
            for entry in files:
                name = entry["path"]
                info = archive.getinfo(name)
                if (
                    info.file_size != entry["size"]
                    or info.is_dir()
                    or info.flag_bits & 1
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise DataError("invalid_archive", "备份文件类型、大小或加密状态无效")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(info) as source, target.open("xb") as destination:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > entry["size"]:
                            raise DataError("invalid_archive", "备份文件展开后超出声明大小")
                        destination.write(chunk)
                if written != entry["size"] or sha256_file(target) != entry["sha256"]:
                    raise DataError("archive_hash_mismatch", "备份内容与摘要不一致")
            report = inspect_data(staging)
            if report["schema_version"] != manifest.get("schema_version") or report[
                "counts"
            ] != manifest.get("counts"):
                raise DataError("invalid_archive", "备份清单与数据库版本或记录数量不一致")
            if not report["idle"]:
                raise DataError(
                    "data_not_idle", "备份包含未结束的任务、导入或文件清理，请使用空闲时生成的备份"
                )
            return report
    except (
        zipfile.BadZipFile,
        json.JSONDecodeError,
        UnicodeError,
        KeyError,
        RuntimeError,
        EOFError,
    ) as exc:
        raise DataError("invalid_archive", "备份文件损坏或内容无法读取") from exc


def restore(root: Path, archive_path: Path) -> dict:
    if not archive_path.is_file():
        raise DataError("archive_missing", "备份文件不存在")
    if archive_path.resolve().is_relative_to(root.resolve()):
        raise DataError("invalid_archive_path", "恢复用备份需要保存在目标数据目录之外")
    with offline(root, create=True):
        if any(path.name != ".instance.lock" for path in root.iterdir()):
            raise DataError("target_not_empty", "恢复目标需要空目录或空数据卷，已有数据不会被覆盖")
        marker = root / RESTORE_MARKER
        marker.write_text(
            json.dumps({"format": 1, "archive_sha256": sha256_file(archive_path)}),
            encoding="utf-8",
        )
        published = []
        publishing = False
        try:
            with tempfile.TemporaryDirectory(prefix=".restore-", dir=root) as directory:
                staging = Path(directory)
                report = extract_archive(archive_path, staging)
                original_version = report["schema_version"]
                Database(staging / DATABASE_NAME).migrate()
                staged_report = inspect_data(staging)
                publishing = True
                for name in ("sources", DATABASE_NAME):
                    source = staging / name
                    if source.exists():
                        os.replace(source, root / name)
                        published.append(root / name)
                shutil.rmtree(staging)
                result = inspect_data(root, restoring=True)
                if result != staged_report:
                    raise DataError("restore_validation_failed", "恢复后的数据与验证结果不一致")
                marker.unlink()
            return {**result, "restored_from_schema": original_version}
        except BaseException:
            for path in reversed(published):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            if not publishing and all(
                path.name in {".instance.lock", RESTORE_MARKER} for path in root.iterdir()
            ):
                marker.unlink(missing_ok=True)
            # 发布失败或进程中断保留标记，阻止应用读取不完整数据。
            raise
