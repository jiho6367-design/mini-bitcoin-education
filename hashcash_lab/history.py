"""Durable, independently verified Hashcash results in a local SQLite file."""
from __future__ import annotations

import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .cpu import MAX_NONCE, proof_hash, validate_difficulty

PROTOCOL = "sha256-utf8-decimal-hexzeros-v1"
MAX_HISTORY_LIMIT = 500


def validate_participant_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("사용자 이름은 문자열이어야 합니다.")
    value = value.strip()
    if not value or len(value) > 60:
        raise ValueError("사용자 이름은 1~60자로 입력하세요.")
    value.encode("utf-8")
    return value


def validate_message(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("메시지는 문자열이어야 합니다.")
    if len(value.encode("utf-8")) > 4096:
        raise ValueError("메시지는 UTF-8 4096바이트까지 입력할 수 있습니다.")
    return value


def decimal_text(value: str, label: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError(f"{label}는 0 이상의 10진수 문자열이어야 합니다.")
    number = int(value)
    if maximum is not None and number > maximum:
        raise ValueError(f"{label}의 범위를 벗어났습니다.")
    return str(number)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class HistoryStore:
    """Open a fresh connection for each transaction; never keep a process handle.

    Initialization is deferred so a temporarily unavailable directory cannot
    prevent calculation. A failed save can be retried after storage recovers.
    """

    def __init__(self, data_dir: str | Path | None = None):
        selected = data_dir if data_dir is not None else os.environ.get("HASHCASH_DATA_DIR")
        directory = Path(selected) if selected else Path(__file__).resolve().parent.parent / "data"
        self.path = directory.expanduser().resolve() / "hashcash-history.sqlite3"

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    participant_name TEXT NOT NULL,
                    message TEXT NOT NULL,
                    difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 64),
                    nonce TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    checked TEXT NOT NULL,
                    elapsed REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS results_participant_difficulty
                ON results (participant_name, difficulty, created_at DESC)
            """)
            # Keep participants, total, and records in one consistent read
            # snapshot when a different request saves a result concurrently.
            connection.execute("BEGIN")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "jobId": row["job_id"],
            "participantName": row["participant_name"], "message": row["message"],
            "difficulty": row["difficulty"], "nonce": row["nonce"],
            "hash": row["hash"], "backend": row["backend"],
            "checked": row["checked"], "elapsed": row["elapsed"],
            "createdAt": row["created_at"],
        }

    def save(
        self, *, job_id: str, participant_name: str, message: str, difficulty: int,
        nonce: str, hash_value: str, backend: str, checked: str, elapsed: float,
    ) -> dict:
        participant_name = validate_participant_name(participant_name)
        message = validate_message(message)
        validate_difficulty(difficulty)
        nonce = decimal_text(nonce, "Nonce", maximum=MAX_NONCE)
        checked = decimal_text(checked, "탐색 개수", maximum=MAX_NONCE + 1)
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 128:
            raise ValueError("작업 식별자가 올바르지 않습니다.")
        if not isinstance(backend, str) or not 1 <= len(backend) <= 300:
            raise ValueError("연산 장치가 올바르지 않습니다.")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("경과 시간이 올바르지 않습니다.")
        expected = proof_hash(message, int(nonce))
        if hash_value != expected or not expected.startswith("0" * difficulty):
            raise ValueError("기록할 Nonce의 SHA-256 검증에 실패했습니다.")
        values = (job_id, participant_name, message, difficulty, nonce, expected, backend, checked, float(elapsed))
        with self._connection() as connection:
            identifier = uuid.uuid4().hex
            connection.execute("""
                INSERT OR IGNORE INTO results
                (id, job_id, participant_name, message, difficulty, nonce, hash,
                 backend, checked, elapsed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (identifier, *values, utc_now()))
            row = connection.execute("SELECT * FROM results WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise RuntimeError("저장한 기록을 다시 읽지 못했습니다.")
            existing = tuple(row[key] for key in (
                "job_id", "participant_name", "message", "difficulty", "nonce",
                "hash", "backend", "checked", "elapsed",
            ))
            if existing != values:
                raise ValueError("같은 작업 식별자에 다른 결과를 덮어쓸 수 없습니다.")
            return self._record(row)

    def query(
        self, *, participant_name: str | None = None, difficulty: int | None = None,
        message: str | None = None, limit: int | None = 100,
    ) -> dict:
        conditions = []
        parameters = []
        if participant_name is not None:
            conditions.append("participant_name = ?")
            parameters.append(validate_participant_name(participant_name))
        if difficulty is not None:
            conditions.append("difficulty = ?")
            parameters.append(validate_difficulty(difficulty))
        if message is not None:
            conditions.append("message = ?")
            parameters.append(validate_message(message))
        if limit is not None and (type(limit) is not int or not 1 <= limit <= MAX_HISTORY_LIMIT):
            raise ValueError(f"기록 조회 수는 1~{MAX_HISTORY_LIMIT} 사이의 정수입니다.")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connection() as connection:
            participants = [row[0] for row in connection.execute(
                "SELECT DISTINCT participant_name FROM results ORDER BY participant_name"
            )]
            total = connection.execute("SELECT COUNT(*) FROM results" + where, parameters).fetchone()[0]
            sql = "SELECT * FROM results" + where + " ORDER BY created_at DESC, rowid DESC"
            if limit is not None:
                sql += " LIMIT ?"
                parameters.append(limit)
            rows = connection.execute(sql, parameters).fetchall()
            return {"records": [self._record(row) for row in rows], "participants": participants, "total": total}
