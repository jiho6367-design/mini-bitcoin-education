"""Native PostgreSQL persistence for the team's original account-model lab."""

import hashlib
import json

from postgres_backend import PostgresBackendError
from storage import RevisionConflict, StorageError, timestamp


class BasicStore:
    def __init__(self, backend, *, max_practices=500):
        self.backend = backend
        self.path = "postgresql:" + backend.schema_name("basic")
        self.max_practices = max_practices
        self._lock = backend.lock
        self._table = backend.table("basic", "practices")
        expected = {"id": "text", "revision": "int8", "created_at": "text",
                    "saved_at": "text", "payload": "text", "digest": "text"}
        try:
            with backend.transaction(write=True) as connection:
                backend.validate_table(connection, "basic", "practices", expected)
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._table} ("
                    "id TEXT PRIMARY KEY, revision BIGINT NOT NULL CHECK(revision>=0), "
                    "created_at TEXT NOT NULL, saved_at TEXT NOT NULL, "
                    "payload TEXT NOT NULL, digest TEXT NOT NULL)"
                )
                backend.validate_table(connection, "basic", "practices", expected)
        except PostgresBackendError:
            raise StorageError("기초 실습 저장소를 초기화하지 못했습니다. DB 연결과 저장 형식을 확인하세요.") from None

    @staticmethod
    def _encode(payload):
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create(self, practice_id, payload):
        raw, digest = self._encode(payload)
        stamp = timestamp()
        try:
            with self.backend.transaction(write=True) as connection:
                count = connection.execute(f"SELECT count(*) AS total FROM {self._table}").fetchone()["total"]
                if count >= self.max_practices:
                    raise StorageError("저장된 기초 실습 수가 한도에 도달했습니다")
                connection.execute(
                    f"INSERT INTO {self._table} (id,revision,created_at,saved_at,payload,digest) "
                    "VALUES (%s,0,%s,%s,%s,%s)", (practice_id, stamp, stamp, raw, digest),
                )
        except PostgresBackendError:
            raise StorageError("실습을 저장하지 못했습니다. DB 연결·용량 또는 현재 배포 상태를 확인하세요.") from None
        return stamp

    def read(self, practice_id):
        try:
            with self.backend.transaction() as connection:
                row = connection.execute(
                    f"SELECT * FROM {self._table} WHERE id=%s", (practice_id,),
                ).fetchone()
        except PostgresBackendError:
            raise StorageError("저장된 실습을 읽지 못했습니다. DB 연결 상태를 확인하세요.") from None
        if row is None:
            return None
        if hashlib.sha256(row["payload"].encode("utf-8")).hexdigest() != row["digest"]:
            raise StorageError("저장 데이터의 무결성 검증에 실패했습니다. 원본은 변경하지 않았습니다.")
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            raise StorageError("저장 데이터를 해석할 수 없습니다. 원본은 변경하지 않았습니다.") from None
        return {"id": row["id"], "revision": row["revision"], "saved_at": row["saved_at"], "payload": payload}

    def save(self, practice_id, revision, payload):
        raw, digest = self._encode(payload)
        stamp = timestamp()
        try:
            with self.backend.transaction(write=True) as connection:
                changed = connection.execute(
                    f"UPDATE {self._table} SET revision=%s,saved_at=%s,payload=%s,digest=%s "
                    "WHERE id=%s AND revision=%s",
                    (revision + 1, stamp, raw, digest, practice_id, revision),
                ).rowcount
                if changed != 1:
                    raise RevisionConflict("다른 요청에서 실습이 변경됐습니다. 현재 상태를 다시 확인하세요.")
        except RevisionConflict:
            raise
        except PostgresBackendError:
            raise StorageError("저장되지 않아 이번 작업을 되돌렸습니다. DB 연결·용량 또는 현재 배포 상태를 확인하세요.") from None
        return stamp

    def close(self):
        self.backend.close()


PostgresBasicStore = BasicStore
