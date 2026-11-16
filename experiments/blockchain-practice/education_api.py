"""Flask transport for the team's UTXO lab, archives and shared classroom.

The original account-model API and its database stay separate. Personal archives
are scoped to an opaque browser cookie; classroom authority uses temporary tokens.
"""
import atexit
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from flask import Blueprint, Response, g, jsonify, request
from flask_sock import Sock
from pydantic import BaseModel, Field, ValidationError
from simple_websocket import ConnectionClosed
from werkzeug.exceptions import HTTPException

from education_engine import __version__
from education_engine.api.schemas import (
    CreateLabRequest, DraftRequest, MiningRequest, RaceRequest, SignRequest,
    StartLabRequest, TamperRequest, TransactionRequest,
)
from education_engine.core.errors import LabError, LabNotFound
from education_engine.core.types import NODE_IDS
from education_engine.service.archive_html import render
from education_engine.service.autosave import persist_room, persist_session
from education_engine.service.checkpoint import restore
from education_engine.service.classroom import Classroom, ClassroomHub, changes
from education_engine.service.registry import LabRegistry
from education_engine.service.storage import PracticeStore

PREFIX = "/api/education"
OWNER_COOKIE = "team_education_owner"


class CreateRoom(BaseModel):
    secret: str = Field(max_length=100)
    name: str = Field(min_length=1, max_length=30, pattern=r".*\S.*")
    difficulty: int = Field(default=1, ge=1, le=4)


class JoinRoom(BaseModel):
    name: str = Field(min_length=1, max_length=30, pattern=r".*\S.*")


class CreateTeamRoom(JoinRoom):
    difficulty: int = Field(default=1, ge=1, le=4)
    initialBalance: int = Field(default=100, strict=True, ge=1, le=10000)
    newPractice: bool = Field(default=False, strict=True)


