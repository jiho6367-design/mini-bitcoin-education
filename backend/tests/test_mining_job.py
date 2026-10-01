"""채굴 작업의 단계·일시정지·중지와 시간 집계. 이전 Web Worker 테스트를 대신한다."""

from dataclasses import replace

import pytest
from test_parity_vectors import _block

from mini_bitcoin_backend.core.block import calculate_nonce, valid_pow
from mini_bitcoin_backend.core.mining import (
    LEARNING_INTERVAL_MS,
    MINING_SAMPLE_LIMIT,
    MiningJob,
)


class ManualClock:
    """테스트가 직접 시간을 움직이는 시계(초 단위)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms / 1000


class TickingClock:
    """읽을 때마다 일정 시간이 흐르는 시계. 빠른 실행의 40ms 묶음을 재현한다."""

    def __init__(self, step_ms: float) -> None:
        self.now = 0.0
        self.step = step_ms / 1000

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def first_valid_nonce(candidate) -> int:
    nonce = 0
    while not valid_pow(calculate_nonce(candidate, nonce).hash, candidate.difficulty):
        nonce += 1
    return nonce


@pytest.fixture
def candidates(vectors):
    return [_block(s["candidate"]) for s in vectors["steps"] if s["op"] == "mine"]


@pytest.fixture
def hard(candidates):
    """난이도 4 후보. 테스트가 계산하는 몇 번 안에는 성공하지 않는다."""
    candidate = replace(candidates[0], difficulty=4)
    assert first_valid_nonce(candidate) > 1000
    return candidate


@pytest.fixture
def late(candidates):
    """첫 성공 Nonce가 31인 난이도 1 후보."""
    candidate = candidates[2]
    assert first_valid_nonce(candidate) == 31
    return candidate


def job(candidate, mode="fast", clock=None) -> MiningJob:
    return MiningJob("job-1", "A", candidate, mode, clock or ManualClock())


def test_step_computes_exactly_one_real_nonce(hard):
    """한 번 계산은 Nonce 하나만 시도하고 결과는 실제 해시와 같다"""
    j = job(hard)
    j.start(step=True)
    assert (j.status, j.attempts, j.latest.nonce) == ("paused", 1, 0)
    assert j.latest == calculate_nonce(hard, 0)
    j.step()
    assert (j.status, j.attempts, j.latest.nonce) == ("paused", 2, 1)
    assert [s.nonce for s in j.samples] == [0, 1]
    assert not any(s.passed for s in j.samples)
    assert j.next_tick_in_ms is None


def test_pause_holds_attempts_and_stop_ends_the_job(hard):
    """일시정지는 시도 횟수를 유지하고, 중지 뒤의 재개·단계 요청은 무시한다"""
    j = job(hard, clock=TickingClock(10))
    j.start(step=False)
    assert j.status == "running" and j.attempts > 1
    assert j.next_tick_in_ms == 30
    j.pause()
    held = j.attempts
    j.tick()
    assert (j.status, j.attempts) == ("paused", held)
    j.resume()
    assert j.status == "running" and j.attempts > held
    j.stop()
    stopped = j.attempts
    for late_command in (j.resume, j.step, j.tick, j.pause):
        late_command()
        assert (j.status, j.attempts) == ("stopped", stopped)
    assert j.active is False


def test_fast_batches_are_bounded_and_count_only_compute_time(hard):
    """빠른 실행은 약 40ms 묶음으로 계산하고 관찰 대기 시간을 쌓지 않는다"""
    # 시계를 읽을 때마다 16ms가 흐르므로 세 번째 계산 뒤에 40ms를 넘는다.
    j = job(hard, clock=TickingClock(16))
    j.start(step=False)
    first = j.attempts
    assert first == 3
    j.tick()
    assert j.attempts == 2 * first
    assert j.elapsed_ms > 0
    assert j.observation_ms == 0


def test_learning_separates_observation_from_compute_and_pause(hard):
    """저속 학습은 Nonce 하나씩 계산하고 관찰 대기·일시정지를 구분한다"""
    clock = ManualClock()
    j = job(hard, "learning", clock)
    j.start(step=False)
    assert (j.status, j.attempts, j.next_tick_in_ms) == ("running", 1, LEARNING_INTERVAL_MS)
    clock.advance(250)
    j.tick()
    assert j.attempts == 2
    assert j.observation_ms == pytest.approx(250)
    clock.advance(100)
    j.pause()
    assert j.observation_ms == pytest.approx(350)
    # 일시정지 중에 흐른 시간은 관찰 대기 시간이 아니다.
    clock.advance(300)
    j.step()
    assert (j.status, j.attempts) == ("paused", 3)
    assert j.observation_ms == pytest.approx(350)
    j.resume()
    assert (j.status, j.attempts) == ("running", 4)
    assert j.observation_ms == pytest.approx(350)
    clock.advance(250)
    j.tick()
    assert j.attempts == 5
    assert j.observation_ms == pytest.approx(600)
    clock.advance(40)
    j.stop()
    assert j.observation_ms == pytest.approx(640)
    # 수동 시계는 계산 중에 흐르지 않으므로 계산 시간은 0으로 남는다.
    assert j.elapsed_ms == 0


def test_first_batch_success_keeps_limited_real_samples(late):
    """빠른 첫 묶음의 성공도 실제 실패·성공 표본을 제한된 개수로 보존한다"""
    j = job(late, clock=TickingClock(0.1))
    j.start(step=False)
    assert (j.status, j.attempts, j.latest.nonce) == ("found", 32, 31)
    assert len(j.samples) == MINING_SAMPLE_LIMIT
    assert [s.nonce for s in j.samples] == list(range(20, 32))
    assert [s.passed for s in j.samples] == [False] * 11 + [True]
    for sample in j.samples:
        assert sample.hash == calculate_nonce(late, sample.nonce).hash
    assert j.next_tick_in_ms is None
    j.tick()
    assert j.attempts == 32


def test_first_real_hash_can_succeed(candidates):
    """첫 실제 해시의 성공을 그대로 인정한다"""
    candidate = next(
        c
        for c in (replace(candidates[0], timestamp=2000 + i) for i in range(2000))
        if first_valid_nonce(c) == 0
    )
    for mode, step in (("fast", False), ("fast", True), ("learning", False)):
        j = job(candidate, mode)
        j.start(step=step)
        assert (j.status, j.attempts, len(j.samples)) == ("found", 1, 1)
        assert j.samples[0].passed is True
        assert j.observation_ms == 0


def test_sequence_number_grows_with_every_change(hard):
    j = job(hard, "learning")
    j.start(step=False)
    seen = [j.seq]
    for command in (j.tick, j.pause, j.step, j.resume, j.stop):
        command()
        seen.append(j.seq)
    assert seen == sorted(set(seen))
