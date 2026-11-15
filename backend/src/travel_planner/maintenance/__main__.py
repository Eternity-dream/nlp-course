import argparse
import json
import sqlite3
import sys
from pathlib import Path

from travel_planner.config import Settings
from travel_planner.maintenance.archive import backup, restore, verify
from travel_planner.maintenance.errors import DataError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="停服后校验、备份和恢复旅行规划数据")
    parser.add_argument("--data-dir", type=Path, help="数据目录，默认读取 DATA_DIR 或 ./data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify", help="校验数据库、资料文件、全文索引和业务版本")
    backup_parser = subparsers.add_parser("backup", help="生成含摘要清单的 ZIP 备份")
    backup_parser.add_argument("--output", required=True, type=Path)
    restore_parser = subparsers.add_parser("restore", help="将备份恢复到空目录")
    restore_parser.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        data_dir = args.data_dir or Settings().data_dir
        if args.command == "verify":
            result = verify(data_dir)
        elif args.command == "backup":
            result = backup(data_dir, args.output)
        else:
            result = restore(data_dir, args.archive)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except DataError as exc:
        error = exc.payload()
    except (OSError, sqlite3.Error):
        error = {
            "code": "data_io_error",
            "message": "数据读写失败，请检查目录权限、可用空间及文件完整性",
        }
    except Exception:
        error = {"code": "maintenance_failed", "message": "数据维护失败，请检查备份格式与应用版本"}
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
