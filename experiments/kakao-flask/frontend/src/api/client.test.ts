import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { serverConnection } from "./serverConnection";

beforeEach(() => {
  vi.spyOn(serverConnection, "reportUnavailable").mockImplementation(() => undefined);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("reports proxy startup without replaying a possibly accepted create request", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("<html>Starting</html>", { status: 502 }));
  vi.stubGlobal("fetch", fetch);
  await expect(api.createLab({ mode: "example", difficulty: 1 })).rejects.toMatchObject({
    status: 502, code: "BACKEND_UNREACHABLE",
    message: expect.stringContaining("서버 준비 중"),
  });
  expect(serverConnection.reportUnavailable).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1].method).toBe("POST");
});

it("preserves uncertainty when a mutation response disappears or a success is not JSON", async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValueOnce(new Response("warming", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  for (let i = 0; i < 2; i++) {
    await expect(api.createLab({ mode: "manual" })).rejects.toMatchObject({
      message: expect.stringContaining("작업이 처리됐을 수"),
    });
  }
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("keeps validation, authorization, quota and application failures separate from warming", async () => {
  for (const [status, code] of [[400, "INVALID_REQUEST"], [403, "FORBIDDEN"], [429, "ARCHIVE_LIMIT"], [500, "APPLICATION_ERROR"]] as const) {
    const message = `원래 오류 ${code}`;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ error: { code, message } }, { status })));
    await expect(api.createLab({ mode: "manual" })).rejects.toMatchObject({ status, code, message });
  }
  expect(serverConnection.reportUnavailable).not.toHaveBeenCalled();
});

it("preserves confirmed rollback details while checking database availability", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    error: { code: "SAVE_FAILED", message: "저장 실패로 되돌렸습니다" },
  }, { status: 503 })));
  await expect(api.createLab({ mode: "manual" })).rejects.toMatchObject({
    status: 503, code: "SAVE_FAILED", message: expect.stringContaining("되돌렸습니다"),
  });
  expect(serverConnection.reportUnavailable).toHaveBeenCalledTimes(1);
});

it("accepts an empty successful deletion without starting a readiness probe", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
  await expect(api.deleteLab("practice-id")).resolves.toBeUndefined();
  expect(serverConnection.reportUnavailable).not.toHaveBeenCalled();
});
