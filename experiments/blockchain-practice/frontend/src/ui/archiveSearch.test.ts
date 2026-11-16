import { afterEach, describe, expect, it, vi } from "vitest";
import { archiveSearchParams } from "./archiveSearch";

afterEach(() => vi.unstubAllEnvs());

describe("저장 날짜의 현지 일자 검색", () => {
  it("한국의 10월 30일에 표시되는 UTC 전날 밤 기록도 포함한다", () => {
    vi.stubEnv("TZ", "Asia/Seoul");
    const params = archiveSearchParams({ q: " 수업 ", date: "2026-10-30" }, 0);
    expect(params.get("q")).toBe("수업");
    expect(params.get("updatedFrom")).toBe("2026-10-29T15:00:00.000Z");
    expect(params.get("updatedUntil")).toBe("2026-10-30T15:00:00.000Z");
    const saved = Date.parse("2026-10-29T22:30:00+00:00");
    expect(saved).toBeGreaterThanOrEqual(Date.parse(params.get("updatedFrom")!));
    expect(saved).toBeLessThan(Date.parse(params.get("updatedUntil")!));
  });

  it("서머타임 시작일은 24시간을 더하지 않고 다음 자정까지 검색한다", () => {
    vi.stubEnv("TZ", "America/New_York");
    const params = archiveSearchParams({ q: "", date: "2026-03-08" }, 20);
    expect(params.get("updatedFrom")).toBe("2026-03-08T05:00:00.000Z");
    expect(params.get("updatedUntil")).toBe("2026-03-09T04:00:00.000Z");
    expect(params.get("offset")).toBe("20");
  });

  it("날짜가 없으면 이름·페이지 조건만 보낸다", () => {
    const params = archiveSearchParams({ q: "수업", date: "" }, 40);
    expect(params.has("updatedFrom")).toBe(false);
    expect(params.has("updatedUntil")).toBe(false);
    expect(params.get("offset")).toBe("40");
  });
});
