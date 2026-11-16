"""Local SQLite archive. HTTP clients never access this file directly."""

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from ..core.errors import LabError


def now():
    return datetime.now(UTC).isoformat()


def utc_timestamp(value):
    """Canonical aware ISO timestamp; preserve microseconds without SQLite rounding."""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError):
        return None


def default_path():
    """Use the team project's archive only; never inherit a personal database path."""
    configured = os.environ.get("TEAM_EDUCATION_DB")
    return (Path(configured).expanduser() if configured
            else Path(__file__).resolve().parents[2] / "instance" / "education.sqlite3")


class PracticeStore:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("utc_timestamp", 1, utc_timestamp, deterministic=True)
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("지원하지 않는 DB 버전입니다. 원본을 변경하지 않았습니다.")
            schemas = {
                "practices": {"id", "mode", "title", "created", "updated", "checkpoint", "digest", "snapshot"},
                "events": {"id", "practice_id", "at", "data"},
            }
            for table, expected in schemas.items():
                exists = self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
                ).fetchone()
                if exists:
                    columns = self.db.execute(f"PRAGMA table_info({table})").fetchall()
                    if ({row["name"] for row in columns} != expected
                            or not any(row["name"] == "id" and row["pk"] for row in columns)):
                        raise RuntimeError("교육용 저장 테이블 형식이 다릅니다. 원본을 변경하지 않았습니다.")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS practices (
                id TEXT PRIMARY KEY, mode TEXT NOT NULL, title TEXT NOT NULL,
                created TEXT NOT NULL, updated TEXT NOT NULL,
                checkpoint TEXT NOT NULL, digest TEXT NOT NULL, snapshot TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, practice_id TEXT NOT NULL,
                at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_practice ON events(practice_id, id);
            PRAGMA user_version=1;
            """)
        except Exception:
            self.db.close()
            raise

    def save(self, practice_id, mode, checkpoint, snapshot, events):
        checkpoint = json.dumps(checkpoint, ensure_ascii=False)
        digest = hashlib.sha256(checkpoint.encode()).hexdigest()
        stamp = now()
        try:
            with self.lock, (nullcontext() if self.db.in_transaction else self.db):
                self.db.execute(
                    """INSERT INTO practices VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,
                    checkpoint=excluded.checkpoint, digest=excluded.digest,
                    snapshot=excluded.snapshot
                    """,
                    (
                        practice_id,
                        mode,
                        "개인 실습" if mode == "personal" else "공동 수업",
                        stamp,
                        stamp,
                        checkpoint,
                        digest,
                        json.dumps(snapshot, ensure_ascii=False),
                    ),
                )
                self.db.executemany(
                    "INSERT INTO events(practice_id,at,data) VALUES (?,?,?)",
                    [
                        (practice_id, e.get("at", stamp), json.dumps(e, ensure_ascii=False))
                        for e in events
                    ],
                )
        except (sqlite3.Error, OSError) as error:
            raise LabError(
                "SAVE_FAILED",
                "저장되지 않았습니다. 실행을 멈췄습니다. "
                "디스크 공간과 DB 접근 권한을 확인한 뒤 다시 저장하세요.",
                503,
            ) from error
        return stamp

    @staticmethod
    def summary(raw_snapshot):
        try:
            snapshot = json.loads(raw_snapshot)
            lab = snapshot.get("lab", snapshot)
            nodes = lab.get("nodes", {}).values()
            chains = [node.get("chain", []) for node in nodes]
            transactions = {
                tx["id"]
                for chain in chains
                for block in chain
                for tx in block.get("transactions", [])
                if tx.get("kind", "regular") == "regular"
            }
            for node in lab.get("nodes", {}).values():
                transactions.update(tx["id"] for tx in node.get("mempool", []))
            return {
                "phase": lab.get("phase", "setup"),
                "height": max((len(chain) - 1 for chain in chains), default=0),
                "transactionCount": len(transactions),
                "walletCount": len(lab.get("wallets", [])),
            }
        except (ValueError, TypeError, AttributeError, KeyError):
            return None

    @staticmethod
    def filters(mode, q, updated_from, updated_until):
        clauses, values = ["mode=?", "instr(lower(title), lower(?)) > 0"], [mode, q]
        for bound, operator in ((updated_from, ">="), (updated_until, "<")):
            if bound is not None:
                stamp = utc_timestamp(bound)
                if stamp is None:
                    raise LabError(
                        "INVALID_DATE_RANGE", "시간대가 포함된 ISO 날짜를 지정하세요.", 422
                    )
                clauses.append(f"utc_timestamp(updated) {operator} ?")
                values.append(stamp)
        if updated_from is not None and updated_until is not None:
            if utc_timestamp(updated_from) >= utc_timestamp(updated_until):
                raise LabError(
                    "INVALID_DATE_RANGE", "시작 시각은 종료 시각보다 빨라야 합니다.", 422
                )
        return " AND ".join(clauses), values

    def list(self, mode, q="", limit=None, offset=0, updated_from=None, updated_until=None):
        where, values = self.filters(mode, q, updated_from, updated_until)
        with self.lock:
            rows = self.db.execute(
                "SELECT id, mode, title, created, updated, snapshot FROM practices "
                f"WHERE {where} ORDER BY utc_timestamp(updated) DESC, id LIMIT ? OFFSET ?",
                (*values, -1 if limit is None else limit, offset),
            )
            result = []
            for row in rows:
                item = dict(row)
                item["summary"] = self.summary(item.pop("snapshot"))
                result.append(item)
            return result

    def count(self, mode, q="", updated_from=None, updated_until=None):
        where, values = self.filters(mode, q, updated_from, updated_until)
        with self.lock:
            return self.db.execute(
                f"SELECT count(*) FROM practices WHERE {where}",
                values,
            ).fetchone()[0]

    def read(self, practice_id, mode):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM practices WHERE id=? AND mode=?", (practice_id, mode)
            ).fetchone()
            if not row:
                raise LabError("PRACTICE_NOT_FOUND", "저장된 실습을 찾을 수 없습니다.", 404)
            item = dict(row)
            if hashlib.sha256(item["checkpoint"].encode()).hexdigest() != item["digest"]:
                raise LabError(
                    "CHECKPOINT_INVALID",
                    "저장 데이터가 손상됐습니다. 원본은 변경하지 않았습니다.",
                    409,
                )
            item["checkpoint"] = json.loads(item["checkpoint"])
            item["snapshot"] = json.loads(item["snapshot"])
            item["events"] = [
                {**json.loads(r["data"]), "id": r["id"], "at": r["at"]}
                for r in self.db.execute(
                    "SELECT * FROM events WHERE practice_id=? ORDER BY id", (practice_id,)
                )
            ]
            return item

    def rename(self, practice_id, mode, title):
        self.read(practice_id, mode)
        with self.lock, self.db:
            self.db.execute("UPDATE practices SET title=? WHERE id=?", (title, practice_id))

    def delete(self, practice_id, mode):
        self.read(practice_id, mode)
        with self.lock, self.db:
            self.db.execute("DELETE FROM events WHERE practice_id=?", (practice_id,))
            self.db.execute("DELETE FROM practices WHERE id=?", (practice_id,))

    def backup(self, destination):
        with self.lock, closing(sqlite3.connect(str(destination))) as target, target:
            self.db.backup(target)

    def restore_backup(self, source):
        # Validate the entire candidate before touching the live database.
        from .checkpoint import restore
        from .session import LabSession

        with closing(sqlite3.connect(
            f"file:{Path(source).resolve().as_posix()}?mode=ro", uri=True
        )) as candidate:
            if (
                candidate.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or candidate.execute("PRAGMA user_version").fetchone()[0] != 1
            ):
                raise ValueError("지원하지 않거나 손상된 백업입니다.")
            for payload, digest in candidate.execute("SELECT checkpoint,digest FROM practices"):
                if hashlib.sha256(payload.encode()).hexdigest() != digest:
                    raise ValueError("백업 검증에 실패했습니다.")
                data = json.loads(payload)
                restore(LabSession(str(uuid.uuid4())), data["session"])
            with self.lock:
                candidate.backup(self.db)
