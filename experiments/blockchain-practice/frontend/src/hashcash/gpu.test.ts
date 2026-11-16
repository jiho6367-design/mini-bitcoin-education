import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_NONCE, proofHash } from "./core";
import { createGpuScanner, GPU_BATCH_SIZE, GpuUnavailable, gpuBatchCount, packGpuParams } from "./gpu";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("GPU uint64 dispatch boundaries", () => {
  it.each([
    [0n, 10], [8n, 2], [9n, 1], [10n, 90], [98n, 2],
    [99n, 1], [100n, 900], [999n, 1], [1000n, GPU_BATCH_SIZE],
    [9007199254740993n, GPU_BATCH_SIZE], [MAX_NONCE - 2n, 3], [MAX_NONCE, 1],
  ])("bounds a dispatch from %s to %s actual nonces", (start, expected) => {
    expect(gpuBatchCount(start, GPU_BATCH_SIZE)).toBe(expected);
    const last = start + BigInt(expected - 1);
    expect(last).toBeLessThanOrEqual(MAX_NONCE);
    expect(last.toString().length).toBe(start.toString().length);
  });

  it("rejects out-of-range starts and unbounded dispatch sizes", () => {
    for (const start of [-1n, MAX_NONCE + 1n]) expect(() => gpuBatchCount(start, 1)).toThrow(GpuUnavailable);
    for (const count of [0, -1, 1.5, NaN, Infinity, GPU_BATCH_SIZE + 1]) {
      expect(() => gpuBatchCount(0n, count)).toThrow(GpuUnavailable);
    }
  });
});

describe("GPU input ABI matches original Hashcash bytes", () => {
  it.each(["", "You are the best of best!!!!", " 한글🙂\n", "a\0b", "a".repeat(35)])(
    "packs exact message and uint64 decimal bytes for %j", async message => {
      const nonce = MAX_NONCE.toString();
      const params = packGpuParams({ message, difficulty: 64, startNonce: "0" }, MAX_NONCE, 4096);
      const messageLength = Buffer.byteLength(message, "utf8");
      expect(params.length).toBe(59);
      expect([...params.slice(0, 4)]).toEqual([1, messageLength, 20, 64]);
      const source = new TextDecoder().decode(new Uint8Array([
        ...params.slice(4, 4 + messageLength), ...params.slice(39, 59),
      ]));
      expect(source).toBe(message + nonce);
      expect(createHash("sha256").update(source, "utf8").digest("hex")).toBe(await proofHash(message, nonce));
      expect(params.slice(4 + messageLength, 39).every(value => value === 0)).toBe(true);
    },
  );

  it("uses the actual start argument without reducing nonce precision", () => {
    const params = packGpuParams({ message: "x", difficulty: 3, startNonce: "0" }, 9007199254740993n, 32);
    expect(new TextDecoder().decode(new Uint8Array(params.slice(39, 39 + params[2])))).toBe("9007199254740993");
    expect(params[0]).toBe(32);
  });

  it("keeps the original GPU single-block boundary in UTF-8 bytes", () => {
    expect(() => packGpuParams({ message: "한".repeat(11) + "ab", difficulty: 1, startNonce: "0" }, 0n, 1)).not.toThrow();
    for (const message of ["a".repeat(36), "한".repeat(12), "🙂".repeat(9)]) {
      expect(() => packGpuParams({ message, difficulty: 1, startNonce: "0" }, 0n, 1)).toThrow(/35바이트/);
    }
  });
});

describe("GPU availability never labels software CPU as hardware GPU", () => {
  it("rejects an unsupported browser before starting work", async () => {
    vi.stubGlobal("navigator", {});
    await expect(createGpuScanner({ message: "x", difficulty: 3, startNonce: "0" })).rejects.toThrow(/WebGPU/);
  });

  it("rejects a missing adapter or software fallback without requesting a device", async () => {
    const requestDevice = vi.fn();
    for (const adapter of [null, { info: { isFallbackAdapter: true }, requestDevice }]) {
      const requestAdapter = vi.fn().mockResolvedValue(adapter);
      vi.stubGlobal("navigator", { gpu: { requestAdapter } });
      await expect(createGpuScanner({ message: "x", difficulty: 3, startNonce: "0" })).rejects.toBeInstanceOf(GpuUnavailable);
      expect(requestAdapter).toHaveBeenCalledWith({ powerPreference: "high-performance" });
    }
    expect(requestDevice).not.toHaveBeenCalled();
  });

  it("rejects long messages before requesting hardware so CPU fallback remains immediate", async () => {
    const requestAdapter = vi.fn();
    vi.stubGlobal("navigator", { gpu: { requestAdapter } });
    await expect(createGpuScanner({ message: "a".repeat(36), difficulty: 3, startNonce: "0" })).rejects.toThrow(/35바이트/);
    expect(requestAdapter).not.toHaveBeenCalled();
  });

  it("destroys the device if shader compilation fails", async () => {
    const destroy = vi.fn();
    const device = {
      destroy, lost: new Promise(() => undefined), addEventListener: vi.fn(),
      createShaderModule: vi.fn().mockReturnValue({ getCompilationInfo: async () => ({ messages: [{ type: "error" }] }) }),
    };
    vi.stubGlobal("navigator", { gpu: { requestAdapter: async () => ({
      info: { isFallbackAdapter: false }, requestDevice: async () => device,
    }) } });
    await expect(createGpuScanner({ message: "x", difficulty: 3, startNonce: "0" })).rejects.toThrow(/프로그램/);
    expect(destroy).toHaveBeenCalledTimes(1);
  });

  it("destroys a device that arrives after initialization timed out", async () => {
    vi.useFakeTimers();
    let deliver: (device: GPUDevice) => void = () => undefined;
    const deviceRequest = new Promise<GPUDevice>(resolve => { deliver = resolve; });
    const destroy = vi.fn();
    vi.stubGlobal("navigator", { gpu: { requestAdapter: async () => ({
      info: { isFallbackAdapter: false }, requestDevice: () => deviceRequest,
    }) } });
    const result = createGpuScanner({ message: "x", difficulty: 3, startNonce: "0" }).catch(error => error);
    await vi.advanceTimersByTimeAsync(10001);
    expect(await result).toBeInstanceOf(GpuUnavailable);
    deliver({ destroy } as unknown as GPUDevice);
    await Promise.resolve();
    expect(destroy).toHaveBeenCalledTimes(1);
  });
});
