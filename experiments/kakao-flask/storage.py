"""Local SQLite practice records; no user data is opened until a request needs it."""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class StorageError(Exception):
    pass


class RevisionConflict(StorageError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class PracticeStore:
    def __init__(self, path):
        self.path = str(path)
        self._db = None
        self._lock = threading.RLock()

    def _connect(self):
        if self._db is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            db.row_factory = sqlite3.Row
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise StorageError("지원하지 않는 저장 형식입니다. 기존 파일은 변경하지 않았습니다.")
                existing = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='practices'").fetchone()
                if existing:
                    columns = db.execute("PRAGMA table_info(practices)").fetchall()
                    if ({r["name"] for r in columns} != {"id", "revision", "created_at", "saved_at", "payload", "digest"}
                            or not any(r["name"] == "id" and r["pk"] for r in columns)):
                        raise StorageError("실습 저장 테이블 형식이 다릅니다. 기존 파일은 변경하지 않았습니다.")
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS practices (
                        id TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        saved_at TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        digest TEXT NOT NULL
                    );
                    PRAGMA user_version=1;
                """)
            except Exception:
                db.close()
                raise
            self._db = db
        return self._db

    @staticmethod
    def _encode(payload):
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create(self, practice_id, payload):
        raw, digest = self._encode(payload)
        now = timestamp()
        try:
            with self._lock:
                db = self._connect()
                with db:
                    db.execute("INSERT INTO practices (id, revision, created_at, saved_at, payload, digest) VALUES (?, 0, ?, ?, ?, ?)",
                               (practice_id, now, now, raw, digest))
        except (sqlite3.Error, OSError) as error:
            raise StorageError("실습을 저장하지 못했습니다. 디스크 공간과 저장 폴더 권한을 확인하세요.") from error
        return now

    def read(self, practice_id):
        try:
            with self._lock:
                row = self._connect().execute("SELECT * FROM practices WHERE id=?", (practice_id,)).fetchone()
        except (sqlite3.Error, OSError) as error:
            raise StorageError("저장된 실습을 읽지 못했습니다. 저장 파일과 폴더 권한을 확인하세요.") from error
        if row is None:
            return None
        if hashlib.sha256(row["payload"].encode("utf-8")).hexdigest() != row["digest"]:
            raise StorageError("저장 데이터의 무결성 검증에 실패했습니다. 원본은 변경하지 않았습니다.")
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError) as error:
            raise StorageError("저장 데이터를 해석할 수 없습니다. 원본은 변경하지 않았습니다.") from error
        return {"id": row["id"], "revision": row["revision"], "saved_at": row["saved_at"], "payload": payload}

    def save(self, practice_id, revision, payload):
        raw, digest = self._encode(payload)
        now = timestamp()
        try:
            with self._lock:
                db = self._connect()
                with db:
                    changed = db.execute(
                        "UPDATE practices SET revision=?, saved_at=?, payload=?, digest=? WHERE id=? AND revision=?",
                        (revision + 1, now, raw, digest, practice_id, revision),
                    ).rowcount
                    if changed != 1:
                        raise RevisionConflict("다른 요청에서 실습이 변경됐습니다. 현재 상태를 다시 확인하세요.")
        except RevisionConflict:
            raise
        except (sqlite3.Error, OSError) as error:
            raise StorageError("저장되지 않아 이번 작업을 되돌렸습니다. 저장 공간과 폴더 권한을 확인하세요.") from error
        return now

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
