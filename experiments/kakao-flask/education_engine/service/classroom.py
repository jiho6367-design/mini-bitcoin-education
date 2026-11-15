"""One active classroom with durable educational state and temporary credentials."""

import copy
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from ..api.schemas import RaceRequest
from ..core.crypto import create_wallet
from ..core.errors import LabError
from ..core.lab import Lab
from ..core.types import NODE_IDS
from .autosave import durable_room, persist_room
from .session import LabSession
from .storage import now

OBSERVER_RECLAIM_SECONDS = 300
REPRESENTATIVE_GRACE_SECONDS = 15


def fail(code, message, status=409):
    raise LabError(code, message, status)


@dataclass
class Participant:
    id: str
    name: str
    token: str
    role: str = "observer"
    team: str | None = None
    connections: int = 0
    disconnected: float = 0
    suspended: bool = False
    commands: OrderedDict = field(default_factory=OrderedDict)
    draft: dict | None = None
    signed: dict | None = None
    assignment_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class Classroom:
    def __init__(self, name, difficulty, clock=time.monotonic, *, team_mode=False, code=None):
        self.store = None
        self.practice_id = str(uuid.uuid4())
        self.saved_at = None
        self.storage_error = ""
        self.pending_events = []
        self.lock = threading.RLock()
        self.clock = clock
        self.team_mode = bool(team_mode)
        self.run_id = str(uuid.uuid4())
        if code is not None and (
            not isinstance(code, str)
            or len(code) != 8
            or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for c in code)
        ):
            fail("INVALID_ROOM_CODE", "방 코드를 확인하세요.", 400)
        self.code = code or "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)
        )
        self.session = LabSession(str(uuid.uuid4()))  # Not registered in personal LabRegistry.
        self.session.lab = Lab(
            [create_wallet(f"팀 {n}") for n in NODE_IDS], difficulty, team_allocations=True
        )
        self.session.wallets = self.session.lab.wallets
        self.people = {}
        self.seq = 0
        self.closed = False
        self.automatic = False
        self.limited = False
        self.activity = []
        self.empty_since = clock()
        # Keep the creator alias for existing callers; it grants no authority in team mode.
        self.teacher = self.add(name, teacher=not self.team_mode)
        self.note("팀" if self.team_mode else "교사", "방 생성 · 각 팀에 학습용 100 코인 배정")

    def note(self, name, message):
        self.seq += 1
        event = {"seq": self.seq, "actor": name, "message": message, "at": now()}
        self.activity.append(event)
        self.pending_events.append({"source": "classroom", "event": event})
        self.activity = self.activity[-100:]

    @durable_room
    def add(self, name, teacher=False):
        with self.lock:
            if self.closed:
                fail("ROOM_GONE", "종료된 방입니다.", 410)
            if len(self.people) >= 30:
                abandoned = sorted(
                    (
                        p
                        for p in self.people.values()
                        if p.role == "observer"
                        and not p.connections
                        and self.clock() - p.disconnected >= OBSERVER_RECLAIM_SECONDS
                    ),
                    key=lambda p: p.disconnected,
                )
                if not abandoned:
                    fail(
                        "ROOM_FULL",
                        "최대 30명까지 참여할 수 있습니다. 잠시 뒤 다시 입장하세요."
                        if self.team_mode
                        else "최대 30명까지 참여할 수 있습니다. 교사에게 이탈 참가자 정리를 요청하세요.",
                    )
                self._remove_participant(abandoned[0], "서버", expired=True)
            p = Participant(
                str(uuid.uuid4()),
                name,
                secrets.token_urlsafe(32),
                "teacher" if teacher and not self.team_mode else "observer",
                disconnected=self.clock(),
            )
            self.people[p.id] = p
            self.note(name, "입장 · 연결 대기")
            return p

    def auth(self, token):
        if self.closed:
            fail("ROOM_GONE", "서버가 재시작되었거나 방이 종료됐습니다.", 410)
        for p in self.people.values():
            if secrets.compare_digest(p.token.encode(), token.encode()):
                return p
        fail("UNAUTHORIZED", "참가자 인증이 만료되었습니다. 다시 입장하세요.", 401)

    def connected(self, p, delta):
        """Socket counts describe physical connections and must survive a save failure."""
        with self.lock:
            p = self.people.get(p.id)
            if p is None:
                return
            if (
                self.team_mode
                and delta > 0
                and not p.connections
                and p.team
                and self.clock() - p.disconnected >= REPRESENTATIVE_GRACE_SECONDS
            ):
                self._expire_representative(p)
            p.connections = max(0, p.connections + delta)
            if p.connections == 0:
                p.disconnected = self.clock()
            else:
                p.suspended = False
                self.empty_since = None
            self.note(
                p.name,
                "접속" if delta > 0 else (
                    "연결 끊김 · 15초 후 팀 역할 해제" if self.team_mode
                    else "연결 끊김 · 15초 후 실행 일시정지"
                ),
            )
            if self.store and not self.closed:
                try:
                    persist_room(self, self.pending_events)
                except LabError as error:
                    if error.code != "SAVE_FAILED":
                        raise
                    # Keep the live socket count; pause educational actions until saved.
                    from .autosave import pause

                    pause(self.session)
                    self.automatic = False
                    self.storage_error = error.message
                    self.seq += 1
                else:
                    self.pending_events = []

    def _release_assignment(self, person):
        """Revoke a representative's work and in-flight authority, retaining the ledger."""
        if person.team and self.session.race:
            race = self.session.race
            race.enabled[person.team] = False
            if race.advance_node == person.team and race.target_reward:
                race.command("stop")
            elif not any(race.enabled.values()):
                race.command("pause")
        person.commands.clear()
        person.assignment_id = str(uuid.uuid4())
        person.team, person.role, person.draft, person.signed = None, "observer", None, None

    def _expire_representative(self, person):
        self._release_assignment(person)
        person.suspended = True
        self.note("서버", f"{person.name} 연결 대기 · 팀 역할 해제")

    def _pause_empty_team(self):
        from .autosave import pause

        running = self.automatic or (
            self.session.race and self.session.race.status == "running"
        ) or (self.session.mining and self.session.mining.status == "running")
        pause(self.session)
        self.automatic = False
        if running:
            self.note("서버", "접속자 없음 · 실습 일시정지 · 기록 유지")

    def _remove_participant(self, target, actor, *, expired=False):
        if target.team and self.session.race:
            race = self.session.race
            race.enabled[target.team] = False
            if race.advance_node == target.team and race.target_reward:
                race.command("stop")
            elif not any(race.enabled.values()):
                race.command("pause")
        del self.people[target.id]
        self.note(
            actor,
            f"{target.name} 참여 종료 · "
            + ("5분 이상 이탈한 관찰자 명단 정리" if expired else "참가자 명단에서 제거"),
        )

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(
                {
                    "storage": {
                        "id": self.practice_id,
                        "savedAt": self.saved_at,
                        "error": self.storage_error,
                    },
                    "runId": self.run_id,
                    "code": self.code,
                    "teamMode": self.team_mode,
                    "seq": self.seq,
                    "closed": self.closed,
                    "automatic": self.automatic,
                    "limited": self.limited,
                    "participants": [
                        {
                            "id": p.id,
                            "name": p.name,
                            "role": p.role,
                            "team": p.team,
                            "connected": p.connections > 0,
                            "assignmentId": p.assignment_id,
                        }
                        for p in self.people.values()
                    ],
                    "activity": list(self.activity),
                    "lab": self.session.snapshot(),
                }
            )

    @durable_room
    def command(self, p, request):
        with self.lock:
            if self.closed or p.id not in self.people:
                fail("ROOM_GONE", "종료된 참여입니다.", 410)
            p = self.people[p.id]
            if request.runId != self.run_id:
                fail("STALE_ROOM", "이전 수업의 요청입니다.")
            key, fingerprint = request.commandId, request.model_dump_json()
            # Claim/release rotate their own assignment. A lost acknowledgement may
            # replay that exact transition, but revoked commands were already cleared.
            if self.team_mode and request.action in {"claim", "release"} and key in p.commands:
                old, result = p.commands[key]
                if old != fingerprint:
                    fail("COMMAND_CONFLICT", "같은 명령 ID를 다른 요청에 사용할 수 없습니다.")
                return copy.deepcopy(result)
            if request.assignmentId is not None and request.assignmentId != p.assignment_id:
                fail("STALE_ROLE", "역할이 변경되었습니다. 현재 역할을 확인한 뒤 다시 조작하세요.")
            if p.connections == 0 and request.action != "leave":
                fail("OFFLINE", "실시간 연결을 복구한 뒤 조작하세요.")
            if key in p.commands:
                old, result = p.commands[key]
                if old != fingerprint:
                    fail("COMMAND_CONFLICT", "같은 명령 ID를 다른 요청에 사용할 수 없습니다.")
                return copy.deepcopy(result)
            result = self._command(p, request.action, request.payload)
            p.commands[key] = (fingerprint, copy.deepcopy(result))
            while len(p.commands) > 128:
                p.commands.popitem(last=False)
            return result

    def _command(self, p, action, data):
        teacher = not self.team_mode and p.role == "teacher"
        if self.limited and action in {
            "prepare",
            "resume",
            "step",
            "advance",
            "fork",
            "draft",
            "sign",
            "submit",
        }:
            fail(
                "ROOM_LIMIT",
                "실습 실행 한도입니다 저장한 기록을 확인하세요"
                if self.team_mode else "수업 실행 한도입니다. 방을 종료하고 새 방을 만드세요.",
            )
        if action == "leave":
            if teacher:
                self.closed = True
                self.automatic = False
                if self.session.race:
                    self.session.race.command("stop")
            else:
                if self.team_mode:
                    self._release_assignment(p)
                elif p.team and self.session.race:
                    self.session.race.enabled[p.team] = False
                del self.people[p.id]
                if self.team_mode and not any(x.connections for x in self.people.values()):
                    self._pause_empty_team()
            self.note(p.name, "퇴장" if not teacher else "방 종료")
            return {"ok": True}
        teacher_actions = {
            "assign",
            "prepare",
            "resume",
            "pause",
            "stop",
            "step",
            "configure",
            "advance",
            "deliver",
            "automatic",
            "fork",
            "close",
            "remove",
        }
        if self.team_mode and action in {"assign", "remove", "close", "reset"}:
            fail("FORBIDDEN", "팀 실습에서는 다른 참가자나 저장한 방을 변경할 수 없습니다.", 403)
        if action in teacher_actions and not teacher and not self.team_mode:
            fail("FORBIDDEN", "교사만 실행할 수 있습니다.", 403)
        if action in {"draft", "sign", "submit", "participate"} and (
            p.team not in NODE_IDS or (self.team_mode and p.role != "representative")
        ):
            fail("FORBIDDEN", "팀 대표만 실행할 수 있습니다.", 403)
        lab = self.session.lab
        result = {"ok": True}
        if action in {"claim", "release"}:
            if not self.team_mode:
                fail("FORBIDDEN", "팀 역할은 교사가 지정합니다.", 403)
            if data.get("participantId", p.id) != p.id:
                fail("FORBIDDEN", "자신의 팀 역할만 변경할 수 있습니다.", 403)
            if action == "claim":
                team = data.get("team")
                if team not in NODE_IDS:
                    fail("INVALID_ASSIGNMENT", "참여할 팀을 확인하세요.", 400)
                holder = next((x for x in self.people.values() if x.team == team), None)
                if holder and holder.id != p.id:
                    if holder.connections or self.clock() - holder.disconnected < REPRESENTATIVE_GRACE_SECONDS:
                        fail("TEAM_TAKEN", "이미 참여 중인 팀입니다. 다른 팀을 선택하세요.")
                    self._expire_representative(holder)
                if p.team == team and p.role == "representative":
                    return result
                self._release_assignment(p)
                p.team, p.role = team, "representative"
                self.note(p.name, f"팀 {team} 대표 참여 · 채굴 참여는 직접 재개")
            elif p.team or p.role != "observer":
                self._release_assignment(p)
                self.note(p.name, "팀 역할 해제")
            return result
        if action == "remove":
            target_id = data.get("participantId")
            target = self.people.get(target_id) if isinstance(target_id, str) else None
            if not target or target.role == "teacher":
                fail(
                    "INVALID_PARTICIPANT",
                    "제거할 참가자를 확인하세요. 교사는 방 종료를 사용하세요.",
                    400,
                )
            self._remove_participant(target, p.name)
            return result
        if action == "assign":
            target_id = data.get("participantId")
            if not isinstance(target_id, str):
                fail("INVALID_ASSIGNMENT", "참가자를 확인하세요.", 400)
            team, target = data.get("team"), self.people.get(target_id)
            if team not in NODE_IDS or not target or target.role == "teacher":
                fail("INVALID_ASSIGNMENT", "참가자와 팀을 확인하세요.", 400)
            for person in self.people.values():
                if person.team == team or person.id == target.id:
                    if person.team and self.session.race:
                        self.session.race.enabled[person.team] = False
                    person.commands.clear()
                    person.assignment_id = str(uuid.uuid4())
                    person.team, person.role, person.draft, person.signed = (
                        None,
                        "observer",
                        None,
                        None,
                    )
            target.team, target.role = team, "representative"
            self.note(p.name, f"{target.name} → 팀 {team} 대표 · 채굴 참여는 직접 재개")
            return result
        if action in {"draft", "sign", "submit"}:
            own = [lab.wallets[NODE_IDS.index(p.team)], lab.miners[p.team]]
            if action == "draft":
                address = data.get("senderAddress")
                if address not in [w.address for w in own]:
                    fail("FORBIDDEN", "다른 팀의 지갑은 사용할 수 없습니다.", 403)
                result = self.session.draft(
                    p.team,
                    address,
                    data.get("recipientAddress"),
                    data.get("amount"),
                    data.get("fee", 1),
                    data.get("selectedInputs"),
                )
                p.draft, p.signed = result, None
            elif action == "sign":
                if not p.draft:
                    fail("NO_DRAFT", "먼저 거래를 작성하세요.")
                if data.get("draftId") != p.draft["signingHash"]:
                    fail("STALE_DRAFT", "교체된 거래입니다. 새 거래를 확인하세요.")
                result = self.session.sign(p.draft["draft"])
                p.signed = result["signed"]
            else:
                if not p.signed or data.get("transactionId") != p.signed["id"]:
                    fail("NO_SIGNATURE", "현재 거래에 먼저 서명하세요.")
                result = self.session.submit(p.team, p.signed)
                result.pop("lab", None)
                if result["validation"]["valid"]:
                    p.draft = p.signed = None
        elif action == "participate":
            if not self.session.race or data.get("raceId") != self.session.race.id:
                fail("STALE_RACE", "현재 후보를 확인하세요.")
            if self.session.race.target_reward:
                fail("ADVANCING", "보상 성숙 진행 중에는 참여 구성을 바꿀 수 없습니다.")
            if type(data.get("enabled")) is not bool:
                fail("INVALID_REQUEST", "참여 여부를 확인하세요.", 400)
            self.session.race.enabled[p.team] = data["enabled"]
        elif action == "close":
            self.closed, self.automatic = True, False
            if self.session.race:
                self.session.race.command("stop")
        elif action == "automatic":
            if type(data.get("enabled")) is not bool:
                fail("INVALID_REQUEST", "자동 전달 여부를 확인하세요.", 400)
            self.automatic = data["enabled"]
        elif action == "deliver":
            self.session.deliver_next()
        elif action in {
            "prepare",
            "fork",
            "resume",
            "pause",
            "stop",
            "step",
            "configure",
            "advance",
        }:
            race_action = "prepare" if action == "fork" else action
            args = {
                k: v
                for k, v in data.items()
                if k in {"raceId", "guided", "mode", "nodeId", "reward"}
            }
            if action == "advance" and not any(
                x.team == data.get("nodeId") and x.connections for x in self.people.values()
            ):
                fail("TEAM_OFFLINE", "해당 팀 대표가 접속한 뒤 진행하세요.")
            self.session.race_action(RaceRequest(action=race_action, **args))
            if action in {"prepare", "fork"}:
                self.session.race.enabled = dict.fromkeys(NODE_IDS, False)
            if action == "fork":
                self.automatic = False
                self.session.race.guided = False
            if action in {"pause", "stop"}:
                self.automatic = False
            if action == "advance":
                node = self.session.race.advance_node
                if not any(x.team == node and x.connections for x in self.people.values()):
                    self.session.race.command("pause")
                    fail("TEAM_OFFLINE", "해당 팀 대표가 접속한 뒤 진행하세요.")
        else:
            fail("UNKNOWN_COMMAND", "지원하지 않는 조작입니다.", 400)
        labels = {
            "draft": "거래 작성 · 아직 서명 전",
            "sign": "개인키로 서명 완료 · 아직 미전파",
            "submit": "거래 검증·전파 요청",
            "participate": f"팀 {p.team} 채굴 참여 변경",
            "prepare": "세 후보 준비 · 팀 대표의 참여 대기",
            "fork": "분기 실험 준비 · 자동 전달 보류",
            "resume": "경쟁 재개",
            "pause": "전체 일시정지",
            "stop": "경쟁 중지",
            "step": "각자 한 번 계산",
            "configure": "실습 방식 변경",
            "advance": "실제 블록 채굴로 보상 성숙 진행",
            "automatic": "자동 전달 설정 변경",
            "deliver": "메시지 한 개 전달·수신 검증",
            "close": "방 종료",
        }
        self.note(p.name, labels.get(action, action))
        return result

    @durable_room
    def tick(self):
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            if self.team_mode:
                for p in self.people.values():
                    if (
                        p.team
                        and not p.connections
                        and now - p.disconnected >= REPRESENTATIVE_GRACE_SECONDS
                    ):
                        self._expire_representative(p)
            if not any(p.connections for p in self.people.values()):
                if self.empty_since is None:
                    self.empty_since = now
                if self.team_mode:
                    self._pause_empty_team()
                    return
                if now - self.empty_since >= 1800:
                    self.closed = True
                    self.note("서버", "모두 퇴장한 뒤 30분 경과 · 방 종료")
                    return
            for p in (() if self.team_mode else self.people.values()):
                if p.connections == 0 and not p.suspended and now - p.disconnected >= 15:
                    p.suspended = True
                    if p.role == "teacher":
                        self.automatic = False
                        if self.session.race:
                            self.session.race.command("pause")
                    elif p.team and self.session.race:
                        self.session.race.enabled[p.team] = False
                        if (
                            self.session.race.advance_node == p.team
                            and self.session.race.target_reward
                        ):
                            self.session.race.command("pause")
                    self.note("서버", f"{p.name} 연결 대기 · 담당 실행 일시정지")
            # Disconnected teacher cannot restart the loop with a late HTTP command.
            if not self.team_mode and self.teacher.connections == 0 and self.teacher.suspended:
                return
            if self.session.race and self.session.race.status == "running":
                before = len(lab_queue := self.session.lab.queue)
                self.session.race_action(RaceRequest(action="tick", raceId=self.session.race.id))
                self.seq += 1
                if len(lab_queue) != before:
                    self.note("서버", "블록 발견 · 각 노드의 수신·검증을 기다립니다")
            if self.automatic and self.session.lab.queue:
                self.session.deliver_next()
                self.note("서버", "메시지 자동 전달 · 수신 노드 독립 검증")
            if not self.limited and (
                max(len(n.blocks) for n in self.session.lab.nodes.values()) >= 500
                or len(self.session.lab.queue) >= 300
            ):
                self.limited = True
                if self.session.race:
                    self.session.race.command("pause")
                self.automatic = False
                self.note(
                    "서버",
                    "실습 실행 한도 도달 · 기록 유지"
                    if self.team_mode else "수업 실행 한도 도달 · 방을 종료하고 새 실습을 시작하세요",
                )


