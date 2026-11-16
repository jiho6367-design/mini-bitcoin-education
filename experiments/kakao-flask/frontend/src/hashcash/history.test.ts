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
