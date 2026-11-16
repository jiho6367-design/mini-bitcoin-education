"""Stable, teacherless team links backed by the education archive.

Only an explicitly registered room is public. Browser ownership hashes identify
members across restarts; bearer tokens and live node claims remain in memory.
Ordinary start rejoins the default room. Explicit new practices have independent
links and ledgers; creating one never replaces an earlier link.
"""

import copy
import json
import re
import sqlite3
from collections import OrderedDict
from contextlib import contextmanager

from education_engine.core.errors import LabError
from education_engine.service.autosave import persist_room
from education_engine.service.checkpoint import restore
from education_engine.service.classroom import Classroom
from education_engine.service.storage import now


_CODE = re.compile(r"[A-HJ-NP-Z2-9]{8}\Z", re.ASCII)
MAX_ACTIVE_TEAM_ROOMS = 4


class TeamRooms:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.store
        self.hub = runtime.hub
        self.postgres = bool(getattr(self.store, "is_postgres", False))
        self.rooms = self.store.backend.table("education", "team_rooms") if self.postgres else "team_rooms"
        self.members = self.store.backend.table("education", "team_members") if self.postgres else "team_members"
        self.practices = self.store.practices if self.postgres else "practices"
        self.extra_rooms = OrderedDict()
        self._initialize()

    @contextmanager
    def _transaction(self, write=False):
        if self.postgres:
            # Reuse the native store's connection, nested transaction and writer
            # fence. A reconnect must never reclaim a newer deployment's fence.
            with self.store._transaction(write=write) as connection:
                yield connection
            return
        with self.store.lock:
            connection = self.store.db
            nested = False
            try:
                nested = connection.in_transaction
                if nested:
                    connection.execute("SAVEPOINT team_room_transaction")
                else:
                    connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection
                if nested:
                    connection.execute("RELEASE SAVEPOINT team_room_transaction")
                else:
                    connection.commit()
            except Exception as error:
                try:
                    if nested:
                        connection.execute("ROLLBACK TO SAVEPOINT team_room_transaction")
                        connection.execute("RELEASE SAVEPOINT team_room_transaction")
                    else:
                        connection.rollback()
                except sqlite3.Error:
                    # A lost/closed connection can also reject rollback. Report
                    # the original storage failure with the normal retry status.
                    pass
                if isinstance(error, (sqlite3.Error, OSError)):
                    raise LabError(
                        "SAVE_FAILED" if write else "READ_FAILED",
                        "팀 실습 저장소에 연결할 수 없습니다. 잠시 후 다시 시도하세요.", 503,
                    ) from None
                raise

    def _execute(self, connection, sql, values=()):
        return connection.execute(sql.replace("?", "%s") if self.postgres else sql, values)

    def _initialize(self):
        schemas = {
            "team_rooms": (("code", "practice_id", "created"), ("code",)),
            "team_members": (("code", "owner_hash", "participant_id", "name"), ("code", "owner_hash")),
        }
        with self._transaction(write=True) as connection:
            for table, (columns, primary) in schemas.items():
                if self.postgres:
                    self.store.backend.validate_table(
                        connection, "education", table, {column: "text" for column in columns},
                        primary_key=primary,
                    )
                else:
                    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
                    if rows and (
                        {row["name"] for row in rows} != set(columns)
                        or tuple(row["name"] for row in sorted(rows, key=lambda r: r["pk"]) if row["pk"]) != primary
                        or any(row["type"].upper() != "TEXT" for row in rows)
                    ):
                        raise LabError("CHECKPOINT_INVALID", "팀 저장 테이블 형식이 다릅니다. 원본을 유지했습니다.", 409)
            connection.execute(f"""CREATE TABLE IF NOT EXISTS {self.rooms} (
                code TEXT PRIMARY KEY, practice_id TEXT NOT NULL UNIQUE, created TEXT NOT NULL
            )""")
            connection.execute(f"""CREATE TABLE IF NOT EXISTS {self.members} (
                code TEXT NOT NULL, owner_hash TEXT NOT NULL,
                participant_id TEXT NOT NULL, name TEXT NOT NULL,
                PRIMARY KEY (code, owner_hash), UNIQUE (code, participant_id)
            )""")

    @staticmethod
    def _code(code):
        code = code.strip().upper() if isinstance(code, str) else ""
        if not _CODE.fullmatch(code):
            raise LabError("TEAM_ROOM_NOT_FOUND", "팀 실습 링크를 찾을 수 없습니다.", 404)
        return code

    def _mapping(self, code, connection=None):
        if connection is None:
            with self._transaction() as connection:
                return self._mapping(code, connection)
        row = self._execute(connection, f"SELECT * FROM {self.rooms} WHERE code=?", (code,)).fetchone()
        return dict(row) if row is not None else None

    def is_team(self, code):
        try:
            return self._mapping(self._code(code)) is not None
        except LabError as error:
            if error.code == "TEAM_ROOM_NOT_FOUND":
                return False
            raise

    def default_code(self):
        with self._transaction() as connection:
            row = connection.execute(f"SELECT code FROM {self.rooms} ORDER BY created, code LIMIT 1").fetchone()
            return row["code"] if row is not None else None

    def is_practice(self, practice_id):
        """Protect the shared checkpoint from private archive delete/resume APIs."""
        with self._transaction() as connection:
            return self._execute(connection, f"SELECT 1 FROM {self.rooms} WHERE practice_id=?",
                                 (practice_id,)).fetchone() is not None

    def _restore(self, item, code):
        try:
            if item["snapshot"].get("closed"):
                raise LabError("ROOM_GONE", "종료된 팀 실습입니다.", 410)
            room = Classroom("팀", 1, self.hub.clock, team_mode=True, code=code)
            # The constructor's participant is not an owned browser identity.
            room.people.clear()
            room.teacher = None
            room.pending_events = []
            restore(room.session, item["checkpoint"]["session"])
            room.practice_id = item["id"]
            room.store = self.store
            room.saved_at = item["updated"]
            room.limited = bool(item["checkpoint"].get("limited", False))
            room.activity = copy.deepcopy(item["checkpoint"].get("activity", []))[-100:]
            room.seq = max(item["snapshot"].get("seq", 0),
                           max((entry.get("seq", 0) for entry in room.activity), default=0))
            room.automatic = False
            if room.session.race:
                room.session.race.enabled = {node: False for node in room.session.race.enabled}
                room.session.race.target_reward = None
            return room
        except LabError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
            raise LabError("CHECKPOINT_INVALID", "팀 실습 저장 데이터가 손상됐습니다. 원본을 유지했습니다.", 409) from None

    def _load(self, practice_id):
        try:
            return self.store.read(practice_id, "classroom")
        except LabError as error:
            if error.code == "PRACTICE_NOT_FOUND":
                raise LabError("ROOM_GONE", "저장된 팀 실습이 삭제됐습니다.", 410) from None
            raise
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
            raise LabError("CHECKPOINT_INVALID", "팀 실습 저장 데이터가 손상됐습니다. 원본을 유지했습니다.", 409) from None

    def get(self, code):
        code = self._code(code)
        with self.runtime.creation_lock, self.hub.lock:
            mapping = self._mapping(code)
            if mapping is None:
                raise LabError("TEAM_ROOM_NOT_FOUND", "팀 실습 링크를 찾을 수 없습니다.", 404)
            current = self.hub.room
            if current is not None and current.code == code:
                if current.closed:
                    raise LabError("ROOM_GONE", "종료된 팀 실습입니다.", 410)
                if current.team_mode:
                    return current
            if code in self.extra_rooms:
                self.extra_rooms.move_to_end(code)
                return self.extra_rooms[code]
            if current is not None and not current.closed and not current.team_mode:
                raise LabError("ROOM_EXISTS", "다른 실습이 진행 중입니다. 잠시 후 다시 접속하세요.", 409)
            room = self._restore(self._load(mapping["practice_id"]), code)
            if code == self.default_code():
                self.hub.room = room
            else:
                self._reserve_extra_slot()
                self.extra_rooms[code] = room
            return room

    def active_rooms(self):
        """A bounded snapshot for the runner, including a legacy primary room."""
        with self.runtime.creation_lock, self.hub.lock:
            return tuple(room for room in (self.hub.room, *self.extra_rooms.values())
                         if room is not None and not room.closed)

    def _reserve_extra_slot(self):
        # Keep the default room outside this cache so a new practice cannot evict
        # the original shared link. All callers hold creation_lock and hub.lock.
        if len(self.extra_rooms) < MAX_ACTIVE_TEAM_ROOMS - 1:
            return
        for code, room in tuple(self.extra_rooms.items()):
            with room.lock:
                if any(person.connections for person in room.people.values()):
                    continue
                room.tick()  # Durable pause; no connected participant can be displaced.
                if room.storage_error:
                    continue
                persist_room(room, [])
                # Retire only this in-memory instance after its open checkpoint
                # is saved. Any request holding an old reference must fail closed.
                room.closed = True
                room.store = None
                del self.extra_rooms[code]
                return
        raise LabError("ACTIVE_ROOM_LIMIT", "동시에 진행 중인 실습이 많습니다 잠시 후 다시 시도하세요", 429)

    def _member(self, code, owner_hash, connection):
        row = self._execute(connection, f"SELECT * FROM {self.members} WHERE code=? AND owner_hash=?",
                            (code, owner_hash)).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 30:
            raise LabError("INVALID_NAME", "별명을 1~30자로 입력하세요", 400)
        return name.strip()

    def _save_name(self, connection, room, person, owner_hash, name):
        self._execute(connection, f"""UPDATE {self.members} SET name=?
            WHERE code=? AND owner_hash=? AND participant_id=?""",
            (name, room.code, owner_hash, person.id))
        person.name = name

    def _join(self, room, name, owner_hash, *, reconnect=False):
        with room.lock:
            if room.closed or room.store is None:
                raise LabError("STALE_ROOM", "실습 연결이 갱신됐습니다 같은 링크로 다시 입장하세요", 409)
            if not reconnect:
                name = self._name(name)
            # Keep the room lock outside the store lock: the simulation runner
            # writes checkpoints while holding the same room lock.
            before = {key: copy.deepcopy(getattr(room, key)) for key in
                      ("seq", "activity", "pending_events", "saved_at", "storage_error")}
            previous_people = dict(room.people)
            previous_names = {person.id: person.name for person in room.people.values()}
            previous_teacher = room.teacher
            try:
                with self._transaction(write=True) as connection:
                    member = self._member(room.code, owner_hash, connection)
                    if member is not None and member["participant_id"] in room.people:
                        person = room.people[member["participant_id"]]
                        if not reconnect and (person.name != name or member["name"] != name):
                            self._save_name(connection, room, person, owner_hash, name)
                            room.note(name, "별명 변경")
                            persist_room(room, room.pending_events)
                            room.pending_events = []
                        return room, person
                    if member is None and reconnect:
                        raise LabError("MEMBERSHIP_REQUIRED", "이 브라우저의 참가 기록이 없습니다. 이름을 입력해 참여하세요.", 404)
                    # The ordinary add implementation runs inside this service's
                    # transaction, so member identity and checkpoint commit once.
                    person = Classroom.add.__wrapped__(room, member["name"] if reconnect else name)
                    if member is not None:
                        del room.people[person.id]
                        person.id = member["participant_id"]
                        room.people[person.id] = person
                        if not reconnect and member["name"] != name:
                            self._save_name(connection, room, person, owner_hash, name)
                    else:
                        self._execute(connection, f"""INSERT INTO {self.members}
                            (code,owner_hash,participant_id,name) VALUES (?,?,?,?)""",
                            (room.code, owner_hash, person.id, person.name))
                    if room.teacher is None:
                        room.teacher = person
                    persist_room(room, room.pending_events)
                    room.pending_events = []
                return room, person
            except Exception:
                room.__dict__.update(before)
                room.people = previous_people
                for person in room.people.values():
                    person.name = previous_names[person.id]
                room.teacher = previous_teacher
                raise

    def rename(self, room, person, owner_hash, name):
        """Change only this browser's member name, together with its checkpoint."""
        name = self._name(name)
        with room.lock:
            if room.closed or room.store is None:
                raise LabError("STALE_ROOM", "실습 연결이 갱신됐습니다 같은 링크로 다시 입장하세요", 409)
            before = {key: copy.deepcopy(getattr(room, key)) for key in
                      ("seq", "activity", "pending_events", "saved_at", "storage_error")}
            previous_name = person.name
            try:
                with self._transaction(write=True) as connection:
                    member = self._member(room.code, owner_hash, connection)
                    if (not room.team_mode or room.people.get(person.id) is not person
                            or member is None or member["participant_id"] != person.id):
                        raise LabError("FORBIDDEN", "내 별명만 변경할 수 있습니다", 403)
                    if person.name == name and member["name"] == name:
                        return person.name
                    self._save_name(connection, room, person, owner_hash, name)
                    room.note(name, "별명 변경")
                    persist_room(room, room.pending_events)
                    room.pending_events = []
                return person.name
            except Exception:
                person.name = previous_name
                room.__dict__.update(before)
                raise

    def join(self, code, name, owner_hash):
        with self.runtime.creation_lock, self.hub.lock:
            return self._join(self.get(code), name, owner_hash)

    def reconnect(self, code, owner_hash):
        with self.runtime.creation_lock, self.hub.lock:
            return self._join(self.get(code), "", owner_hash, reconnect=True)

    def create(self, name, difficulty, owner_hash, *, initial_balance=100, new_practice=False):
        with self.runtime.creation_lock, self.hub.lock:
            existing = self.default_code()
            if existing is not None and not new_practice:
                return self.join(existing, name, owner_hash)
            if self.hub.room is not None and not self.hub.room.closed and (
                existing is None or not self.hub.room.team_mode
            ):
                raise LabError("ROOM_EXISTS", "진행 중인 실습이 있습니다.", 409)
            room = Classroom(name, difficulty, self.hub.clock, team_mode=True,
                             initial_balance=initial_balance)
            if existing is not None:
                self._reserve_extra_slot()
            person = room.teacher
            room.store = self.store
            with room.lock, self._transaction(write=True) as connection:
                self._execute(connection, f"INSERT INTO {self.rooms}(code,practice_id,created) VALUES (?,?,?)",
                              (room.code, room.practice_id, now()))
                self._execute(connection, f"""INSERT INTO {self.members}
                    (code,owner_hash,participant_id,name) VALUES (?,?,?,?)""",
                    (room.code, owner_hash, person.id, person.name))
                persist_room(room, room.pending_events)
                room.pending_events = []
            if existing is None:
                self.hub.room = room
            else:
                self.extra_rooms[room.code] = room
            return room, person

    def migrate_existing(self, code):
        """Register only the exact legacy code explicitly selected by deployment.

        Call after the shared PostgreSQL writer is activated, before serving
        requests. Historical participant names never become live credentials.
        """
        code = self._code(code)
        with self.runtime.creation_lock, self.hub.lock:
            if self._mapping(code) is not None:
                return True
            if self.default_code() is not None:
                raise LabError("ROOM_EXISTS", "이미 공유 팀 실습이 등록돼 있습니다.", 409)
            with self._transaction() as connection:
                rows = self._execute(connection, f"SELECT id,snapshot FROM {self.practices} WHERE mode=?",
                                     ("classroom",)).fetchall()
            matches = []
            for row in rows:
                try:
                    if json.loads(row["snapshot"]).get("code") == code:
                        matches.append(row["id"])
                except (ValueError, TypeError, AttributeError):
                    continue
            if not matches:
                return False
            if len(matches) != 1:
                raise LabError("CHECKPOINT_INVALID", "같은 링크의 저장 기록이 여러 개여서 자동 연결하지 않았습니다.", 409)
            current = self.hub.room
            if current is not None and not current.closed and current.practice_id != matches[0]:
                raise LabError("ROOM_EXISTS", "다른 실습이 진행 중입니다.", 409)
            room = self._restore(self._load(matches[0]), code)
            with room.lock, self._transaction(write=True) as connection:
                self._execute(connection, f"INSERT INTO {self.rooms}(code,practice_id,created) VALUES (?,?,?)",
                              (code, room.practice_id, now()))
                room.note("팀", "기존 기록을 팀 실습으로 연결")
                persist_room(room, room.pending_events)
                room.pending_events = []
            self.hub.room = room
            return True
