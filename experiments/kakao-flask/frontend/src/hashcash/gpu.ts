/// <reference types="@webgpu/types" />
import { MAX_NONCE, proofHash, validateSettings, type HashcashSettings } from "./core";
import { HASHCASH_WGSL } from "./sha256.wgsl";

export const GPU_BATCH_SIZE = 4096;
export const GPU_MAX_MESSAGE_BYTES = 35;
const PARAM_WORDS = 4 + 35 + 20;
const RESULT_WORDS = 1 + 8 + 8;
const NOT_FOUND = 0xffffffff;
const encoder = new TextEncoder();

export class GpuUnavailable extends Error {
  constructor(message: string) { super(message); this.name = "GpuUnavailable"; }
}
export type GpuBatch = {
  checked: number;
  nonce: string | null;
  hash: string | null;
  lastNonce: string;
  lastHash: string;
  elapsedMs: number;
};
export type GpuScanner = {
  deviceLabel: string;
  scanBatch(startNonce: bigint, count: number): Promise<GpuBatch>;
  destroy(): void;
};

/** Bound one dispatch without rounding a uint64 nonce through Number. */
export function gpuBatchCount(startNonce: bigint, requested: number): number {
  if (startNonce < 0n || startNonce > MAX_NONCE) throw new GpuUnavailable("GPU 넌스 범위를 벗어났습니다");
  if (!Number.isInteger(requested) || requested < 1 || requested > GPU_BATCH_SIZE) {
    throw new GpuUnavailable(`GPU 계산 묶음은 1~${GPU_BATCH_SIZE}회로 지정하세요`);
  }
  const decimalEnd = 10n ** BigInt(startNonce.toString().length) - 1n;
  const end = decimalEnd < MAX_NONCE ? decimalEnd : MAX_NONCE;
  const available = end - startNonce + 1n;
  return available < BigInt(requested) ? Number(available) : requested;
}

export function packGpuParams(settings: HashcashSettings, startNonce: bigint, count: number): Uint32Array<ArrayBuffer> {
  const normalized = validateSettings(settings);
  const message = encoder.encode(normalized.message);
  if (message.length > GPU_MAX_MESSAGE_BYTES) {
    throw new GpuUnavailable("GPU는 메시지 35바이트까지 지원합니다 긴 메시지는 CPU로 계산합니다");
  }
  const actualCount = gpuBatchCount(startNonce, count);
  const digits = encoder.encode(startNonce.toString());
  const values = new Uint32Array(PARAM_WORDS);
  values.set([actualCount, message.length, digits.length, normalized.difficulty]);
  values.set(message, 4);
  values.set(digits, 4 + 35);
  return values;
}

function wordsToHex(values: Uint32Array, offset: number): string {
  return Array.from(values.slice(offset, offset + 8), word => word.toString(16).padStart(8, "0")).join("");
}

async function timed<T>(promise: Promise<T>, message: string, milliseconds = 10000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<T>((_, reject) => {
      timer = setTimeout(() => reject(new GpuUnavailable(message)), milliseconds);
    })]);
  } finally { if (timer !== undefined) clearTimeout(timer); }
}

