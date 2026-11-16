import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_NONCE, scanCpuBatch, type HashcashBatch, type HashcashProgress, type HashcashSettings } from "./core";
import { type GpuScanner } from "./gpu";
import { runHashcash } from "./runner";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
const original: HashcashSettings = { message: "You are the best of best!!!!", difficulty: 3, startNonce: "0" };
const originalHash = "0002a0cc7ab05331087b7525cea18a492af097a20f4c350f5b83bd7ce05f8010";
const sha256 = (message: string, nonce: bigint) => createHash("sha256").update(message + nonce.toString(), "utf8").digest("hex");
const endSettings = (count: bigint): HashcashSettings => ({ message: "boundary", difficulty: 64, startNonce: (MAX_NONCE - count + 1n).toString() });

/** Independent CPU reference for a complete parallel GPU batch, including overscan. */
function fullBatch(settings: HashcashSettings, start: bigint, count: number): HashcashBatch {
  let nonce: string | null = null;
  let hash: string | null = null;
  let lastHash = "";
  for (let offset = 0; offset < count; offset += 1) {
    const current = start + BigInt(offset);
    lastHash = sha256(settings.message, current);
    if (nonce === null && lastHash.startsWith("0".repeat(settings.difficulty))) {
      nonce = current.toString(); hash = lastHash;
    }
  }
  return { checked: count, nonce, hash, lastNonce: (start + BigInt(count - 1)).toString(), lastHash, elapsedMs: 1 };
}

function scannerFor(settings: HashcashSettings, scan = async (start: bigint, count: number) => fullBatch(settings, start, count)) {
  return { deviceLabel: "test hardware", scanBatch: vi.fn(scan), destroy: vi.fn() } satisfies GpuScanner;
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (error: Error) => void = () => undefined;
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}

describe("nonce device selection and completed counters", () => {
  it("explicit CPU never requests GPU and still finds the original SHA-256 proof", async () => {
    const createGpu = vi.fn();
    const result = await runHashcash(original, "cpu", undefined, { createGpu });
    expect(createGpu).not.toHaveBeenCalled();
    expect(result).toMatchObject({ type: "found", backend: "cpu", winnerBackend: "cpu", nonce: "3055", hash: originalHash,
      checked: "3056", cpuChecked: "3056", gpuChecked: "0" });
  });

  it.each(["auto", "gpu"] as const)("%s selects a verified GPU and counts the complete parallel batch", async mode => {
    const scanner = scannerFor(original);
    const cpuBatch = vi.fn();
    const updates: HashcashProgress[] = [];
    const result = await runHashcash(original, mode, update => updates.push(update), { createGpu: async () => scanner, cpuBatch });
    expect(result).toMatchObject({ type: "found", backend: "gpu", winnerBackend: "gpu", nonce: "3055", hash: originalHash,
      checked: "5096", cpuChecked: "0", gpuChecked: "5096" });
    expect(scanner.scanBatch.mock.calls).toEqual([[0n, 10], [10n, 90], [100n, 900], [1000n, 4096]]);
    expect(updates[0].initializing).toBe(true);
    expect(updates[0].checked).toBe("0");
    expect(cpuBatch).not.toHaveBeenCalled();
    expect(scanner.destroy).toHaveBeenCalledTimes(1);
  });

  it.each(["auto", "gpu", "hybrid"] as const)("%s transparently falls back before GPU work starts", async mode => {
    const result = await runHashcash(original, mode, undefined, { createGpu: async () => { throw new Error("WebGPU unavailable"); } });
    expect(result).toMatchObject({ type: "found", backend: "cpu", nonce: "3055", checked: "3056", cpuChecked: "3056", gpuChecked: "0" });
    expect(result.reason).toContain("WebGPU unavailable");
    expect(result.reason).toContain("CPU로 전환");
  });
});

