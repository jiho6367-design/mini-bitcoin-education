"""작업증명 채굴 작업.

서버는 스스로 시간을 재며 돌지 않는다. 브라우저가 tick을 보낼 때마다 다음 묶음의
Nonce를 실제로 계산하고 그 결과를 돌려준다. 그래서 스레드나 타이머가 필요 없고,
탭을 닫으면 계산도 멈춘다.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .block import calculate_nonce, valid_pow
from .types import Block, NodeId

MiningMode = Literal["fast", "learning"]
MiningStatus = Literal["running", "paused", "found", "stopped", "error"]

MINING_SAMPLE_LIMIT = 12
LEARNING_INTERVAL_MS = 250
FAST_INTERVAL_MS = 30
FAST_BATCH_MS = 40


@dataclass(frozen=True, slots=True)
class MiningSample:
    nonce: int
    hash: str
    passed: bool


class MiningJob:
    def __init__(
        self,
        job_id: str,
        node: NodeId,
        candidate: Block,
        mode: MiningMode = "fast",
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.job_id = job_id
        self.node = node
        self.candidate = candidate
        self.mode: MiningMode = mode
        self.status: MiningStatus = "paused"
        self.attempts = 0
        # 실제 해시 계산에 쓴 시간만 더한다. 일시정지·관찰 대기 시간은 포함하지 않는다.
        self.elapsed_ms = 0.0
        # 학습 실행에서 계산과 계산 사이에 화면을 관찰하도록 기다린 시간.
        self.observation_ms = 0.0
        self.samples: deque[MiningSample] = deque(maxlen=MINING_SAMPLE_LIMIT)
        self.latest: Block | None = None
        self.message: str | None = None
        self.seq = 0
        self._clock = clock
        self._waiting_since: float | None = None

    @property
    def active(self) -> bool:
        return self.status in ("running", "paused")

    @property
    def next_tick_in_ms(self) -> int | None:
        if self.status != "running":
            return None
        return LEARNING_INTERVAL_MS if self.mode == "learning" else FAST_INTERVAL_MS

    def start(self, step: bool) -> None:
        self.status = "paused" if step else "running"
        self._batch(single=step)

    def tick(self) -> None:
        if self.status != "running":
            return
        self._stop_waiting()
        self._batch(single=False)

    def pause(self) -> None:
        if self.status != "running":
            return
        self.status = "paused"
        self._stop_waiting()
        self.seq += 1

    def resume(self) -> None:
        if self.status != "paused":
            return
        self.status = "running"
        self._batch(single=False)

    def step(self) -> None:
        if self.status != "paused":
            return
        self._batch(single=True)

    def stop(self) -> None:
        if not self.active:
            return
        self.status = "stopped"
        self._stop_waiting()
        self.seq += 1

    def fail(self, message: str) -> None:
        self.status = "error"
        self.message = message
        self._waiting_since = None
        self.seq += 1

    def _stop_waiting(self) -> None:
        if self._waiting_since is not None:
            self.observation_ms += (self._clock() - self._waiting_since) * 1000
            self._waiting_since = None

    def _batch(self, single: bool) -> None:
        started = self._clock()
        # 계산을 짧게 나누어 다음 요청(일시정지·중지)을 처리할 기회를 만든다.
        while True:
            self.latest = calculate_nonce(self.candidate, self.attempts)
            self.attempts += 1
            found = valid_pow(self.latest.hash, self.latest.difficulty)
            # 첫 묶음에서 해답을 찾아도 마지막 실패들과 실제 성공을 비교할 수 있다.
            self.samples.append(MiningSample(self.latest.nonce, self.latest.hash, found))
            if found or single or self.mode == "learning":
                break
            if (self._clock() - started) * 1000 >= FAST_BATCH_MS:
                break
        self.elapsed_ms += (self._clock() - started) * 1000
        self.seq += 1
        if found:
            self.status = "found"
        elif self.status == "running" and self.mode == "learning":
            self._waiting_since = self._clock()
