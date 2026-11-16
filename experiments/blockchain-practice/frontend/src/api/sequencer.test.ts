import { describe, expect, it } from "vitest";
import { createSequencer } from "./sequencer";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("요청 순서 제어", () => {
  it("앞선 요청이 끝난 뒤에 다음 요청을 시작하고 순서를 지킨다", async () => {
    const sequencer = createSequencer();
    const first = deferred<string>();
    const log: string[] = [];
    const a = sequencer.run(() => {
      log.push("a 시작");
      return first.promise;
    });
    const b = sequencer.run(async () => {
      log.push("b 시작");
      return "b";
    });
    await Promise.resolve();
    expect(log).toEqual(["a 시작"]);
    first.resolve("a");
    expect(await a).toBe("a");
    expect(await b).toBe("b");
    expect(log).toEqual(["a 시작", "b 시작"]);
  });

  it("실패한 요청은 호출자에게 알리고 뒤의 요청을 막지 않는다", async () => {
    const sequencer = createSequencer();
    const failed = sequencer.run(async () => {
      throw new Error("실패");
    });
    const next = sequencer.run(async () => "다음");
    await expect(failed).rejects.toThrow("실패");
    expect(await next).toBe("다음");
  });

  it("초기화 이전의 진행 중·대기 중 작업은 결과와 오류를 모두 버린다", async () => {
    const sequencer = createSequencer();
    const slow = deferred<string>();
    const failing = deferred<string>();
    let queuedRan = false;
    const inFlight = sequencer.run(() => slow.promise);
    const queued = sequencer.run(async () => {
      queuedRan = true;
      return "이전 실습";
    });
    sequencer.reset();
    const fresh = sequencer.run(async () => "새 실습");
    slow.resolve("늦은 응답");
    expect(await inFlight).toBeNull();
    expect(await queued).toBeNull();
    expect(queuedRan).toBe(false);
    expect(await fresh).toBe("새 실습");

    let failingStarted = false;
    const lateError = sequencer.run(() => {
      failingStarted = true;
      return failing.promise;
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(failingStarted).toBe(true);
    sequencer.reset();
    failing.reject(new Error("늦은 오류"));
    expect(await lateError).toBeNull();
  });
});