describe("real concurrent scheduling semantics", () => {
  it("submits both independent queues before either resolves and reserves every nonce exactly once", async () => {
    const settings = endSettings(1001n);
    const cpuGate = deferred<HashcashBatch>();
    const gpuGate = deferred<HashcashBatch>();
    const reservations: { device: string; start: bigint; count: number }[] = [];
    const cpuBatch = vi.fn(async (_settings: HashcashSettings, start: bigint, count: number) => {
      reservations.push({ device: "cpu", start, count }); return cpuGate.promise;
    });
    const scanner = scannerFor(settings, async (start, count) => {
      reservations.push({ device: "gpu", start, count }); return gpuGate.promise;
    });
    const resultPromise = runHashcash(settings, "hybrid", undefined, { createGpu: async () => scanner, cpuBatch });
    await Promise.resolve(); await Promise.resolve();
    expect(reservations.map(item => item.device)).toEqual(["cpu", "gpu"]);
    expect(reservations[0].count).toBe(128);
    expect(reservations[1].count).toBe(873);
    const nonces = reservations.flatMap(range => Array.from({ length: range.count }, (_, offset) => range.start + BigInt(offset)));
    expect(new Set(nonces).size).toBe(1001);
    expect(nonces[0]).toBe(BigInt(settings.startNonce));
    expect(nonces.at(-1)).toBe(MAX_NONCE);
    cpuGate.resolve(fullBatch(settings, reservations[0].start, reservations[0].count));
    gpuGate.resolve(fullBatch(settings, reservations[1].start, reservations[1].count));
    expect(await resultPromise).toMatchObject({ type: "exhausted", backend: "hybrid", checked: "1001", cpuChecked: "128", gpuChecked: "873", nonce: MAX_NONCE.toString() });
    expect(scanner.destroy).toHaveBeenCalledTimes(1);
  });

  it("does not count an allocated CPU range that was cancelled after a GPU proof", async () => {
    const settings = { message: "cancelled CPU", difficulty: 1, startNonce: "0" };
    const cpuGate = deferred<HashcashBatch>();
    let isCancelled: () => boolean = () => false;
    const cpuBatch = vi.fn(async (_settings: HashcashSettings, start: bigint, _count: number, cancelled?: () => boolean) => {
      isCancelled = cancelled ?? (() => false);
      expect(start).toBe(0n); return cpuGate.promise;
    });
    const scanner = scannerFor(settings);
    const pending = runHashcash(settings, "hybrid", undefined, { createGpu: async () => scanner, cpuBatch });
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(isCancelled()).toBe(true);
    cpuGate.resolve({ checked: 0, nonce: null, hash: null, lastNonce: "0", lastHash: "", elapsedMs: 0 });
    const result = await pending;
    expect(result).toMatchObject({ type: "found", winnerBackend: "gpu", cpuChecked: "0", gpuChecked: "90", checked: "90" });
    expect(result.hash).toBe(sha256(settings.message, BigInt(result.nonce!)));
  });
});

describe("failed GPU reservations always return to CPU", () => {
  it("retries exactly the failed unverified range after earlier successful GPU work", async () => {
    const settings = endSettings(5001n);
    const failed = MAX_NONCE - 904n;
    const scanner = scannerFor(settings, async (start, count) => {
      if (start === failed) throw new Error("GPU disconnected");
      return fullBatch(settings, start, count);
    });
    const completed: bigint[] = [];
    const cpuBatch = vi.fn(async (given: HashcashSettings, start: bigint, count: number) => {
      const batch = await scanCpuBatch(given, start, count);
      for (let offset = 0; offset < batch.checked; offset += 1) completed.push(start + BigInt(offset));
      return batch;
    });
    const result = await runHashcash(settings, "gpu", undefined, { createGpu: async () => scanner, cpuBatch });
    expect(result).toMatchObject({ type: "exhausted", backend: "cpu", checked: "5001", cpuChecked: "905", gpuChecked: "4096", nonce: MAX_NONCE.toString() });
    expect(cpuBatch.mock.calls.map(call => [call[1], call[2]])).toEqual([[failed, 905]]);
    expect(new Set(completed).size).toBe(905);
    expect(completed[0]).toBe(failed);
    expect(completed.at(-1)).toBe(MAX_NONCE);
    expect(result.reason).toContain("GPU disconnected");
    expect(scanner.destroy).toHaveBeenCalledTimes(1);
  });

  it("retries GPU work even when the parallel CPU queue already reached the uint64 end", async () => {
    const settings = endSettings(1001n);
    const gpuGate = deferred<HashcashBatch>();
    let failedRange: { start: bigint; count: number } | undefined;
    const scanner = scannerFor(settings, async (start, count) => { failedRange = { start, count }; return gpuGate.promise; });
    const cpuRanges: { start: bigint; count: number }[] = [];
    const cpuBatch = vi.fn(async (given: HashcashSettings, start: bigint, count: number) => {
      cpuRanges.push({ start, count }); return fullBatch(given, start, count);
    });
    const pending = runHashcash(settings, "hybrid", undefined, { createGpu: async () => scanner, cpuBatch });
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(cpuRanges).toHaveLength(1);
    gpuGate.reject(new Error("late GPU loss"));
    const result = await pending;
    expect(cpuRanges[1]).toEqual(failedRange);
    expect(result).toMatchObject({ type: "exhausted", checked: "1001", cpuChecked: "1001", gpuChecked: "0" });
    expect(scanner.destroy).toHaveBeenCalledTimes(1);
  });

  it("discards an unexpected partial GPU completion and retries its complete reservation", async () => {
    const settings = endSettings(7n);
    const scanner = scannerFor(settings, async (start, count) => fullBatch(settings, start, count - 1));
    const cpuBatch = vi.fn(scanCpuBatch);
    const result = await runHashcash(settings, "gpu", undefined, { createGpu: async () => scanner, cpuBatch });
    expect(result).toMatchObject({ type: "exhausted", checked: "7", cpuChecked: "7", gpuChecked: "0", nonce: MAX_NONCE.toString() });
    expect(cpuBatch.mock.calls.map(call => [call[1], call[2]])).toEqual([[MAX_NONCE - 6n, 7]]);
    expect(result.reason).toContain("계산 범위");
    expect(scanner.destroy).toHaveBeenCalledTimes(1);
  });

  it("cleans up the GPU device if its CPU fallback also fails", async () => {
    const scanner = scannerFor(original, async () => { throw new Error("GPU failed"); });
    const result = await runHashcash(original, "gpu", undefined, { createGpu: async () => scanner, cpuBatch: async () => { throw new Error("CPU failed"); } });
    expect(result).toMatchObject({ type: "error", checked: "0", cpuChecked: "0", gpuChecked: "0", error: "CPU failed" });
    expect(scanner.destroy).toHaveBeenCalledTimes(1);
  });
});

