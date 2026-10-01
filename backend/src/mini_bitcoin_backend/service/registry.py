"""진행 중인 실습들을 메모리에 보관한다. 저장소나 DB는 없으며 서버를 끄면 모두 사라진다."""

import threading
import uuid
from collections import OrderedDict

from ..core.errors import LabNotFound
from .session import LabSession

MAX_LABS = 32


class LabRegistry:
    def __init__(self, max_labs: int = MAX_LABS) -> None:
        self._labs: OrderedDict[str, LabSession] = OrderedDict()
        self._max_labs = max_labs
        self._lock = threading.Lock()

    def create(self) -> LabSession:
        with self._lock:
            session = LabSession(str(uuid.uuid4()))
            self._labs[session.lab_id] = session
            # 탭을 닫아 버려진 실습이 쌓이지 않도록 가장 오래 쓰지 않은 실습부터 정리한다.
            while len(self._labs) > self._max_labs:
                self._labs.popitem(last=False)
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
            self._labs.pop(lab_id, None)

    def __len__(self) -> int:
        return len(self._labs)
