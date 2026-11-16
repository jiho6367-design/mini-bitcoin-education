"""Commit state and events together before acknowledging a mutation."""

import copy
from functools import wraps

from ..core.errors import LabError
from ..core.wire import to_wire
from .checkpoint import capture, restore
from .storage import now


def pause(session):
    if session.race and session.race.status == "running":
        session.race.status = "paused"
    if session.mining and session.mining.status == "running":
        session.mining.status = "paused"


def bind_events(session, events):
    if session.lab:
        session.lab._event_sink = lambda event: events.append(
            {"at": now(), "source": "engine", "event": to_wire(event)}
        )


def persist_session(session, events):
    snapshot = session._snapshot()
    snapshot.pop("storage", None)
    session.saved_at = session.store.save(
        session.practice_id, "personal", {"session": capture(session)}, snapshot, events
    )
    session.storage_error = ""


def durable(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if not self.store:
            return method(self, *args, **kwargs)
        with self._lock:
            if self.retired:
                raise LabError("STALE_LAB", "교체되거나 종료된 실습입니다.", 410)
            before = capture(self)
            events = []
            bind_events(self, events)
            try:
                result = method(self, *args, **kwargs)
                if self.lab and before["lab"] is None:
                    events.extend(
                        {"at": now(), "source": "engine", "event": to_wire(e)}
                        for e in self.lab.events
                    )
                if method.__name__ in ("draft", "sign", "tamper"):
                    labels = {
                        "draft": "거래 작성 · 아직 서명 전",
                        "sign": "전자서명 완료 · 아직 미전파",
                        "tamper": "변조 실험 · 원본 체인 유지",
                    }
                    events.append(
                        {
                            "at": now(),
                            "source": "personal",
                            "event": {
                                "actor": "개인 실습",
                                "message": labels[method.__name__],
                                "detail": result,
                            },
                        }
                    )
                # Tick counters are saved as well; each batch is bounded by the engine.
                persist_session(self, events)
                if isinstance(result, dict) and isinstance(result.get("lab"), dict):
                    result["lab"]["storage"] = self.storage_status()
                return result
            except LabError as error:
                if error.code == "SAVE_FAILED":
                    restore(self, before, fresh=False, validate=False)
                    pause(self)
                    self.storage_error = error.message
                raise
            finally:
                if self.lab:
                    self.lab._event_sink = None

    return wrapped


def persist_room(room, events):
    # Participant identities are history only. No bearer tokens, secret or live authority.
    snapshot = room.snapshot()
    snapshot.pop("storage", None)
    room.saved_at = room.store.save(
        room.practice_id,
        "classroom",
        {
            "session": capture(room.session),
            "limited": room.limited,
            "activity": room.activity,
        },
        snapshot,
        events,
    )
    room.storage_error = ""


def durable_room(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if not self.store:
            return method(self, *args, **kwargs)
        with self.lock:
            if not self.store:
                return method(self, *args, **kwargs)
            if self.storage_error and method.__name__ == "tick":
                return
            before = capture(self.session)
            attrs = {
                k: copy.deepcopy(getattr(self, k))
                for k in (
                    "seq",
                    "closed",
                    "automatic",
                    "limited",
                    "activity",
                    "people",
                    "empty_since",
                )
            }
            self.pending_events = []
            bind_events(self.session, self.pending_events)
            try:
                result = method(self, *args, **kwargs)
                if self.seq != attrs["seq"] or method.__name__ == "command":
                    persist_room(self, self.pending_events)
                return result
            except LabError as error:
                if error.code == "SAVE_FAILED":
                    restore(self.session, before, fresh=False, validate=False)
                    self.__dict__.update(attrs)
                    self.teacher = (next(iter(self.people.values()), None) if getattr(self, "team_mode", False)
                                    else next((p for p in self.people.values() if p.role == "teacher"), None))
                    pause(self.session)
                    self.automatic = False
                    self.storage_error = error.message
                    self.seq += 1
                raise
            finally:
                if self.session.lab:
                    self.session.lab._event_sink = None
                self.pending_events = []

    return wrapped
