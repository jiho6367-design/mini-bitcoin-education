import { afterEach, expect, it, vi } from "vitest";
import { RequestTimeoutError, withRequestTimeout } from "./transport";

afterEach(() => vi.useRealTimers());

it("응답 없는 변경 요청의 대기를 해제하며 자동 재전송하지 않는다", async () => {
  vi.useFakeTimers();
  let signal: AbortSignal | undefined;
  const send = vi.fn((next: AbortSignal) => {
    signal = next;
    return new Promise<never>(() => undefined);
  });
  const result = withRequestTimeout(send, { mutation: true, timeoutMs: 100 });
  const rejected = expect(result).rejects.toThrow("작업이 처리됐을 수");
  await vi.advanceTimersByTimeAsync(100);
  await rejected;
  expect(signal?.aborted).toBe(true);
  expect(send).toHaveBeenCalledTimes(1);
});

it("본문 읽기가 멈춘 경우에도 시간 제한을 적용한다", async () => {
  vi.useFakeTimers();
  const result = withRequestTimeout(async () => {
    await Promise.resolve();
    return new Promise<never>(() => undefined);
  }, { timeoutMs: 100 });
  const rejected = expect(result).rejects.toBeInstanceOf(RequestTimeoutError);
  await vi.advanceTimersByTimeAsync(100);
  await rejected;
});

it("응답이 끝나면 타이머를 해제하고 서버 오류는 그대로 전달한다", async () => {
  vi.useFakeTimers();
  expect(await withRequestTimeout(async () => 42)).toBe(42);
  const failure = new Error("validation rejected");
  await expect(withRequestTimeout(async () => { throw failure; })).rejects.toBe(failure);
  expect(vi.getTimerCount()).toBe(0);
});