class ClassroomHub:
    def __init__(self, secret=None, clock=time.monotonic, store=None):
        self.store = store
        self.secret = secret or secrets.token_urlsafe(18)
        self.clock = clock
        self.room = None
        self.lock = threading.RLock()
        self.attempts = []

    def throttle(self):
        now = self.clock()
        self.attempts = [x for x in self.attempts if now - x < 60]
        if len(self.attempts) >= 90:
            fail("RATE_LIMIT", "입장 요청이 많습니다. 잠시 뒤 다시 시도하세요.", 429)
        self.attempts.append(now)

    def create(self, secret, name, difficulty):
        with self.lock:
            self.throttle()
            if not secrets.compare_digest(secret.encode(), self.secret.encode()):
                fail("FORBIDDEN", "교사용 비밀코드를 확인하세요.", 403)
            if self.room and not self.room.closed:
                fail("ROOM_EXISTS", "진행 중인 방을 먼저 종료하세요.")
            room = Classroom(name, difficulty, self.clock)
            room.store = self.store
            if self.store:
                persist_room(room, room.pending_events)
                room.pending_events = []
            self.room = room
            return room, room.teacher

    def get(self, code):
        with self.lock:
            if not self.room or self.room.code != code or self.room.closed:
                fail(
                    "ROOM_GONE",
                    "서버가 재시작되었거나 방이 종료됐습니다. 새 방 코드를 확인하세요.",
                    410,
                )
            return self.room


def changes(previous, current):
    """Dictionary merge patch; arrays replaced, null is a value (no dictionary deletion)."""
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return current
    if previous.keys() - current.keys():
        return {"$replace": current}
    return {k: changes(previous.get(k), v) for k, v in current.items() if previous.get(k) != v}
