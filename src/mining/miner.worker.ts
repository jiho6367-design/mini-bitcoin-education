import { calculateNonce, validPow } from "../core/block";
import type { Block } from "../core/types";
import {
  LEARNING_INTERVAL_MS,
  MINING_SAMPLE_LIMIT,
  type MiningCommand,
  type MiningMode,
  type MiningSample,
  type MiningUpdate,
} from "./protocol";

let jobId = "";
let candidate: Block | null = null;
let attempts = 0;
let elapsedMs = 0;
let observationMs = 0;
let mode: MiningMode = "fast";
let samples: MiningSample[] = [];
let running = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let observationStarted: number | undefined;
let latest: Block | undefined;
const send = (type: MiningUpdate["type"], message?: string) =>
  postMessage({
    type,
    jobId,
    attempts,
    mode,
    elapsedMs,
    observationMs,
    samples,
    block: latest,
    message,
  } satisfies MiningUpdate);

function clearSchedule(): void {
  clearTimeout(timer);
  timer = undefined;
  if (observationStarted !== undefined) {
    observationMs += performance.now() - observationStarted;
    observationStarted = undefined;
  }
}

function schedule(): void {
  const scheduledJob = jobId;
  if (mode === "learning") observationStarted = performance.now();
  timer = setTimeout(
    () => {
      if (scheduledJob !== jobId) return;
      clearSchedule();
      if (running) batch(false);
    },
    mode === "learning" ? LEARNING_INTERVAL_MS : 30,
  );
}

function batch(single: boolean): void {
  if (!candidate) return;
  const start = performance.now();
  let found: boolean;
  // 계산을 짧게 나누어 일시정지·중지 메시지를 처리할 기회를 만든다.
  try {
    do {
      latest = calculateNonce(candidate, attempts);
      attempts++;
      found = validPow(latest.hash, latest.difficulty);
      // 첫 묶음에서 해답을 찾아도 마지막 실패들과 실제 성공을 비교할 수 있다.
      samples.push({ nonce: latest.nonce, hash: latest.hash, passed: found });
      if (samples.length > MINING_SAMPLE_LIMIT) samples.shift();
      if (found || single || mode === "learning") break;
    } while (performance.now() - start < 40);
    elapsedMs += performance.now() - start;
    if (found) {
      running = false;
      send("found");
      candidate = null;
      return;
    }
    send(single ? "paused" : "progress");
    if (running) schedule();
  } catch (e) {
    running = false;
    send("error", e instanceof Error ? e.message : "채굴 계산 오류");
    candidate = null;
  }
}

self.onmessage = (event: MessageEvent<MiningCommand>) => {
  const cmd = event.data;
  if (cmd.type === "start") {
    clearSchedule();
    jobId = cmd.jobId;
    candidate = cmd.candidate;
    attempts = 0;
    elapsedMs = 0;
    observationMs = 0;
    mode = cmd.mode ?? "fast";
    samples = [];
    latest = undefined;
    running = !cmd.step;
    batch(cmd.step);
    return;
  }
  if (cmd.jobId !== jobId) return;
  if (cmd.type === "pause") {
    if (!candidate) return;
    running = false;
    clearSchedule();
    send("paused");
  }
  if (cmd.type === "stop") {
    running = false;
    clearSchedule();
    candidate = null;
    send("stopped");
  }
  if (cmd.type === "resume" && candidate && !running) {
    running = true;
    batch(false);
  }
  if (cmd.type === "step" && candidate && !running) batch(true);
};
