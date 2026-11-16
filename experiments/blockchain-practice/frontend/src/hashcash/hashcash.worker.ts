import { type HashcashWorkerRequest, type HashcashWorkerResponse } from "./core";
import { runHashcash } from "./runner";

// This worker belongs to one browser search; the UI terminates it on stop or navigation.
const worker = globalThis as unknown as {
  onmessage: ((event: MessageEvent<HashcashWorkerRequest>) => void) | null;
  postMessage: (message: HashcashWorkerResponse) => void;
};
let started = false;

worker.onmessage = (event) => {
  const request = event.data;
  if (request?.type !== "start" || typeof request.runId !== "string" || started) return;
  started = true;
  const runId = request.runId;
  void runHashcash(request.settings, request.mode ?? "auto", (progress) => worker.postMessage({ ...progress, runId }))
    .then((result) => worker.postMessage({ ...result, runId }))
    .catch((error: unknown) => worker.postMessage({
      type: "error", runId, checked: "0", nonce: null, hash: null, elapsedMs: 0,
      error: error instanceof Error ? error.message : "해시를 계산하지 못했습니다 다시 시작하세요",
    }));
};