describe("native preflight fallback does not replay a dispatched native search", () => {
  it("falls back to browser CPU when capabilities explicitly say unavailable", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ available: false, reason: "PC 전용" })));
    const cpuBatch = vi.fn(scanCpuBatch);
    const result = await runHashcash(original, "python-native", undefined, { cpuBatch });
    expect(result).toMatchObject({ type: "found", backend: "cpu", nonce: "3055", checked: "3056" });
    expect(result.reason).toContain("PC 전용");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(cpuBatch).toHaveBeenCalled();
  });

  it("does not silently replay on browser CPU when a native start response is lost", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).endsWith("capabilities")) return new Response(JSON.stringify({ available: true }));
      throw new Error("reply lost after native request was dispatched");
    });
    const cpuBatch = vi.fn(scanCpuBatch);
    const result = await runHashcash(original, "c-native", undefined, { cpuBatch });
    expect(result.type).toBe("error");
    expect(cpuBatch).not.toHaveBeenCalled();
  });

  it("does not replay when a dispatched native start returns a malformed job", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => new Response(JSON.stringify(
      String(input).endsWith("capabilities") ? { available: true } : { job: {} },
    )));
    const cpuBatch = vi.fn(scanCpuBatch);
    const result = await runHashcash(original, "c-native", undefined, { cpuBatch });
    expect(result.type).toBe("error");
    expect(cpuBatch).not.toHaveBeenCalled();
  });

  it("preserves the known native job identity to stop it after malformed status", async () => {
    vi.useFakeTimers();
    const active = {
      id: "known-native-job", status: "running", finished: false, backend: "Python CPU", reason: "local", checked: "5",
      cpuChecked: "5", gpuChecked: "0", elapsed: 1, lastNonce: "4", lastHash: sha256(original.message, 4n), error: null, result: null,
    };
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path.endsWith("capabilities")) return new Response(JSON.stringify({ available: true }));
      if (path.endsWith("start")) return new Response(JSON.stringify({ job: active }));
      return new Response(JSON.stringify({}));
    });
    const cpuBatch = vi.fn(scanCpuBatch);
    const pending = runHashcash(original, "python-native", undefined, { cpuBatch });
    await vi.advanceTimersByTimeAsync(1000);
    const result = await pending;
    expect(result).toMatchObject({ type: "error", nativeJobId: "known-native-job", checked: "5" });
    expect(cpuBatch).not.toHaveBeenCalled();
    const stop = fetch.mock.calls.find(call => String(call[0]).endsWith("stop"));
    expect(stop).toBeDefined();
    expect(JSON.parse(String(stop![1]?.body))).toEqual({ jobId: "known-native-job" });
  });
});