export async function createGpuScanner(rawSettings: HashcashSettings): Promise<GpuScanner> {
  const settings = validateSettings(rawSettings);
  // Fail before requesting hardware for an input unsupported by the original one-block GPU path.
  packGpuParams(settings, BigInt(settings.startNonce), 1);
  if (!globalThis.navigator?.gpu) {
    throw new GpuUnavailable("이 브라우저에서는 WebGPU를 지원하지 않습니다");
  }
  let device: GPUDevice | undefined;
  const buffers: GPUBuffer[] = [];
  let destroyed = false;
  let failure = "";
  const destroy = () => {
    if (destroyed) return;
    destroyed = true;
    for (const buffer of buffers) buffer.destroy();
    device?.destroy();
  };
  try {
    const adapter = await timed(navigator.gpu.requestAdapter({ powerPreference: "high-performance" }), "GPU 장치를 확인하는 데 시간이 오래 걸립니다");
    if (!adapter) throw new GpuUnavailable("사용 가능한 GPU 장치를 찾지 못했습니다");
    if (adapter.info.isFallbackAdapter || (adapter as GPUAdapter & { isFallbackAdapter?: boolean }).isFallbackAdapter) {
      throw new GpuUnavailable("하드웨어 GPU를 사용할 수 없어 CPU로 계산합니다");
    }
    const deviceRequest = adapter.requestDevice();
    // A delayed device may arrive after the timeout has already selected CPU.
    void deviceRequest.then(lateDevice => { if (destroyed) lateDevice.destroy(); }, () => undefined);
    device = await timed(deviceRequest, "GPU 장치를 준비하는 데 시간이 오래 걸립니다");
    const gpuDevice = device;
    void gpuDevice.lost.then(info => {
      if (!destroyed) failure = `GPU 연결이 끊겼습니다${info.message ? " 다시 CPU로 계산합니다" : ""}`;
    });
    gpuDevice.addEventListener("uncapturederror", () => { failure = "GPU 계산 중 장치 오류가 발생했습니다"; });
    const module = gpuDevice.createShaderModule({ code: HASHCASH_WGSL, label: "Hashcash SHA-256" });
    const compilation = await timed(module.getCompilationInfo(), "GPU 계산 프로그램을 준비하는 데 시간이 오래 걸립니다");
    if (compilation.messages.some(item => item.type === "error")) {
      throw new GpuUnavailable("GPU 계산 프로그램을 준비하지 못했습니다");
    }
    const layout = gpuDevice.createBindGroupLayout({ entries: [
      { binding: 0, visibility: GPUShaderStage.COMPUTE, buffer: { type: "read-only-storage" } },
      { binding: 1, visibility: GPUShaderStage.COMPUTE, buffer: { type: "storage" } },
    ] });
    const pipelineLayout = gpuDevice.createPipelineLayout({ bindGroupLayouts: [layout] });
    const [search, resolve] = await timed(Promise.all([
      gpuDevice.createComputePipelineAsync({ layout: pipelineLayout, compute: { module, entryPoint: "search" } }),
      gpuDevice.createComputePipelineAsync({ layout: pipelineLayout, compute: { module, entryPoint: "resolve" } }),
    ]), "GPU 계산 프로그램을 준비하는 데 시간이 오래 걸립니다");
    const input = gpuDevice.createBuffer({ size: PARAM_WORDS * 4, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST });
    const output = gpuDevice.createBuffer({ size: RESULT_WORDS * 4, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST });
    const readback = gpuDevice.createBuffer({ size: RESULT_WORDS * 4, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    buffers.push(input, output, readback);
    const bindGroup = gpuDevice.createBindGroup({ layout, entries: [
      { binding: 0, resource: { buffer: input } }, { binding: 1, resource: { buffer: output } },
    ] });
    let busy = false;
    const runBatch = async (batchSettings: HashcashSettings, startNonce: bigint, requested: number): Promise<GpuBatch> => {
      if (destroyed || failure) throw new GpuUnavailable(failure || "GPU 계산이 종료됐습니다");
      if (busy) throw new GpuUnavailable("앞의 GPU 계산이 끝난 뒤 다시 시작하세요");
      busy = true;
      const started = performance.now();
      let mapped = false;
      let scope = false;
      try {
        const params = packGpuParams(batchSettings, startNonce, requested);
        const checked = params[0];
        const initial = new Uint32Array(RESULT_WORDS);
        initial[0] = NOT_FOUND;
        gpuDevice.pushErrorScope("validation");
        scope = true;
        gpuDevice.queue.writeBuffer(input, 0, params);
        gpuDevice.queue.writeBuffer(output, 0, initial);
        const command = gpuDevice.createCommandEncoder();
        const firstPass = command.beginComputePass();
        firstPass.setPipeline(search); firstPass.setBindGroup(0, bindGroup);
        firstPass.dispatchWorkgroups(Math.ceil(checked / 64)); firstPass.end();
        const secondPass = command.beginComputePass();
        secondPass.setPipeline(resolve); secondPass.setBindGroup(0, bindGroup);
        secondPass.dispatchWorkgroups(1); secondPass.end();
        command.copyBufferToBuffer(output, 0, readback, 0, RESULT_WORDS * 4);
        gpuDevice.queue.submit([command.finish()]);
        await timed(readback.mapAsync(GPUMapMode.READ), "GPU 응답을 받지 못했습니다");
        mapped = true;
        const values = new Uint32Array(readback.getMappedRange().slice(0));
        readback.unmap(); mapped = false;
        const validation = await gpuDevice.popErrorScope(); scope = false;
        if (validation || failure || destroyed) throw new GpuUnavailable(failure || "GPU 계산 결과를 읽지 못했습니다");
        const lastNonce = (startNonce + BigInt(checked - 1)).toString();
        const lastHash = wordsToHex(values, 9);
        if (await proofHash(batchSettings.message, lastNonce) !== lastHash) {
          throw new GpuUnavailable("GPU 표본의 SHA-256 재검증에 실패했습니다");
        }
        const first = values[0];
        if (first !== NOT_FOUND && first >= checked) throw new GpuUnavailable("GPU 결과의 넌스 범위가 올바르지 않습니다");
        const nonce = first === NOT_FOUND ? null : (startNonce + BigInt(first)).toString();
        const hash = nonce === null ? null : wordsToHex(values, 1);
        if (nonce !== null && (await proofHash(batchSettings.message, nonce) !== hash || !hash!.startsWith("0".repeat(batchSettings.difficulty)))) {
          throw new GpuUnavailable("GPU 해답의 SHA-256 재검증에 실패했습니다");
        }
        return { checked, nonce, hash, lastNonce, lastHash, elapsedMs: Math.max(0, performance.now() - started) };
      } catch (error) {
        if (error instanceof GpuUnavailable) throw error;
        throw new GpuUnavailable("GPU 계산을 마치지 못했습니다");
      } finally {
        if (mapped) readback.unmap();
        if (scope) await gpuDevice.popErrorScope().catch(() => undefined);
        busy = false;
      }
    };
    const fixed: HashcashSettings = { message: "You are the best of best!!!!", difficulty: 3, startNonce: "3055" };
    const positive = await runBatch(fixed, 3055n, 1);
    if (positive.nonce !== "3055" || positive.hash !== "0002a0cc7ab05331087b7525cea18a492af097a20f4c350f5b83bd7ce05f8010") {
      throw new GpuUnavailable("GPU 자체 검증을 통과하지 못했습니다");
    }
    const negative = await runBatch({ ...fixed, difficulty: 64 }, 0n, 1);
    if (negative.nonce !== null) throw new GpuUnavailable("GPU 목표 조건 자체 검증을 통과하지 못했습니다");
    const info = adapter.info;
    const deviceLabel = info.description || [info.vendor, info.architecture].filter(Boolean).join(" · ") || "WebGPU 하드웨어";
    return { deviceLabel, scanBatch: (startNonce, count) => runBatch(settings, startNonce, count), destroy };
  } catch (error) {
    destroy();
    if (error instanceof GpuUnavailable) throw error;
    throw new GpuUnavailable("GPU 장치를 준비하지 못했습니다");
  }
}
