import { MAX_NONCE, scanCpuBatch, validateSettings, type HashcashBatch,
  type HashcashMode, type HashcashProgress, type HashcashSettings } from "./core";
import { createGpuScanner, type GpuScanner } from "./gpu";
import { runNativeHashcash } from "./nativeClient";

type Dependencies = {
  createGpu: (settings: HashcashSettings) => Promise<GpuScanner>;
  cpuBatch: typeof scanCpuBatch;
};

/** Each reservation is disjoint, including retries after a failed GPU batch. */
export async function runHashcash(
  rawSettings: unknown, requestedMode: HashcashMode = "auto",
  onProgress: (progress: HashcashProgress) => void = () => undefined,
  dependencies: Partial<Dependencies> = {},
): Promise<HashcashProgress> {
  const settings = validateSettings(rawSettings);
  const started = performance.now();
  const createGpu = dependencies.createGpu ?? createGpuScanner;
  const cpuBatch = dependencies.cpuBatch ?? scanCpuBatch;
  let backend: "cpu" | "gpu" | "hybrid" = "cpu";
  let label = "브라우저 CPU · Web Crypto";
  let reason = "이 기기의 CPU로 계산합니다";
  let cpuChecked = 0n;
  let gpuChecked = 0n;
  let lastNonce: string | null = null;
  let lastHash: string | null = null;
  let winner: { nonce: string; hash: string; device: "cpu" | "gpu" } | null = null;
  let next = BigInt(settings.startNonce);
  let scanner: GpuScanner | null = null;
  let failure: unknown;
  let lastReport = 0;
  const retries: { start: bigint; count: number }[] = [];
  const snapshot = (type: HashcashProgress["type"], initializing = false): HashcashProgress => ({
    type, checked: (cpuChecked + gpuChecked).toString(),
    nonce: winner?.nonce ?? lastNonce, hash: winner?.hash ?? lastHash,
    elapsedMs: performance.now() - started, backend, deviceLabel: label, reason,
    cpuChecked: cpuChecked.toString(), gpuChecked: gpuChecked.toString(), initializing,
    winnerBackend: winner?.device,
  });
  const emit = (force = false) => {
    if (force || performance.now() - lastReport >= 100) {
      lastReport = performance.now(); onProgress(snapshot("progress"));
    }
  };
  if (requestedMode === "python-native" || requestedMode === "c-native") {
    try { return await runNativeHashcash(settings, requestedMode, onProgress); }
    catch (error) {
      reason = `${error instanceof Error ? error.message : "PC 전용 엔진에 연결하지 못했습니다"} 브라우저 CPU로 전환했습니다`;
      requestedMode = "cpu";
    }
  }
  if (!["auto", "cpu", "gpu", "hybrid"].includes(requestedMode)) {
    throw new Error("연산 방식을 확인하세요");
  }
  if (requestedMode !== "cpu") {
    reason = "GPU 사용 가능 여부와 실제 해시를 확인합니다";
    onProgress(snapshot("progress", true));
    try {
      scanner = await createGpu(settings);
      backend = requestedMode === "hybrid" ? "hybrid" : "gpu";
      label = `${backend === "hybrid" ? "브라우저 CPU + " : ""}${scanner.deviceLabel} · WebGPU`;
      reason = backend === "hybrid" ? "CPU와 GPU가 서로 다른 넌스 구간을 동시에 계산합니다" : "이 기기의 GPU로 병렬 계산합니다";
    } catch (error) {
      reason = `${error instanceof Error ? error.message : "GPU를 준비하지 못했습니다"} CPU로 전환했습니다`;
    }
  }
  emit(true);
  const reserve = (size: number) => {
    if (winner || failure || next > MAX_NONCE) return null;
    const start = next;
    const decimalEnd = 10n ** BigInt(start.toString().length);
    const remaining = (decimalEnd - start) < (MAX_NONCE - start + 1n)
      ? decimalEnd - start : MAX_NONCE - start + 1n;
    const count = Number(remaining < BigInt(size) ? remaining : BigInt(size));
    next += BigInt(count);
    return { start, count };
  };
  const accept = (batch: HashcashBatch, device: "cpu" | "gpu") => {
    if (device === "cpu") cpuChecked += BigInt(batch.checked);
    else gpuChecked += BigInt(batch.checked);
    if (batch.checked > 0) { lastNonce = batch.lastNonce; lastHash = batch.lastHash; }
    if (!winner && batch.nonce !== null && batch.hash !== null) {
      winner = { nonce: batch.nonce, hash: batch.hash, device };
    }
    emit();
  };
  const runCpu = async () => {
    while (!winner && !failure) {
      const range = retries.shift() ?? reserve(128);
      if (!range) break;
      const batch = await cpuBatch(settings, range.start, range.count, () => winner !== null || failure !== undefined);
      accept(batch, "cpu");
      // Some Web Crypto promises settle within the microtask queue. Yield a
      // real task so GPU readback/device-loss callbacks can run concurrently.
      if (!winner && !failure) await new Promise((resolve) => setTimeout(resolve, 0));
    }
  };
  const runGpu = async () => {
    while (!winner && !failure && scanner) {
      const range = reserve(4096);
      if (!range) break;
      try {
        const batch = await scanner.scanBatch(range.start, range.count);
        if (batch.checked !== range.count) throw new Error("GPU 계산 범위가 일치하지 않습니다");
        accept(batch, "gpu");
      } catch (error) {
        // A failed batch has no verified completion report. Retry its full range.
        retries.push(range);
        backend = "cpu"; label = "브라우저 CPU · Web Crypto";
        reason = `${error instanceof Error ? error.message : "GPU 계산이 중단됐습니다"} 미완료 구간부터 CPU로 전환했습니다`;
        emit(true);
        break;
      }
    }
  };
  try {
    // Both tasks submit work before awaiting each other; Web Crypto and WebGPU
    // are independently asynchronous device queues, never two serial phases.
    const guarded = async (task: () => Promise<void>) => {
      try { await task(); } catch (error) { failure = error; }
    };
    await Promise.all([
      guarded(async () => { if (backend !== "gpu") await runCpu(); }),
      guarded(async () => { if (scanner) await runGpu(); }),
    ]);
    if (retries.length && !winner && !failure) await runCpu();
    if (failure) throw failure;
    return snapshot(winner ? "found" : "exhausted");
  } catch (error) {
    return { ...snapshot("error"), error: error instanceof Error ? error.message : "계산을 마치지 못했습니다" };
  } finally {
    scanner?.destroy();
  }
}
