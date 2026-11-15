"""Offline restore and consistent online backups: python -m ...service.backup."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from .storage import PracticeStore, default_path


def main():
    parser = argparse.ArgumentParser(description="실습 DB 백업·복원 (복원 전 서버를 종료하세요)")
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("file", type=Path)
    parser.add_argument("--db", type=Path, default=default_path())
    parser.add_argument("--confirm-server-stopped", action="store_true")
    args = parser.parse_args()
    if args.file.resolve() == args.db.resolve():
        parser.error("백업 파일과 운영 DB 경로는 달라야 합니다.")
    if args.action == "backup" and args.file.exists():
        parser.error("기존 파일을 덮어쓰지 않습니다. 새 백업 이름을 입력하세요.")
    if args.action == "restore" and not args.confirm_server_stopped:
        parser.error("모든 실습 서버를 종료한 뒤 --confirm-server-stopped를 지정하세요.")
    store = PracticeStore(args.db)
    try:
        if args.action == "backup":
            store.backup(args.file)
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
            recovery = args.db.with_name(f"before-restore-{stamp}.sqlite3")
            store.backup(recovery)
            store.restore_backup(args.file)
            print(f"복원 전 백업: {recovery}")
        print(f"완료: {args.file}")
    finally:
        store.db.close()


if __name__ == "__main__":
    main()
