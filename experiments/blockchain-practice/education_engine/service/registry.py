"""Active sessions in memory; durable practice records live in SQLite."""

import threading
import uuid
from collections import OrderedDict

from ..core.errors import LabNotFound
from .session import LabSession

MAX_LABS = 32


class LabRegistry:
    def __init__(self, max_labs: int = MAX_LABS, store=None) -> None:
        self.store = store
        self._labs: OrderedDict[str, LabSession] = OrderedDict()
        self._max_labs = max_labs
        self._lock = threading.RLock()

    def create(self) -> LabSession:
        with self._lock:
            session = LabSession(str(uuid.uuid4()))
            session.store = self.store
            if self.store:
                from .autosave import persist_session

                persist_session(session, [])
            self._labs[session.lab_id] = session
            # 탭을 닫아 버려진 실습이 쌓이지 않도록 가장 오래 쓰지 않은 실습부터 정리한다.
            while len(self._labs) > self._max_labs:
                _, evicted = self._labs.popitem(last=False)
                with evicted._lock:
                    evicted.retired = True
            return session

    def get(self, lab_id: str) -> LabSession:
        with self._lock:
            session = self._labs.get(lab_id)
            if session is None:
                raise LabNotFound()
            self._labs.move_to_end(lab_id)
            return session

    def delete(self, lab_id: str) -> None:
        with self._lock:
            session = self._labs.get(lab_id)
            if session:
                with session._lock:
                    from .autosave import pause, persist_session

                    pause(session)
                    if session.store:
                        persist_session(session, [])
                    session.retired = True
                self._labs.pop(lab_id, None)

    def __len__(self) -> int:
        return len(self._labs)

    def resume(self, item):
        from contextlib import ExitStack

        from .autosave import persist_session
        from .checkpoint import restore

        with self._lock, ExitStack() as locks:
            previous = [
                (key, value) for key, value in self._labs.items() if value.practice_id == item["id"]
            ]
            for _, old in previous:
                locks.enter_context(old._lock)
            item = self.store.read(item["id"], "personal")
            session = LabSession(str(uuid.uuid4()))
            restore(session, item["checkpoint"]["session"])
            session.practice_id = item["id"]
            session.store = self.store
            persist_session(session, [])
            for old_id, old in previous:
                old.retired = True
                del self._labs[old_id]
            self._labs[session.lab_id] = session
            while len(self._labs) > self._max_labs:
                _, evicted = self._labs.popitem(last=False)
                with evicted._lock:
                    evicted.retired = True
            return session
