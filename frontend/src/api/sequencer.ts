/**
 * 요청을 한 번에 하나씩, 누른 순서대로 보낸다.
 * 앞선 응답으로 화면 상태가 갱신된 뒤 다음 작업을 시작하므로, 각 작업은 실행 시점의
 * 최신 상태로 조건을 다시 확인할 수 있다. reset 이전에 예약되었거나 진행 중이던 작업의
 * 결과와 오류는 버리고 null을 돌려준다.
 */
export function createSequencer() {
  let tail: Promise<unknown> = Promise.resolve();
  let epoch = 0;
  return {
    // alive(): 이 작업이 아직 현재 실습의 것인지. 응답 뒤 상태를 바꾸기 전에 확인한다.
    run<T>(task: (alive: () => boolean) => Promise<T | null>): Promise<T | null> {
      const started = epoch;
      const alive = () => started === epoch;
      const result = tail
        .then(() => (alive() ? task(alive) : null))
        .then(
          (value) => (alive() ? value : null),
          (error: unknown) => {
            if (alive()) throw error;
            return null;
          },
        );
      tail = result.catch(() => undefined);
      return result;
    },
    reset() {
      epoch++;
    },
  };
}
export type Sequencer = ReturnType<typeof createSequencer>;
