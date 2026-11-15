import { afterEach, expect, it, vi } from "vitest";
import { archiveRequest } from "./PracticeArchive";
import { serverConnection } from "../api/serverConnection";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("결과 HTML 내보내기는 서버 준비 응답으로 오인하지 않는다", async () => {
  const notify = vi.spyOn(serverConnection, "reportUnavailable");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<h1>저장한 실습</h1>", {
    headers: { "Content-Type": "text/html" },
  })));
  await expect(archiveRequest("/api/education/practices/saved/html")).resolves.toContain("저장한 실습");
  expect(notify).not.toHaveBeenCalled();
});

it("보관함 목록 대신 받은 준비 HTML은 저장 결과로 승인하지 않는다", async () => {
  const notify = vi.spyOn(serverConnection, "reportUnavailable");
  const send = vi.fn().mockResolvedValue(new Response("<h1>Starting service</h1>", {
    headers: { "Content-Type": "text/html" },
  }));
  vi.stubGlobal("fetch", send);
  await expect(archiveRequest("/api/education/practices")).rejects.toThrow("서버 준비 중");
  expect(notify).toHaveBeenCalled();
  expect(send).toHaveBeenCalledTimes(1);
});

it("저장 요청 응답을 잃으면 처리 여부를 안내하고 자동으로 재전송하지 않는다", async () => {
  vi.spyOn(serverConnection, "reportUnavailable");
  const send = vi.fn().mockResolvedValue(new Response("Bad Gateway", { status: 502 }));
  vi.stubGlobal("fetch", send);
  await expect(archiveRequest("/api/education/practices/saved/save", "POST", {}))
    .rejects.toThrow("작업이 처리됐을 수");
  expect(send).toHaveBeenCalledTimes(1);
});

it("보관함 접근 거부는 원래 오류를 유지하고 준비 상태로 바꾸지 않는다", async () => {
  const notify = vi.spyOn(serverConnection, "reportUnavailable");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    error: { code: "FORBIDDEN", message: "접속 권한을 확인하세요" },
  }, { status: 403 })));
  await expect(archiveRequest("/api/education/classroom/practices"))
    .rejects.toMatchObject({ status: 403, code: "FORBIDDEN", message: "접속 권한을 확인하세요" });
  expect(notify).not.toHaveBeenCalled();
});
