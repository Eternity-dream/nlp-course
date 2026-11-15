import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def instance_lock(data_dir: Path):
    """一个数据目录只允许一个应用进程执行后台导入与恢复。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".instance.lock").open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("数据目录已被应用占用，请使用单个服务进程") from exc
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
