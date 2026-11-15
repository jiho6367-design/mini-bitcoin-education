import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createServerConnection, PREPARING_MESSAGE } from "./serverConnection";

const config = { classroomOnly: false, publicUrl: "https://team.example", defaultTeamCode: "ABCD2345", teamMode: true };
let browser: EventTarget;
let documentEvents: EventTarget;
let network: { onLine: boolean };
let cleanups: (() => void)[];

beforeEach(() => {
  vi.useFakeTimers();
  browser = new EventTarget();
  documentEvents = new EventTarget();
  Object.defineProperty(documentEvents, "visibilityState", { value: "visible" });
  network = { onLine: true };
  vi.stubGlobal("window", browser);
  vi.stubGlobal("document", documentEvents);
  vi.stubGlobal("navigator", network);
  cleanups = [];
});

afterEach(() => {
  for (const cleanup of cleanups) cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function monitor() {
  const connection = createServerConnection();
  const unsubscribe = connection.subscribe(() => undefined);
  cleanups.push(unsubscribe);
  return connection;
}

it("keeps preparing through proxy HTML and malformed success until the database config is valid", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response("warming", { status: 503 }))
    .mockResolvedValueOnce(new Response("<html>Waking up</html>", { status: 200 }))
    .mockResolvedValueOnce(Response.json({ status: "ok" }))
    .mockResolvedValueOnce(Response.json(config));
  vi.stubGlobal("fetch", fetch);
  const connection = monitor();
  await vi.advanceTimersByTimeAsync(0);
  expect(connection.getSnapshot()).toMatchObject({ phase: "preparing", config: null, message: PREPARING_MESSAGE });
  for (const delay of [2_000, 4_000]) {
    await vi.advanceTimersByTimeAsync(delay);
    expect(connection.getSnapshot().phase).toBe("preparing");
  }
  await vi.advanceTimersByTimeAsync(8_000);
  expect(connection.getSnapshot()).toEqual({ phase: "ready", config, message: "연결됨" });
  expect(fetch).toHaveBeenCalledTimes(4);
  for (const [url, options] of fetch.mock.calls) {
    expect(url).toBe("/api/education/classroom/config");
    expect(options).toMatchObject({ method: "GET", cache: "no-store" });
  }
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetch).toHaveBeenCalledTimes(4);
  expect(vi.getTimerCount()).toBe(0);
});

it("bounds even a stalled response body and caps retry intervals while waiting over a minute", async () => {
  let firstSignal: AbortSignal | undefined;
  const fetch = vi.fn().mockImplementationOnce((_url, options: RequestInit) => {
    firstSignal = options.signal as AbortSignal;
    return Promise.resolve({ ok: true, status: 200, json: () => new Promise(() => undefined) });
  }).mockRejectedValue(new TypeError("service starting"));
  vi.stubGlobal("fetch", fetch);
  const connection = monitor();
  await vi.advanceTimersByTimeAsync(12_000);
  expect(firstSignal?.aborted).toBe(true);
  expect(connection.getSnapshot().phase).toBe("preparing");
  let calls = 1;
  for (const delay of [2_000, 4_000, 8_000, 10_000, 10_000, 10_000, 10_000]) {
    await vi.advanceTimersByTimeAsync(delay - 1);
    expect(fetch).toHaveBeenCalledTimes(calls);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(++calls);
  }
  fetch.mockResolvedValueOnce(Response.json(config));
  await vi.advanceTimersByTimeAsync(10_000);
  expect(connection.getSnapshot().phase).toBe("ready");
});

it("deduplicates runtime recovery and retains the existing config without replaying any mutation", async () => {
  let finish: ((value: Response) => void) | undefined;
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(config))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  vi.stubGlobal("fetch", fetch);
  const connection = monitor();
  await vi.advanceTimersByTimeAsync(0);
  const original = connection.getSnapshot().config;
  for (let i = 0; i < 5; i++) connection.reportUnavailable();
  expect(connection.getSnapshot()).toMatchObject({ phase: "preparing", config: original });
  await vi.advanceTimersByTimeAsync(0);
  for (let i = 0; i < 5; i++) connection.reportUnavailable();
  browser.dispatchEvent(new Event("online"));
  documentEvents.dispatchEvent(new Event("visibilitychange"));
  expect(fetch).toHaveBeenCalledTimes(2);
  finish!(Response.json(config));
  await vi.advanceTimersByTimeAsync(0);
  expect(connection.getSnapshot().phase).toBe("ready");
  expect(fetch.mock.calls.every(([, options]) => options.method === "GET")).toBe(true);
});

it("waits offline without requests and immediately resumes on an online event", async () => {
  network.onLine = false;
  const fetch = vi.fn().mockResolvedValue(Response.json(config));
  vi.stubGlobal("fetch", fetch);
  const connection = monitor();
  expect(connection.getSnapshot().phase).toBe("offline");
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetch).not.toHaveBeenCalled();
  network.onLine = true;
  browser.dispatchEvent(new Event("online"));
  await vi.advanceTimersByTimeAsync(0);
  expect(connection.getSnapshot().phase).toBe("ready");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("keeps an authorization or wrong-address failure honest until explicit retry", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ error: { code: "FORBIDDEN" } }, { status: 403 }))
    .mockResolvedValueOnce(Response.json(config));
  vi.stubGlobal("fetch", fetch);
  const connection = monitor();
  await vi.advanceTimersByTimeAsync(0);
  expect(connection.getSnapshot().phase).toBe("unavailable");
  documentEvents.dispatchEvent(new Event("visibilitychange"));
  browser.dispatchEvent(new Event("online"));
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetch).toHaveBeenCalledTimes(1);
  connection.retry();
  await vi.advanceTimersByTimeAsync(0);
  expect(connection.getSnapshot().phase).toBe("ready");
});

it("aborts on last unsubscribe and ignores the stale result after mounting again", async () => {
  let finishOld: ((value: Response) => void) | undefined;
  let oldSignal: AbortSignal | undefined;
  const fetch = vi.fn().mockImplementationOnce((_url, options: RequestInit) => {
    oldSignal = options.signal as AbortSignal;
    return new Promise<Response>(resolve => { finishOld = resolve; });
  }).mockResolvedValueOnce(Response.json(config));
  vi.stubGlobal("fetch", fetch);
  const connection = createServerConnection();
  const unsubscribe = connection.subscribe(() => undefined);
  unsubscribe();
  expect(oldSignal?.aborted).toBe(true);
  cleanups.push(connection.subscribe(() => undefined));
  await vi.advanceTimersByTimeAsync(0);
  finishOld!(Response.json({ ...config, defaultTeamCode: "OLD23456" }));
  await vi.advanceTimersByTimeAsync(0);
  expect(connection.getSnapshot()).toEqual({ phase: "ready", config, message: "연결됨" });
  expect(vi.getTimerCount()).toBe(0);
});
