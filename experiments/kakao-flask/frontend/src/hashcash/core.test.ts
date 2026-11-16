import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_NONCE, proofHash, scanHashcash, validateSettings, type HashcashProgress } from "./core";

afterEach(() => vi.restoreAllMocks());

const referenceProofs = [
  ["Pyeongtaek University!", "8656", "00028e62333a32b5b6bdf41418f42725f8c21d84dc0b7ff60e4480cc11a8677b"],
  ["You are the best of best!!!!", "3055", "0002a0cc7ab05331087b7525cea18a492af097a20f4c350f5b83bd7ce05f8010"],
  ["한글🙂", "980", "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad"],
] as const;

describe("hashcash-only proof compatibility", () => {
  it.each(referenceProofs)("independently matches the original proof for %s", async (message, nonce, hash) => {
    expect(await proofHash(message, nonce)).toBe(hash);
    expect(createHash("sha256").update(message + nonce, "utf8").digest("hex")).toBe(hash);
  });

  it("finds the original first proof using actual SHA-256 calculations", async () => {
    const result = await scanHashcash({ message: "한글🙂", difficulty: 3, startNonce: "0" });
    expect(result).toMatchObject({ type: "found", checked: "981", nonce: "980", hash: referenceProofs[2][2] });
    expect(result.elapsedMs).toBeGreaterThanOrEqual(0);
  });

  it("preserves Unicode, empty text, spaces and line breaks exactly", async () => {
    for (const message of ["", " ", " x\n", "x\n", "한글🙂\n", "a\0b"]) {
      expect(validateSettings({ message, difficulty: 1, startNonce: "0" }).message).toBe(message);
      expect(await proofHash(message, "10"))
        .toBe(createHash("sha256").update(message + "10", "utf8").digest("hex"));
    }
    expect(await proofHash(" x\n", "0")).not.toBe(await proofHash("x", "0"));
  });

  it("keeps uint64 nonce precision beyond JavaScript safe integers", async () => {
    for (const nonce of ["9007199254740993", MAX_NONCE.toString()]) {
      const value = "  한글🙂\n";
      expect(await proofHash(value, nonce))
        .toBe(createHash("sha256").update(value + nonce, "utf8").digest("hex"));
      expect(validateSettings({ message: value, difficulty: 64, startNonce: nonce }).startNonce).toBe(nonce);
    }
  });

  it("normalizes leading zeros without changing message bytes", async () => {
    expect(validateSettings({ message: " x ", difficulty: 1, startNonce: "00012" }))
      .toEqual({ message: " x ", difficulty: 1, startNonce: "12" });
    expect(await proofHash("x", "0000")).toBe(await proofHash("x", "0"));
  });

  it("exhausts the uint64 boundary without wrapping or losing count", async () => {
    const result = await scanHashcash({ message: "end", difficulty: 64, startNonce: (MAX_NONCE - 2n).toString() });
    expect(result).toMatchObject({
      type: "exhausted", checked: "3", nonce: MAX_NONCE.toString(),
      hash: createHash("sha256").update("end" + MAX_NONCE.toString()).digest("hex"),
    });
  });

  it("reports actual completed hashes at most once per 100ms", async () => {
    let clock = 0;
    vi.spyOn(performance, "now").mockImplementation(() => { clock += 25; return clock; });
    const updates: HashcashProgress[] = [];
    const start = MAX_NONCE - 20n;
    const result = await scanHashcash({ message: "progress", difficulty: 64, startNonce: start.toString() },
      (progress) => updates.push(progress));
    expect(result).toMatchObject({ type: "exhausted", checked: "21" });
    expect(updates.length).toBeGreaterThan(0);
    for (let index = 0; index < updates.length; index += 1) {
      const update = updates[index];
      expect(update.type).toBe("progress");
      expect(BigInt(update.nonce!)).toBe(start + BigInt(update.checked) - 1n);
      expect(update.hash).toBe(createHash("sha256").update("progress" + update.nonce).digest("hex"));
      if (index > 0) expect(update.elapsedMs - updates[index - 1].elapsedMs).toBeGreaterThanOrEqual(100);
    }
  });

  it("rejects an apparent proof when independent reconstruction disagrees", async () => {
    vi.spyOn(globalThis.crypto.subtle, "digest").mockResolvedValueOnce(new ArrayBuffer(32));
    const result = await scanHashcash({ message: "independent", difficulty: 1, startNonce: "0" });
    expect(result).toMatchObject({ type: "error", checked: "1", nonce: "0" });
    expect(result.error).toContain("검증");
  });

  it("accepts 4096 UTF-8 bytes and rejects larger or malformed messages", () => {
    for (const message of ["a".repeat(4096), "🙂".repeat(1024)]) {
      expect(validateSettings({ message, difficulty: 64, startNonce: "0" }).message).toBe(message);
    }
    for (const message of ["a".repeat(4097), "🙂".repeat(1025), "\ud800", "\udc00", "x\ud800x", 123, null]) {
      expect(() => validateSettings({ message, difficulty: 1, startNonce: "0" })).toThrow();
    }
  });

  it("rejects invalid difficulty, decimal nonce and request shapes", () => {
    for (const difficulty of [0, 65, 1.5, NaN, Infinity, true, "3", null]) {
      expect(() => validateSettings({ message: "x", difficulty, startNonce: "0" })).toThrow();
    }
    for (const startNonce of ["", "-1", "+1", "1.0", "1e3", " 0", "0 ", "１２", "18446744073709551616", 1, null]) {
      expect(() => validateSettings({ message: "x", difficulty: 1, startNonce })).toThrow();
    }
    for (const settings of [null, undefined, false, "x", {}, []]) {
      expect(() => validateSettings(settings)).toThrow();
    }
  });
});
