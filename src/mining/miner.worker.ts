import { calculateNonce, validPow } from "../core/block";
import type { Block } from "../core/types";
import type { MiningCommand, MiningUpdate } from "./protocol";

let jobId = "";
let candidate: Block | null = null;
let attempts = 0;
let elapsedMs = 0;
let running = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let latest: Block | undefined;
const send = (type: MiningUpdate["type"], message?: string) =>
  postMessage({
    type,
    jobId,
    attempts,
    elapsedMs,
    block: latest,
    message,
  } satisfies MiningUpdate);

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
      if (found || single) break;
    } while (performance.now() - start < 40);
    elapsedMs += performance.now() - start;
    if (found) {
      running = false;
      send("found");
      candidate = null;
      return;
    }
    send(single ? "paused" : "progress");
    if (running) timer = setTimeout(() => batch(false), 30);
  } catch (e) {
    running = false;
    send("error", e instanceof Error ? e.message : "채굴 계산 오류");
    candidate = null;
  }
}

self.onmessage = (event: MessageEvent<MiningCommand>) => {
  const cmd = event.data;
  if (cmd.type === "start") {
    clearTimeout(timer);
    jobId = cmd.jobId;
    candidate = cmd.candidate;
    attempts = 0;
    elapsedMs = 0;
    latest = undefined;
    running = !cmd.step;
    batch(cmd.step);
    return;
  }
  if (cmd.jobId !== jobId) return;
  if (cmd.type === "pause") {
    running = false;
    clearTimeout(timer);
    send("paused");
  }
  if (cmd.type === "stop") {
    running = false;
    clearTimeout(timer);
    candidate = null;
    send("stopped");
  }
  if (cmd.type === "resume" && candidate && !running) {
    running = true;
    batch(false);
  }
  if (cmd.type === "step" && candidate && !running) batch(true);
};
