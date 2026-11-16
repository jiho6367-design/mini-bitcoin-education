import { proofHash, type HashcashProgress, type HashcashSettings } from "./core";

type NativeJob = {
  id: string; status: string; finished: boolean; backend: string; reason: string;
  checked: string; cpuChecked: string; gpuChecked: string; elapsed: number;
  lastNonce: string | null; lastHash: string | null; error: string | null;
  result: { nonce: string; hash: string; verified: boolean } | null;
};

function parseJob(value: unknown): NativeJob {
  if (!value || typeof value !== "object") throw new Error("PC 엔진 응답을 확인하지 못했습니다");
  const job = value as Partial<NativeJob>;
  const decimal = (entry: unknown) => typeof entry === "string" && /^[0-9]{1,20}$/.test(entry);
  const digest = (entry: unknown) => typeof entry === "string" && /^[a-f0-9]{64}$/.test(entry);
  if (typeof job.id !== "string" || !job.id || job.id.length > 128 ||
    typeof job.finished !== "boolean" || typeof job.status !== "string" ||
    !["preparing", "running", "found", "stopped", "exhausted", "error"].includes(job.status) ||
    typeof job.backend !== "string" || typeof job.reason !== "string" ||
    !decimal(job.checked) || !decimal(job.cpuChecked) || !decimal(job.gpuChecked) ||
    typeof job.elapsed !== "number" || !Number.isFinite(job.elapsed) || job.elapsed < 0 ||
    (job.lastNonce !== null && !decimal(job.lastNonce)) || (job.lastHash !== null && !digest(job.lastHash)) ||
    (job.error !== null && typeof job.error !== "string") ||
    (job.result !== null && (!job.result || !decimal(job.result.nonce) || !digest(job.result.hash) || typeof job.result.verified !== "boolean"))) {
    throw new Error("PC 엔진 응답을 확인하지 못했습니다");
  }
  return job as NativeJob;
}

async function call<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/hashcash-native/${path}`, {
    method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store",
    ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    let reason = "PC 전용 엔진에 연결하지 못했습니다";
    try { const data = await response.json(); reason = data.error || reason; } catch { /* Non-JSON warmup page */ }
    throw new Error(reason);
  }
  return response.json();
}

export async function runNativeHashcash(
  settings: HashcashSettings, mode: "python-native" | "c-native",
  onProgress: (progress: HashcashProgress) => void,
): Promise<HashcashProgress> {
  const capability = await call<{ available: boolean; reason: string; cpuCount?: number }>("capabilities");
  if (!capability.available) throw new Error(capability.reason || "Python·C 모드는 PC에서 실행할 때 사용할 수 있습니다");
  let job: NativeJob | null = null;
  const snapshot = (current: NativeJob, type: HashcashProgress["type"]): HashcashProgress => ({
    type, checked: current.checked, nonce: current.result?.nonce ?? current.lastNonce,
    hash: current.result?.hash ?? current.lastHash, elapsedMs: current.elapsed * 1000,
    backend: BigInt(current.gpuChecked) > 0n ? BigInt(current.cpuChecked) > 0n ? "hybrid" : "gpu" : "cpu",
    cpuChecked: current.cpuChecked, gpuChecked: current.gpuChecked,
    deviceLabel: current.backend.startsWith("CPU") ? `Python · ${current.backend}` : current.backend, reason: current.reason,
    initializing: current.status === "preparing", nativeJobId: current.id,
    error: current.error || undefined,
  });
  try {
    const workers = Math.min(mode === "c-native" ? 4 : 1, Math.max(1, capability.cpuCount ?? 1));
    job = parseJob((await call<{ job: unknown }>("start", { ...settings, mode, workers })).job);
    while (true) {
      if (job.finished) {
        if (job.status === "found" && job.result?.verified) {
          const digest = await proofHash(settings.message, job.result.nonce);
          if (digest !== job.result.hash || !digest.startsWith("0".repeat(settings.difficulty))) {
            return { ...snapshot(job, "error"), error: "PC 엔진의 해시 재검증에 실패했습니다" };
          }
          return snapshot(job, "found");
        }
        if (job.status === "exhausted") return snapshot(job, "exhausted");
        return { ...snapshot(job, "error"), error: job.error || "PC 엔진의 탐색이 중지됐습니다 다시 시작하세요" };
      }
      onProgress(snapshot(job, "progress"));
      await new Promise((resolve) => setTimeout(resolve, 250));
      job = parseJob((await call<{ job: unknown }>(`status?jobId=${encodeURIComponent(job.id)}`)).job);
    }
  } catch {
    if (!job) return {
      type: "error", checked: "0", nonce: null, hash: null, elapsedMs: 0,
      backend: "cpu", cpuChecked: "0", gpuChecked: "0", deviceLabel: "PC 엔진 응답 확인 필요",
      error: "PC 탐색 요청이 처리됐을 수 있어 CPU 탐색을 다시 실행하지 않았습니다 잠시 후 상태를 확인하고 다시 시작하세요",
    };
    // A lost connection must not launch a duplicate CPU search over a live job.
    void fetch("/api/hashcash-native/stop", { method: "POST", credentials: "same-origin", keepalive: true,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ jobId: job.id }) }).catch(() => undefined);
    return { ...snapshot(job, "error"), initializing: false, error: "PC 엔진 연결이 끊겼습니다 잠시 후 다시 시작하세요" };
  }
}
