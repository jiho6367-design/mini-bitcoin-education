import { describe, expect, it } from "vitest";
import { addHistory, HISTORY_LIMIT, parseHistory, type HistoryRecord } from "./history";

const record: HistoryRecord = {
  id: "sample-proof", participantName: "팀원", message: "You are the best of best!!!!",
  difficulty: 3, startNonce: "0", nonce: "3055",
  hash: "0002a0cc7ab05331087b7525cea18a492af097a20f4c350f5b83bd7ce05f8010",
  checked: "3056", elapsedMs: 125.5, createdAt: "2026-11-17T00:00:00.000Z",
};

describe("nonce result browser history", () => {
  it("preserves exact original conditions and decimal nonce strings on reload", () => {
    const large = { ...record, id: "large-nonce", startNonce: "9007199254740993", nonce: "9007199254741000", checked: "8" };
    expect(parseHistory(JSON.stringify([record, large]))).toEqual([record, large]);
  });

  it("keeps old CPU records and new actual device metadata in the same storage", () => {
    const gpu = { ...record, id: "gpu-proof", requestedMode: "gpu", backend: "gpu", deviceLabel: "NVIDIA · WebGPU",
      cpuChecked: "0", gpuChecked: "4096", checked: "4096", winnerBackend: "gpu" };
    const hybrid = { ...record, id: "hybrid-proof", requestedMode: "hybrid", backend: "hybrid",
      cpuChecked: "37", gpuChecked: "4096", checked: "4133", winnerBackend: "cpu" };
    const fallback = { ...record, id: "fallback-proof", requestedMode: "auto", backend: "cpu",
      deviceLabel: "브라우저 CPU", reason: "GPU를 사용할 수 없어 CPU로 전환했습니다", cpuChecked: "3056", gpuChecked: "0" };
    const native = { ...hybrid, id: "native-proof", requestedMode: "c-native", deviceLabel: "C CPU+CUDA" };
    expect(parseHistory(JSON.stringify([record, gpu, hybrid, fallback, native]))).toEqual([record, gpu, hybrid, fallback, native]);
  });

  it("rejects corrupt optional device fields while preserving old records", () => {
    const invalid = [
      { ...record, requestedMode: "cuda" }, { ...record, backend: "native" },
      { ...record, winnerBackend: "hybrid" }, { ...record, deviceLabel: 123 },
      { ...record, reason: "x".repeat(2001) }, { ...record, cpuChecked: "-1" },
      { ...record, gpuChecked: 4096 }, { ...record, gpuChecked: "1e10" },
    ];
    expect(parseHistory(JSON.stringify([...invalid, record]))).toEqual([record]);
  });

  it("ignores malformed or incompatible records without losing valid ones", () => {
    const invalid = [null, {}, { ...record, nonce: 3055 }, { ...record, difficulty: 65 },
      { ...record, checked: "-3" }, { ...record, elapsedMs: -5 },
      { ...record, createdAt: "not-a-date" }, { ...record, nonce: "18446744073709551616" },
      { ...record, message: "한".repeat(1400) }, { ...record, hash: "<script>bad</script>" }];
    expect(parseHistory(JSON.stringify([...invalid, record]))).toEqual([record]);
  });

  it("limits history size, removes repeated ids, and tolerates malformed storage", () => {
    expect(parseHistory("not JSON")).toEqual([]);
    expect(parseHistory("[]".repeat(600000))).toEqual([]);
    expect(parseHistory(JSON.stringify({ records: [record] }))).toEqual([]);
    expect(parseHistory(JSON.stringify([record, record]))).toEqual([record]);
    const many = Array.from({ length: 75 }, (_, index) => ({ ...record, id: `proof-${index}` }));
    expect(parseHistory(JSON.stringify(many))).toHaveLength(HISTORY_LIMIT);
    const newest = { ...record, id: "new" };
    const updated = addHistory(many.slice(0, 50), newest);
    expect(updated).toHaveLength(HISTORY_LIMIT);
    expect(updated[0]).toEqual(newest);
    expect(addHistory(updated, newest).filter((item) => item.id === "new")).toHaveLength(1);
  });
});