class RoomCommand(BaseModel):
    runId: str = Field(max_length=50)
    assignmentId: str | None = Field(default=None, max_length=50)
    commandId: str = Field(min_length=8, max_length=64)
    action: Literal[
        "leave", "close", "remove", "assign", "draft", "sign", "submit",
        "participate", "prepare", "fork", "resume", "pause", "stop", "step",
        "configure", "advance", "deliver", "automatic", "claim", "release",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class ArchiveAction(BaseModel):
    title: str = Field(default="", max_length=100)
    name: str = Field(default="교사", min_length=1, max_length=30)


class ArchiveQuery(BaseModel):
    q: str = Field(default="", max_length=100)
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    updatedFrom: str | None = None
    updatedUntil: str | None = None


def credential(room, person):
    return {"code": room.code, "runId": room.run_id,
            "participantId": person.id, "token": person.token,
            "teamMode": bool(getattr(room, "team_mode", False))}


class PersonalOwnership:
    """Durable ownership metadata shares the team's archive database and lock."""
    def __init__(self, store):
        self.store = store
        if hasattr(store, "owner_secret"):
            self.secret = store.owner_secret()
            return
        with store.lock, store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS team_education_owners (
                practice_id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL
            )""")
            store.db.execute("CREATE INDEX IF NOT EXISTS education_owner_lookup "
                             "ON team_education_owners(owner_hash)")
            store.db.execute("CREATE TABLE IF NOT EXISTS team_education_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            store.db.execute("INSERT OR IGNORE INTO team_education_meta VALUES ('owner_cookie_secret',?)",
                             (secrets.token_hex(32),))
            self.secret = bytes.fromhex(store.db.execute(
                "SELECT value FROM team_education_meta WHERE key='owner_cookie_secret'",
            ).fetchone()[0])

    def cookie(self, incoming):
        if isinstance(incoming, str):
            token, separator, signature = incoming.partition(".")
            if (separator and re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
                    and re.fullmatch(r"[0-9a-f]{64}", signature)
                    and hmac.compare_digest(signature, hmac.new(self.secret, token.encode(), hashlib.sha256).hexdigest())):
                return incoming
        token = secrets.token_urlsafe(32)
        return token + "." + hmac.new(self.secret, token.encode(), hashlib.sha256).hexdigest()

    def assign(self, practice_id, owner_hash):
        if hasattr(self.store, "owner_assign"):
            return self.store.owner_assign(practice_id, owner_hash)
        try:
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT INTO team_education_owners VALUES (?,?)",
                                      (practice_id, owner_hash))
        except sqlite3.Error as error:
            raise LabError("SAVE_FAILED", "실습 소유 정보를 저장하지 못했습니다", 503) from error

    def owns(self, practice_id, owner_hash):
        if hasattr(self.store, "owner_owns"):
            return self.store.owner_owns(practice_id, owner_hash)
        with self.store.lock:
            return self.store.db.execute(
                "SELECT 1 FROM team_education_owners WHERE practice_id=? AND owner_hash=?",
                (practice_id, owner_hash),
            ).fetchone() is not None

    def remove(self, practice_id):
        if hasattr(self.store, "owner_remove"):
            return self.store.owner_remove(practice_id)
        with self.store.lock, self.store.db:
            self.store.db.execute("DELETE FROM team_education_owners WHERE practice_id=?", (practice_id,))

    def list(self, owner_hash, query):
        if hasattr(self.store, "owner_list"):
            return self.store.owner_list(owner_hash, query)
        where, values = self.store.filters(
            "personal", query.q.strip(), query.updatedFrom, query.updatedUntil,
        )
        sql = (" FROM practices p JOIN team_education_owners o ON o.practice_id=p.id "
               f"WHERE {where} AND o.owner_hash=?")
        with self.store.lock:
            total = self.store.db.execute("SELECT count(*)" + sql, (*values, owner_hash)).fetchone()[0]
            rows = self.store.db.execute(
                "SELECT p.id,p.mode,p.title,p.created,p.updated,p.snapshot" + sql
                + " ORDER BY utc_timestamp(updated) DESC,p.id LIMIT ? OFFSET ?",
                (*values, owner_hash, query.limit, query.offset),
            )
            items = []
            for row in rows:
                item = dict(row)
                item["summary"] = self.store.summary(item.pop("snapshot"))
                items.append(item)
        return {"practices": items, "total": total}


class EducationRuntime:
    def __init__(self, app, database_path):
        self.app = app
        backend = app.extensions.get("postgres_backend")
        if backend is not None:
            from postgres_education_store import PostgresEducationStore
            self.store = PostgresEducationStore(backend)
        else:
            self.store = PracticeStore(database_path)
        self.ownership = PersonalOwnership(self.store)
        self.registry = LabRegistry(max_labs=app.config.get("EDUCATION_MAX_ACTIVE_LABS", 32), store=self.store)
        self.hub = ClassroomHub(app.config.get("TEAM_TEACHER_SECRET")
                                or os.environ.get("TEAM_TEACHER_SECRET"), store=self.store)
        self.creation_lock = threading.RLock()
        from team_rooms import TeamRooms
        self.team_rooms = TeamRooms(self)
        self.stop_event = threading.Event()
        self.runner = None
        self._closed = False
        self._atexit_registered = False

    @staticmethod
    def publish(room):
        with room.lock:
            if getattr(room, "published", {}).get("seq") != room.seq:
                room.published = room.snapshot()

    def run_once(self):
        for room in self.team_rooms.active_rooms():
            if room.closed:
                continue
            with room.lock:
                try:
                    room.tick()
                except LabError as error:
                    if error.code != "SAVE_FAILED":
                        raise
                self.publish(room)

    def start(self):
        if self.runner is not None:
            return
        def run():
            while not self.stop_event.wait(0.25):
                try:
                    self.run_once()
                except Exception:
                    self.app.logger.exception("Shared classroom runner failed")
        self.runner = threading.Thread(target=run, name="team-classroom", daemon=True)
        self.runner.start()

    def close(self):
        self.stop_event.set()
        if self.runner is not None and threading.current_thread() is not self.runner:
            self.runner.join(timeout=2)
        with self.store.lock:
            if self._closed:
                return
            self._closed = True
            if hasattr(self.store, "close"):
                self.store.close()
            else:
                self.store.db.close()
            if self._atexit_registered:
                atexit.unregister(self.close)
                self._atexit_registered = False


def register_education(app, database_path, *, start_runner=None, classroom_enabled=True):
    """Register namespaced routes once; return an explicitly closable runtime."""
    if "education" in app.extensions:
        raise RuntimeError("Education API is already registered")
    runtime = EducationRuntime(app, database_path)
    app.extensions["education"] = runtime
    store, registry, hub = runtime.store, runtime.registry, runtime.hub
    bp = Blueprint("team_education", __name__, url_prefix=PREFIX)

    def error(status, code, message):
        return jsonify({"error": {"code": code, "message": message}}), status

    @bp.errorhandler(LabError)
    def lab_error(exc):
        return error(exc.status, exc.code, exc.message)

    @bp.errorhandler(ValidationError)
    def validation_error(_exc):
        return error(400, "INVALID_REQUEST", "요청 형식이 올바르지 않습니다")

    @bp.errorhandler(HTTPException)
    def http_error(exc):
        return error(exc.code, "HTTP_ERROR", exc.description)

    @bp.before_request
    def protect_request():
        origin = request.headers.get("Origin")
        if origin and (request.method in ("POST", "DELETE", "PUT", "PATCH")
                       or request.headers.get("Upgrade", "").lower() == "websocket"):
            if not same_origin(origin):
                raise LabError("ORIGIN_FORBIDDEN", "현재 사이트에서 다시 요청하세요", 403)
        if request.path.startswith(PREFIX + "/classroom"):
            if not classroom_enabled:
                raise LabError("CLASSROOM_DISABLED", "공동 수업을 사용할 수 없습니다", 404)
            return
        if request.path.startswith(PREFIX + "/team") and not classroom_enabled:
            raise LabError("CLASSROOM_DISABLED", "공동 실습을 사용할 수 없습니다", 404)
        if request.path == PREFIX + "/health":
            return
        choose_owner()

    def same_origin(origin):
        try:
            parsed = urlsplit(origin)
            return (parsed.scheme == request.scheme and parsed.netloc.lower() == request.host.lower()
                    and not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:
            return False

    def choose_owner():
        token = runtime.ownership.cookie(request.cookies.get(OWNER_COOKIE))
        if token != request.cookies.get(OWNER_COOKIE):
            g.education_new_owner = token
        g.education_owner = hashlib.sha256(token.encode()).hexdigest()

    def owner_cookie(response):
        token = getattr(g, "education_new_owner", None)
        if token:
            response.set_cookie(
                OWNER_COOKIE, token, max_age=365 * 24 * 60 * 60, httponly=True,
                secure=app.config.get("EDUCATION_COOKIE_SECURE", request.is_secure),
                samesite="Lax", path="/",
            )
        return response

    @app.after_request
    def bootstrap_owner(response):
        # Seed the cookie with the page itself, before parallel React API calls.
        if (request.path in ("/", "/index.html", "/advanced") and 200 <= response.status_code < 300
                and response.mimetype == "text/html"):
            choose_owner()
            owner_cookie(response)
            response.headers["Cache-Control"] = "no-store"
        return response

    @bp.after_request
    def protect_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return owner_cookie(response)

    def body(model, *, empty=False):
        raw = request.get_data()
        if empty and not raw:
            return model.model_validate({})
        if len(raw) > 32768:
            raise LabError("BODY_TOO_LARGE", "요청이 너무 큽니다", 413)
        if not request.is_json:
            raise LabError("INVALID_REQUEST", "JSON 형식으로 요청하세요", 400)
        try:
            data = request.get_json(silent=True)
        except (ValueError, RecursionError, OverflowError) as exc:
            raise LabError("INVALID_REQUEST", "JSON을 해석할 수 없습니다", 400) from exc
        if not isinstance(data, dict):
            raise LabError("INVALID_REQUEST", "JSON 객체를 전달하세요", 400)
        return model.model_validate(data)

    def owned(practice_id):
        if not runtime.ownership.owns(practice_id, g.education_owner):
            raise LabError("PRACTICE_NOT_FOUND", "저장된 실습을 찾을 수 없습니다", 404)

    def session(lab_id):
        found = registry.get(lab_id)
        if not runtime.ownership.owns(found.practice_id, g.education_owner):
            raise LabNotFound()
        return found

    def nid(value):
        if value not in NODE_IDS:
            raise LabError("INVALID_REQUEST", "노드를 확인하세요", 400)
        return value

    def personal_action(lab_id, method, *args):
        found = session(lab_id)
        with found._lock:
            command = args[1] if method == "mining_command" else None
            race_command = args[0].action if method == "race_action" else None
            relief = (method == "deliver_next" or command in ("pause", "stop")
                      or race_command in ("pause", "stop", "configure", "toggle"))
            if not relief:
                with store.lock:
                    count = (store.event_count(found.practice_id) if hasattr(store, "event_count") else
                             store.db.execute("SELECT count(*) FROM events WHERE practice_id=?",
                                              (found.practice_id,)).fetchone()[0])
                if count >= app.config.get("EDUCATION_MAX_PERSONAL_EVENTS", 5000):
                    raise LabError("EVENT_LIMIT", "활동 기록 한도입니다. 결과를 저장하고 새 실습을 시작하세요", 409)
            if method == "add_wallet" and len(found.wallets) >= app.config.get("EDUCATION_MAX_WALLETS", 100):
                raise LabError("WALLET_LIMIT", "지갑 한도입니다. 기존 지갑을 사용하거나 새 실습을 시작하세요", 409)
            lab = found.lab
            if lab:
                computes = (method == "start_mining" or command in ("tick", "step", "resume")
                            or race_command in ("prepare", "tick", "step", "resume", "advance"))
                if computes and max(len(node.blocks) for node in lab.nodes.values()) >= app.config.get("EDUCATION_MAX_CHAIN_BLOCKS", 500):
                    raise LabError("CHAIN_LIMIT", "블록 한도입니다. 결과를 저장하고 새 실습을 시작하세요", 409)
                if method == "submit" and len(lab.nodes[args[0]].mempool) >= app.config.get("EDUCATION_MAX_PENDING_TRANSACTIONS", 300):
                    raise LabError("MEMPOOL_LIMIT", "대기 거래 한도입니다. 기존 거래를 먼저 채굴하세요", 409)
                if (method == "submit" or computes) and len(lab.queue) >= app.config.get("EDUCATION_MAX_PENDING_MESSAGES", 300):
                    raise LabError("QUEUE_LIMIT", "전파 대기 한도입니다. 메시지를 먼저 전달하세요", 409)
            return getattr(found, method)(*args)

    def creation_limit(*, personal=False):
        with store.lock:
            total = (store.total_count() if hasattr(store, "total_count") else
                     store.db.execute("SELECT count(*) FROM practices").fetchone()[0])
            if total >= app.config.get("EDUCATION_MAX_PRACTICES", 2000):
                raise LabError("ARCHIVE_LIMIT", "저장 한도입니다. 불필요한 기록을 정리한 뒤 다시 시작하세요", 429)
            if personal:
                count = store.owner_count(g.education_owner) if hasattr(store, "owner_count") else store.db.execute(
                    "SELECT count(*) FROM practices p JOIN team_education_owners o "
                    "ON p.id=o.practice_id WHERE o.owner_hash=?", (g.education_owner,),
                ).fetchone()[0]
                if count >= app.config.get("EDUCATION_MAX_PRACTICES_PER_OWNER", 200):
                    raise LabError("ARCHIVE_LIMIT", "개인 저장 한도입니다. 이전 기록을 정리한 뒤 다시 시작하세요", 429)

    @bp.get("/health")
    def health():
        return {"status": "ok", "version": app.config.get("TEAM_APP_VERSION", "3.0.0"),
                "engineVersion": __version__, "engine": "team-utxo"}

    @bp.post("/labs")
    def create_lab():
        data = body(CreateLabRequest)
        with runtime.creation_lock, registry._lock:
            creation_limit(personal=True)
            created = registry.create()
            try:
                runtime.ownership.assign(created.practice_id, g.education_owner)
                result = created.start(data.difficulty, example=True) if data.mode == "example" else {"lab": created.snapshot()}
            except Exception:
                # This new ID was never returned. Retire it without another save,
                # then remove its initial checkpoint if creation did not finish.
                created.retired = True
                registry._labs.pop(created.lab_id, None)
                try:
                    store.delete(created.practice_id, "personal")
                    runtime.ownership.remove(created.practice_id)
                except (LabError, sqlite3.Error):
                    app.logger.exception("Could not clean up an unfinished new practice")
                raise
        return result, 201

    @bp.get("/labs/<lab_id>")
    def read_lab(lab_id):
        return {"lab": session(lab_id).snapshot()}

    @bp.delete("/labs/<lab_id>")
    def delete_lab(lab_id):
        session(lab_id)
        registry.delete(lab_id)
        return Response(status=204)

    @bp.post("/labs/<lab_id>/wallets")
    def add_wallet(lab_id):
        return personal_action(lab_id, "add_wallet")

    @bp.get("/labs/<lab_id>/wallets/<address>/private-key")
    def private_key(lab_id, address):
        return session(lab_id).private_key(address)

    @bp.post("/labs/<lab_id>/start")
    def start_lab(lab_id):
        return personal_action(lab_id, "start", body(StartLabRequest).difficulty)

    @bp.post("/labs/<lab_id>/nodes/<node_id>/drafts")
    def draft(lab_id, node_id):
        data = body(DraftRequest)
        return personal_action(lab_id, "draft", nid(node_id), data.senderAddress, data.recipientAddress,
                               data.amount, data.fee, data.selectedInputs)

    @bp.post("/labs/<lab_id>/wallets/sign")
    def sign(lab_id):
        return personal_action(lab_id, "sign", body(SignRequest).draft)

    @bp.post("/labs/<lab_id>/nodes/<node_id>/transactions")
    def submit(lab_id, node_id):
        return personal_action(lab_id, "submit", nid(node_id), body(TransactionRequest).transaction)

    @bp.post("/labs/<lab_id>/messages/deliver-next")
    def deliver_next(lab_id):
        return personal_action(lab_id, "deliver_next")

    @bp.post("/labs/<lab_id>/race")
    def race(lab_id):
        return personal_action(lab_id, "race_action", body(RaceRequest))

    @bp.post("/labs/<lab_id>/mining")
    def start_mining(lab_id):
        data = body(MiningRequest)
        return personal_action(lab_id, "start_mining", data.nodeId, data.mode, data.step)

    @bp.post("/labs/<lab_id>/mining/<job_id>/<command>")
    def mining_command(lab_id, job_id, command):
        if command not in ("tick", "pause", "resume", "step", "stop"):
            raise LabError("INVALID_REQUEST", "채굴 명령을 확인하세요", 400)
        return personal_action(lab_id, "mining_command", job_id, command)

    @bp.post("/labs/<lab_id>/nodes/<node_id>/experiments/forged-signature")
    def forged_signature(lab_id, node_id):
        return personal_action(lab_id, "forged_signature", nid(node_id), body(TransactionRequest).transaction)

    @bp.post("/labs/<lab_id>/nodes/<node_id>/experiments/double-spend")
    def double_spend(lab_id, node_id):
        return personal_action(lab_id, "double_spend", nid(node_id))

    @bp.post("/labs/<lab_id>/nodes/<node_id>/experiments/invalid-pow-block")
    def invalid_pow(lab_id, node_id):
        return personal_action(lab_id, "invalid_pow_block", nid(node_id))

    @bp.post("/labs/<lab_id>/nodes/<node_id>/experiments/tamper")
    def tamper(lab_id, node_id):
        data = body(TamperRequest)
        return personal_action(lab_id, "tamper", nid(node_id), data.height, data.mode, data.amount, data.recomputeHashes)

    def archive_auth(classroom):
        if not classroom:
            return
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token and secrets.compare_digest(token.encode(), hub.secret.encode()):
            return
        if token and hub.room and not hub.room.closed:
            with hub.room.lock:
                person = hub.room.auth(token)
                if person.role == "teacher" and not getattr(hub.room, "team_mode", False):
                    return
        raise LabError("FORBIDDEN", "교사용 비밀코드로 저장된 수업을 열어 주세요", 403)

    def archives(classroom):
        mode = "classroom" if classroom else "personal"
        base = "/classroom/practices" if classroom else "/practices"
        label = "classroom" if classroom else "personal"

        def checked(practice_id):
            archive_auth(classroom)
            if not classroom:
                owned(practice_id)

        def listing():
            archive_auth(classroom)
            query = ArchiveQuery.model_validate(request.args.to_dict())
            if not classroom:
                return runtime.ownership.list(g.education_owner, query)
            with store.lock:
                return {"practices": store.list(mode, query.q.strip(), query.limit, query.offset, query.updatedFrom, query.updatedUntil),
                        "total": store.count(mode, query.q.strip(), query.updatedFrom, query.updatedUntil)}

        def read(practice_id):
            checked(practice_id)
            item = store.read(practice_id, mode)
            item.pop("checkpoint")
            item.pop("digest")
            return item

        def export(practice_id):
            checked(practice_id)
            return Response(render(store.read(practice_id, mode)), mimetype="text/html",
                            headers={"Content-Disposition": f'attachment; filename="practice-{practice_id}.html"'})

        def rename(practice_id):
            checked(practice_id)
            data = body(ArchiveAction)
            if not data.title.strip():
                raise LabError("INVALID_TITLE", "실습 이름을 입력하세요")
            store.rename(practice_id, mode, data.title.strip())
            return {"ok": True}

        def delete(practice_id):
            checked(practice_id)
            if classroom and runtime.team_rooms.is_practice(practice_id):
                raise LabError("PRACTICE_LINKED", "공유 링크에 연결된 팀 실습은 삭제할 수 없습니다", 409)
            with hub.lock if classroom else registry._lock:
                if classroom:
                    if hub.room and not hub.room.closed and hub.room.practice_id == practice_id:
                        raise LabError("PRACTICE_ACTIVE", "진행 중인 방을 먼저 종료하세요", 409)
                    if hub.room and hub.room.practice_id == practice_id:
                        with hub.room.lock:
                            store.delete(practice_id, mode)
                            hub.room.store = None
                    else:
                        store.delete(practice_id, mode)
                else:
                    if any(s.practice_id == practice_id for s in registry._labs.values()):
                        raise LabError("PRACTICE_ACTIVE", "진행 중인 실습을 먼저 종료하세요", 409)
                    store.delete(practice_id, mode)
                    runtime.ownership.remove(practice_id)
            return {"ok": True}

        def resume(practice_id):
            checked(practice_id)
            if classroom and runtime.team_rooms.is_practice(practice_id):
                raise LabError("PRACTICE_LINKED", "팀 실습은 기존 참여 링크에서 이어서 진행하세요", 409)
            data = body(ArchiveAction, empty=True)
            with hub.lock if classroom else registry._lock:
                item = store.read(practice_id, mode)
                try:
                    if not classroom:
                        return {"lab": registry.resume(item).snapshot()}
                    if runtime.team_rooms.default_code():
                        raise LabError("ROOM_EXISTS", "공유 팀 실습은 기존 참여 링크에서 이어서 진행하세요", 409)
                    if hub.room and not hub.room.closed:
                        raise LabError("ROOM_EXISTS", "진행 중인 방을 먼저 종료하세요", 409)
                    room = Classroom(data.name.strip() or "교사", 1, hub.clock)
                    restore(room.session, item["checkpoint"]["session"])
                    room.practice_id, room.store = practice_id, store
                    room.limited = item["checkpoint"].get("limited", False)
                    room.activity = item["checkpoint"].get("activity", [])
                    room.seq = max((event["seq"] for event in room.activity), default=0)
                    if room.session.race:
                        room.session.race.enabled = dict.fromkeys(NODE_IDS, False)
                        room.session.race.target_reward = None
                    room.pending_events = []
                    room.note(data.name, "저장된 수업 복원 · 새 방에서 대표를 다시 지정하세요")
                    persist_room(room, room.pending_events)
                    room.pending_events = []
                    hub.room = room
                    runtime.publish(room)
                    return credential(room, room.teacher)
                except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
                    raise LabError("CHECKPOINT_INVALID", "저장 데이터 검증에 실패했습니다. 원본을 유지했습니다", 409) from exc

        def status(practice_id):
            checked(practice_id)
            if classroom:
                room = hub.room
                if room and room.practice_id == practice_id:
                    with room.lock:
                        return {"id": practice_id, "savedAt": room.saved_at, "error": room.storage_error}
            else:
                with registry._lock:
                    for found in registry._labs.values():
                        if found.practice_id == practice_id:
                            with found._lock:
                                return found.storage_status()
            item = store.read(practice_id, mode)
            return {"id": practice_id, "savedAt": item["updated"], "error": ""}

        def save(practice_id):
            checked(practice_id)
            if classroom:
                room = hub.room
                if not room or room.practice_id != practice_id or room.closed:
                    raise LabError("ROOM_GONE", "진행 중인 수업이 아닙니다", 410)
                with room.lock:
                    persist_room(room, [])
                    room.seq += 1
                    runtime.publish(room)
                    return {"savedAt": room.saved_at}
            with registry._lock:
                for found in registry._labs.values():
                    if found.practice_id == practice_id:
                        with found._lock:
                            persist_session(found, [])
                            return {"savedAt": found.saved_at}
            raise LabError("PRACTICE_NOT_FOUND", "진행 중인 실습이 아닙니다", 404)

        for suffix, method, handler in [
            ("", "GET", listing), ("/<practice_id>", "GET", read),
            ("/<practice_id>/html", "GET", export), ("/<practice_id>/rename", "POST", rename),
            ("/<practice_id>", "DELETE", delete), ("/<practice_id>/resume", "POST", resume),
            ("/<practice_id>/status", "GET", status), ("/<practice_id>/save", "POST", save),
        ]:
            bp.add_url_rule(base + suffix, f"{label}_{handler.__name__}", handler, methods=[method])

    archives(False)
    if classroom_enabled:
        archives(True)

    def authorized(code):
        room = room_by_code(code)
        with room.lock:
            return room, room.auth(request.headers.get("Authorization", "").removeprefix("Bearer "))

    def room_by_code(code):
        return runtime.team_rooms.get(code) if runtime.team_rooms.is_team(code) else hub.get(code)

    @bp.get("/classroom/config")
    def classroom_config():
        code = runtime.team_rooms.default_code()
        return {"classroomOnly": False, "publicUrl": app.config.get("TEAM_PUBLIC_URL", ""),
                "defaultTeamCode": code, "teamMode": bool(code)}

    @bp.post("/team/rooms")
    def create_team_room():
        data = body(CreateTeamRoom)
        with runtime.creation_lock, hub.lock:
            hub.throttle()
            if data.newPractice or not runtime.team_rooms.default_code():
                creation_limit()
            room, person = runtime.team_rooms.create(
                data.name.strip(), data.difficulty, g.education_owner,
                initial_balance=data.initialBalance, new_practice=data.newPractice,
            )
            runtime.publish(room)
            return credential(room, person), 201

    @bp.post("/team/rooms/<code>/reconnect")
    def reconnect_team_room(code):
        with runtime.creation_lock, hub.lock:
            hub.throttle()
            room, person = runtime.team_rooms.reconnect(code, g.education_owner)
            runtime.publish(room)
            return credential(room, person)

    @bp.post("/team/rooms/<code>/save")
    def save_team_room(code):
        if not runtime.team_rooms.is_team(code):
            raise LabError("TEAM_ROOM_NOT_FOUND", "팀 실습을 찾을 수 없습니다", 404)
        room, _person = authorized(code)
        with room.lock:
            if room.closed or room.store is None:
                raise LabError("STALE_ROOM", "실습 연결이 갱신됐습니다 같은 링크로 다시 입장하세요", 409)
            persist_room(room, [])
            room.seq += 1
            runtime.publish(room)
            return {"savedAt": room.saved_at}

    @bp.post("/team/rooms/<code>/profile")
    def update_team_profile(code):
        data = body(JoinRoom)
        if not runtime.team_rooms.is_team(code):
            raise LabError("TEAM_ROOM_NOT_FOUND", "팀 실습을 찾을 수 없습니다", 404)
        room, person = authorized(code)
        name = runtime.team_rooms.rename(room, person, g.education_owner, data.name)
        runtime.publish(room)
        return {"name": name}

    @bp.post("/classroom/rooms")
    def create_room():
        data = body(CreateRoom)
        with runtime.creation_lock, hub.lock:
            if runtime.team_rooms.default_code():
                raise LabError("ROOM_EXISTS", "공유 팀 실습은 기존 참여 링크에서 이어서 진행하세요", 409)
            creation_limit()
            room, person = hub.create(data.secret, data.name.strip(), data.difficulty)
        runtime.publish(room)
        return credential(room, person), 201

    @bp.post("/classroom/rooms/<code>/join")
    def join_room(code):
        data = body(JoinRoom)
        with runtime.creation_lock, hub.lock:
            hub.throttle()
            if runtime.team_rooms.is_team(code):
                choose_owner()
                room, person = runtime.team_rooms.join(code, data.name.strip(), g.education_owner)
            else:
                room = hub.get(code)
                person = room.add(data.name.strip())
            runtime.publish(room)
            return credential(room, person), 201

    @bp.get("/classroom/rooms/<code>")
    def read_room(code):
        room, _person = authorized(code)
        return room.snapshot()

    @bp.post("/classroom/rooms/<code>/commands")
    def room_command(code):
        data = body(RoomCommand)
        room, person = authorized(code)
        with room.lock:
            now = time.monotonic()
            person.requests = [stamp for stamp in getattr(person, "requests", []) if now - stamp < 1]
            if len(person.requests) >= 10:
                raise LabError("RATE_LIMIT", "조작이 너무 빠릅니다. 잠시 기다려 주세요", 429)
            person.requests.append(now)
            result = room.command(person, data)
            runtime.publish(room)
            return result

    app.register_blueprint(bp)
    socket_options = app.config.setdefault("SOCK_SERVER_OPTIONS", {})
    socket_options.setdefault("ping_interval", 25)
    socket_options.setdefault("max_message_size", 32768)
    sock = Sock(app)
    runtime.sock = sock

    @sock.route(PREFIX + "/classroom/rooms/<code>/stream")
    def stream(ws, code):
        room = person = None
        attached = False
        try:
            if not classroom_enabled:
                raise LabError("CLASSROOM_DISABLED", "공동 수업을 사용할 수 없습니다", 404)
            origin = request.headers.get("Origin")
            if origin and not same_origin(origin):
                raise LabError("ORIGIN_FORBIDDEN", "현재 사이트에서 다시 연결하세요", 403)
            raw = ws.receive(timeout=5)
            if raw is None:
                return
            auth = json.loads(raw)
            if not isinstance(auth, dict):
                raise LabError("UNAUTHORIZED", "참가자 인증을 확인하세요", 401)
            room = room_by_code(code)
            with room.lock:
                person = room.auth(str(auth.get("token", "")))
                if person.connections >= 2:
                    raise LabError("TOO_MANY_CONNECTIONS", "동일 참가자의 연결이 너무 많습니다", 429)
                room.connected(person, 1)
                attached = True
                runtime.publish(room)
                previous = room.snapshot()
            ws.send(json.dumps({"type": "snapshot", "state": previous}, ensure_ascii=False))
            last_ack = last_ping = time.monotonic()
            while not runtime.stop_event.is_set():
                frame = ws.receive(timeout=0.25)
                if frame is not None:
                    if frame != "pong":
                        ws.close(reason=1008)
                        return
                    last_ack = time.monotonic()
                with room.lock:
                    if room.closed or person.id not in room.people:
                        ws.send(json.dumps({"type": "ended", "message": "방 또는 참여가 종료되었습니다"}, ensure_ascii=False))
                        break
                    current = getattr(room, "published", previous)
                if current["seq"] > previous["seq"]:
                    ws.send(json.dumps({"type": "patch", "runId": room.run_id,
                                        "baseSeq": previous["seq"], "seq": current["seq"],
                                        "patch": changes(previous, current)}, ensure_ascii=False))
                    previous = current
                now = time.monotonic()
                if now - last_ack > 30:
                    break
                if now - last_ping >= 10:
                    ws.send('{"type":"ping"}')
                    last_ping = now
        except LabError as exc:
            try:
                ws.send(json.dumps({"type": "ended", "code": exc.code, "message": exc.message}, ensure_ascii=False))
            except ConnectionClosed:
                pass
        except (ConnectionClosed, TimeoutError, ValueError, TypeError, AttributeError, RuntimeError):
            pass
        finally:
            if attached:
                room.connected(person, -1)
                runtime.publish(room)
            try:
                ws.close()
            except ConnectionClosed:
                pass

    if start_runner is None:
        start_runner = app.config.get("EDUCATION_RUNNER_ENABLED", not app.testing)
    if start_runner and classroom_enabled:
        runtime.start()
    atexit.register(runtime.close)
    runtime._atexit_registered = True
    return runtime
