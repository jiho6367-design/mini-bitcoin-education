/**
 * Exercise the real web form, not a private search API entry point.
 * Start an isolated HASHCASH_DATA_DIR server before running this tool.
 * GPU benchmarking has one owner: do not run other GPU/CPU tests concurrently.
 */
import { chromium } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { Buffer } from "node:buffer";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { dirname, resolve, join, basename } from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";
import { setTimeout as scheduleTimeout, clearTimeout as cancelTimeout } from "node:timers";
import os from "node:os";
import process from "node:process";
import console from "node:console";

const { fetch, AbortSignal, URL, URLSearchParams } = globalThis;
const execute = promisify(execFile);
const defaultMessage = "You are the best of best!!!!";
const allCases = [
  { id: "easy", message: defaultMessage, difficulty: 3, startNonce: "0", expectedStatus: "found" },
  { id: "ascii", message: defaultMessage, difficulty: 8, startNonce: "1000000000", expectedStatus: "found" },
  { id: "unicode", message: "한글🙂", difficulty: 8, startNonce: "1000000000", expectedStatus: "found" },
  {
    id: "fixed", message: defaultMessage, difficulty: 64,
    startNonce: ((1n << 64n) - (1n << 32n)).toString(),
    count: (1n << 32n).toString(), expectedStatus: "exhausted",
  },
];
const usage = `Usage: node scripts/hashcash-web-benchmark.mjs [options]
  --base-url URL       Isolated loopback web server (default http://127.0.0.1:5188)
  --out FILE          Raw JSON result (default work/hashcash-web-benchmark/<run-id>.json)
  --repeats N         Alternating GPU/native pairs per case, 1..31 (default 9)
  --cases IDS         Comma separated finite case IDs: easy,ascii,unicode,fixed
  --first-mode MODE   First pair order: gpu or native (default gpu)
  --no-warmups        Record measured pairs immediately; first sample is marked
  --expect-reuse      Require prepared resource hits in measured samples
  --plan              Print the finite plan without contacting a server or GPU
  --help              Show this help

This tool never starts a server or stops an existing search. Use an isolated
HASHCASH_DATA_DIR. Ports 5186 and 5187 are reserved for existing user previews.
Each search is started by an actual Edge web-form click. Only the screen's
existing 350 ms status responses are observed during the timed search.
The first sample in this process is NOT proof of a cold OS CUDA/disk cache.
`;

function options(argv) {
  const result = { baseUrl: "http://127.0.0.1:5188", repeats: 9, cases: allCases.map((item) => item.id), firstMode: "gpu", warmups: true };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--help") { result.help = true; continue; }
    if (key === "--plan") { result.plan = true; continue; }
    if (key === "--no-warmups") { result.warmups = false; continue; }
    if (key === "--expect-reuse") { result.expectReuse = true; continue; }
    const value = argv[++index];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for ${key}`);
    if (key === "--base-url") result.baseUrl = value;
    else if (key === "--out") result.out = value;
    else if (key === "--repeats") {
      if (!/^\d+$/.test(value)) throw new Error("--repeats must be an integer");
      result.repeats = Number(value);
    } else if (key === "--cases") result.cases = value.split(",");
    else if (key === "--first-mode") result.firstMode = value;
    else throw new Error(`Unknown argument: ${key}`);
  }
  if (!Number.isInteger(result.repeats) || result.repeats < 1 || result.repeats > 31) throw new Error("--repeats must be 1..31");
  if (!["gpu", "native"].includes(result.firstMode)) throw new Error("--first-mode must be gpu or native");
  if (!result.cases.length || new Set(result.cases).size !== result.cases.length || result.cases.some((id) => !allCases.some((item) => item.id === id))) {
    throw new Error("--cases must contain unique IDs from easy,ascii,unicode,fixed");
  }
  const address = new URL(result.baseUrl);
  if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname) ||
      address.username || address.password || address.search || address.hash || address.pathname !== "/") {
    throw new Error("--base-url must be an http loopback origin");
  }
  if (["5186", "5187"].includes(address.port)) throw new Error("Use an isolated port; existing preview ports 5186/5187 are protected");
  result.baseUrl = address.origin;
  return result;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function command(commandName, args) {
  try {
    const result = await execute(commandName, args, { windowsHide: true, timeout: 10_000, maxBuffer: 1_000_000 });
    return { command: commandName, args, ok: true, stdout: result.stdout.trim(), stderr: result.stderr.trim() };
  } catch (error) {
    return { command: commandName, args, ok: false, error: String(error), stdout: error.stdout?.trim() ?? "", stderr: error.stderr?.trim() ?? "" };
  }
}

async function gpuState() {
  const [device, processes] = await Promise.all([
    command("nvidia-smi", ["--query-gpu=name,driver_version,temperature.gpu,power.draw,power.limit,utilization.gpu,utilization.memory,clocks.current.graphics,clocks.current.memory,pstate", "--format=csv,noheader,nounits"]),
    command("nvidia-smi", ["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]),
  ]);
  return { capturedAt: new Date().toISOString(), device, processes };
}

async function fingerprints() {
  const files = [
    fileURLToPath(import.meta.url),
    "scripts/hashcash-web-benchmark.mjs", "hashcash_lab/server.py", "hashcash_lab/gpu.py", "hashcash_lab/gpu_resources.py",
    "hashcash_lab/native_scheduler_experiment.py", "experiments/native_scheduler.c", "experiments/native_scheduler.h",
    "experiments/native_cuda_driver.c", "experiments/native_cuda_driver.h", "src/hashcash/HashcashApp.tsx",
    "work/native_scheduler/native_scheduler.dll", "work/native_scheduler/native_scheduler.build.json",
  ];
  const result = {};
  for (const file of files) {
    try {
      const bytes = await readFile(file);
      result[file] = { sha256: createHash("sha256").update(bytes).digest("hex"), bytes: bytes.byteLength };
    } catch (error) { result[file] = { unavailable: error.code ?? String(error) }; }
  }
  return result;
}

function distribution(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  const median = ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const populationSd = Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length);
  return { count: values.length, median, min: ordered[0], max: ordered.at(-1), mean, populationSd, cv: mean ? populationSd / mean : null };
}

const metricNames = [
  "serverStartupSeconds", "serverSearchPreparationSeconds", "serverTotalPreparationSeconds",
  "serverProofSeconds", "serverProofAfterReadySeconds", "serverActiveProofSeconds", "serverLifecycleSeconds",
  "serverFixedActiveSeconds", "serverCleanupAfterProofSeconds", "domTerminalSeconds", "domReadySeconds",
  "applicationStatusResponseProofObservedSeconds", "applicationStatusResponseFinishedObservedSeconds", "postResponseSeconds",
];

function aggregate(samples, cases) {
  return cases.map((item) => {
    const selected = samples.filter((sample) => sample.phase === "measured" && sample.caseId === item.id && sample.ok);
    const metrics = {};
    for (const metric of metricNames) {
      const byMode = {};
      for (const mode of ["gpu", "native"]) {
        byMode[mode] = distribution(selected.filter((sample) => sample.mode === mode)
          .map((sample) => sample.metrics[metric]).filter((value) => Number.isFinite(value)));
      }
      if (!byMode.gpu && !byMode.native) continue;
      const pairs = [];
      for (let repetition = 0; repetition < 31; repetition += 1) {
        const gpu = selected.find((sample) => sample.mode === "gpu" && sample.repetition === repetition)?.metrics[metric];
        const native = selected.find((sample) => sample.mode === "native" && sample.repetition === repetition)?.metrics[metric];
        if (Number.isFinite(gpu) && Number.isFinite(native)) pairs.push({ repetition, gpu, native, deltaSeconds: native - gpu, nativeChangePercent: gpu ? (native / gpu - 1) * 100 : null });
      }
      metrics[metric] = {
        ...byMode,
        nativeChangeOfMediansPercent: byMode.gpu?.median && byMode.native ? (byMode.native.median / byMode.gpu.median - 1) * 100 : null,
        pairs,
        nativeWins: pairs.filter((pair) => pair.native < pair.gpu).length,
        gpuWins: pairs.filter((pair) => pair.gpu < pair.native).length,
        ties: pairs.filter((pair) => pair.gpu === pair.native).length,
        pairedChangePercent: distribution(pairs.map((pair) => pair.nativeChangePercent).filter((value) => Number.isFinite(value))),
      };
    }
    return {
      caseId: item.id, metrics,
      proofNonces: Object.fromEntries(["gpu", "native"].map((mode) => [mode, [...new Set(selected.filter((sample) => sample.mode === mode).map((sample) => sample.final?.result?.nonce).filter(Boolean))]])),
      checked: Object.fromEntries(["gpu", "native"].map((mode) => [mode, selected.filter((sample) => sample.mode === mode).map((sample) => sample.final.checked)])),
    };
  });
}

// Runs in the browser. Mutation events measure rendered results independently
// from backend clocks and keep the app's polling interval intact.
function installDomObserver({ expectedStatus }) {
  const { document, performance, MutationObserver } = globalThis;
  globalThis.__hashcashWebBenchmark?.observer?.disconnect();
  if (globalThis.__hashcashWebBenchmark?.listener) document.removeEventListener("click", globalThis.__hashcashWebBenchmark.listener, true);
  const sample = { clickAt: null, terminalAt: null, readyAt: null, sawNewSearch: false, expectedStatus };
  const inspect = () => {
    if (sample.clickAt === null) return;
    const button = document.querySelector("button.hc-primary");
    const proof = document.querySelector(".hc-proof");
    const status = document.querySelector(".hc-status");
    if (button?.disabled || !proof || !status?.classList.contains("found")) sample.sawNewSearch = true;
    const terminal = sample.expectedStatus === "found"
      ? Boolean(proof && status?.classList.contains("found") && document.querySelector(".hc-hash-label span")?.textContent === "서버 재검증 통과")
      : status?.classList.contains("exhausted");
    if (!sample.sawNewSearch || !terminal || !status.getClientRects().length) return;
    if (sample.terminalAt === null) sample.terminalAt = performance.now();
    const saved = sample.expectedStatus !== "found" || document.querySelector(".hc-save-state")?.textContent.includes("기록으로 저장 완료했습니다.");
    if (button && !button.disabled && saved && sample.readyAt === null) {
      sample.readyAt = performance.now();
      sample.observer.disconnect();
    }
  };
  sample.listener = (event) => {
    if (event.target.closest("button.hc-primary") && sample.clickAt === null) sample.clickAt = performance.now();
  };
  sample.observer = new MutationObserver(inspect);
  sample.observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["class", "disabled"] });
  document.addEventListener("click", sample.listener, true);
  globalThis.__hashcashWebBenchmark = sample;
}

function readDomTiming() {
  const sample = globalThis.__hashcashWebBenchmark;
  return sample && {
    clickAt: sample.clickAt, terminalAt: sample.terminalAt, readyAt: sample.readyAt,
    sawNewSearch: sample.sawNewSearch, expectedStatus: sample.expectedStatus,
    terminalSeconds: sample.terminalAt === null ? null : (sample.terminalAt - sample.clickAt) / 1000,
    readySeconds: sample.readyAt === null ? null : (sample.readyAt - sample.clickAt) / 1000,
  };
}

async function jsonGet(baseUrl, path) {
  const response = await fetch(`${baseUrl}/api/hashcash/${path}`, { signal: AbortSignal.timeout(5_000), cache: "no-store" });
  assert(response.ok, `GET ${path} returned ${response.status}`);
  return response.json();
}

function checkInput(job, item, mode, participantName) {
  assert(job.message === item.message && job.difficulty === item.difficulty && job.startNonce === item.startNonce && job.mode === mode && job.participantName === participantName,
    "Observed job inputs changed or another search replaced this sample");
}

function checkFinal(job, item, mode, participantName) {
  checkInput(job, item, mode, participantName);
  assert(job.finished && job.status === item.expectedStatus, `Expected ${item.expectedStatus}, got ${job.status}: ${job.error ?? ""}`);
  assert(!job.error && !job.saveError && !job.cleanupWarning, `Job failure, save failure, or cleanup warning: ${JSON.stringify(job)}`);
  assert(mode === "gpu" ? job.backend.startsWith("GPU · ") : job.backend.startsWith("C · CPU + GPU · "), `Unexpected fallback device: ${job.backend}`);
  const checked = BigInt(job.checked), cpu = BigInt(job.cpuChecked), gpu = BigInt(job.gpuChecked);
  assert(checked === cpu + gpu && checked > 0n, "Completed device counts do not add up or no work was completed");
  if (mode === "gpu") assert(cpu === 0n && gpu > 0n, "GPU-only search did not complete exclusively GPU work");
  if (mode === "native" && item.id !== "easy") {
    assert(cpu > 0n && gpu > 0n && job.hybridExecuted, "Native search did not execute both devices");
    assert(job.hybridKernelOverlapLowerBound > 0, "No demonstrated actual CPU/CUDA overlap");
  }
  assert(Number.isFinite(job.startupElapsed) && job.startupElapsed >= 0 && Number.isFinite(job.elapsed) && job.elapsed >= job.startupElapsed, "Missing or inconsistent startup/lifecycle timing");
  assert(Number.isFinite(job.searchPreparationElapsed) && job.searchPreparationElapsed >= 0 &&
    Number.isFinite(job.preparationElapsed) && job.preparationElapsed >= job.startupElapsed && job.preparationElapsed <= job.elapsed,
  "Missing or inconsistent preparationElapsed/searchPreparationElapsed; restart the instrumented benchmark server");
  if (item.expectedStatus === "exhausted") {
    assert(checked === BigInt(item.count), "Fixed-work case skipped or duplicated a nonce");
    assert(job.result === null && job.recordId === null, "Fixed-work exhaustion unexpectedly returned or saved a proof");
  } else {
    assert(job.result?.verified === true && typeof job.result.nonce === "string" && /^\d+$/.test(job.result.nonce), "Missing verified string nonce");
    const nonce = BigInt(job.result.nonce);
    assert(nonce >= BigInt(item.startNonce) && nonce <= (1n << 64n) - 1n, "Proof nonce outside uint64 search range");
    const hash = createHash("sha256").update(Buffer.from(item.message, "utf8")).update(Buffer.from(job.result.nonce, "ascii")).digest("hex");
    assert(hash === job.result.hash && hash.startsWith("0".repeat(item.difficulty)), "Independent Node SHA-256 proof verification failed");
    assert(typeof job.recordId === "string" && job.recordId.length > 0, "Proof was not saved");
    assert(Number.isFinite(job.proofElapsed) && job.proofElapsed >= job.startupElapsed && job.proofElapsed <= job.elapsed,
      "Missing or inconsistent proofElapsed; restart the benchmark server with proof timing instrumentation");
    assert(Number.isFinite(job.proofSearchElapsed) && job.proofSearchElapsed >= 0 &&
      Math.abs(job.proofSearchElapsed - (job.proofElapsed - job.preparationElapsed)) < 1e-9,
    "Missing or inconsistent preparation-excluded proofSearchElapsed");
  }
}

async function runSample({ page, config, item, mode, participantName, phase, repetition, ordinal, outDir }) {
  const sample = {
    ordinal, phase, repetition, caseId: item.id, mode, participantName,
    processFirstSample: ordinal === 0, inputs: { ...item, mode, workers: 4, participantName },
    startedAt: new Date().toISOString(), ok: false, statusObservations: [],
  };
  const current = await jsonGet(config.baseUrl, "status");
  assert(!current.job || current.job.finished, "Existing search is still running; refusing to stop or replace it");
  await page.getByRole("button", { name: "탐색 시작" }).waitFor({ state: "visible" });
  await page.waitForFunction(() => !globalThis.document.querySelector("button.hc-primary")?.disabled);
  await page.getByLabel("기록 이름", { exact: true }).fill(participantName);
  await page.getByLabel("메시지", { exact: false }).fill(item.message);
  await page.getByLabel("목표 난이도", { exact: false }).fill(String(item.difficulty));
  await page.getByLabel("연산 장치").selectOption(mode);
  await page.getByLabel("CPU 작업 수").fill("4");
  await page.getByLabel("시작 nonce").fill(item.startNonce);
  sample.gpuBefore = await gpuState();
  await page.evaluate(installDomObserver, { expectedStatus: item.expectedStatus });
  let resolveFinished;
  let rejectFinished;
  let expectedJobId;
  let clickedAt;
  let proofObservedAt = null;
  let finishedObservedAt = null;
  let observerClosed = false;
  const buffered = [];
  const finishedResponse = new Promise((accept, reject) => { resolveFinished = accept; rejectFinished = reject; });
  // Attach a rejection handler immediately: the 90 s deadline may fire while
  // Playwright is still waiting for an unusually slow POST response.
  void finishedResponse.catch(() => {});
  function observeJob(job, observedAt, source) {
    if (observerClosed || clickedAt === undefined) return;
    if (!expectedJobId) { buffered.push({ job, observedAt, source }); return; }
    if (job?.id !== expectedJobId) {
      // A status fetch submitted before the click can return the previous job.
      if (!job || job.id === current.job?.id) return;
      rejectFinished(new Error("Another job replaced this sample; refusing to stop it"));
      return;
    }
    try {
      checkInput(job, item, mode, participantName);
      sample.statusObservations.push({
        source, observedSeconds: (observedAt - clickedAt) / 1000,
        status: job.status, finished: job.finished,
        startupElapsed: job.startupElapsed, searchPreparationElapsed: job.searchPreparationElapsed,
        preparationElapsed: job.preparationElapsed, proofElapsed: job.proofElapsed,
        proofSearchElapsed: job.proofSearchElapsed, elapsed: job.elapsed,
        checked: job.checked, cpuChecked: job.cpuChecked, gpuChecked: job.gpuChecked,
        nonce: job.result?.nonce ?? null, recordId: job.recordId,
      });
      if (job.result && (proofObservedAt === null || observedAt < proofObservedAt)) proofObservedAt = observedAt;
      if (job.finished) {
        if (finishedObservedAt === null || observedAt < finishedObservedAt) {
          finishedObservedAt = observedAt;
          sample.applicationStatusResponseFinishedObservedSeconds = (observedAt - clickedAt) / 1000;
          sample.finishedObservationSource = source;
        }
        resolveFinished(job);
      }
    } catch (error) { rejectFinished(error); }
  }
  const observeStatusResponse = async (response) => {
    if (response.url() !== `${config.baseUrl}/api/hashcash/status` || response.request().method() !== "GET") return;
    const observedAt = performance.now();
    try {
      assert(response.ok(), `Application status request returned ${response.status()}`);
      const { job } = await response.json();
      observeJob(job, observedAt, "application-status-response");
    } catch (error) {
      if (!observerClosed) rejectFinished(error);
    }
  };
  page.on("response", observeStatusResponse);
  const startedResponse = page.waitForResponse((response) => response.url() === `${config.baseUrl}/api/hashcash/start` && response.request().method() === "POST", { timeout: 90_000 });
  clickedAt = performance.now();
  const deadlineTimer = scheduleTimeout(() => rejectFinished(new Error(`Search exceeded 90 seconds: ${sample.jobId ?? "awaiting POST"}; the tool leaves the search intact`)), 90_000);
  try {
    await page.getByRole("button", { name: "탐색 시작" }).click();
    const response = await startedResponse;
    sample.postResponseSeconds = (performance.now() - clickedAt) / 1000;
    sample.startRequest = response.request().postDataJSON();
    sample.startResponseStatus = response.status();
    sample.initialResponse = await response.json();
    assert(response.ok(), `Start failed: ${JSON.stringify(sample.initialResponse)}`);
    const initial = sample.initialResponse.job;
    assert(initial?.id, "Start response did not identify a job");
    checkInput(initial, item, mode, participantName);
    assert(sample.startRequest.workers === 4, "Browser did not submit four CPU workers");
    sample.jobId = initial.id;
    expectedJobId = initial.id;
    observeJob(initial, clickedAt + sample.postResponseSeconds * 1000, "initial-post-response");
    for (const observation of buffered) observeJob(observation.job, observation.observedAt, observation.source);
    buffered.length = 0;
    const observedFinal = await finishedResponse;
    cancelTimeout(deadlineTimer);
    sample.finalApplicationResponse = observedFinal;
    checkFinal(observedFinal, item, mode, participantName);
    await page.waitForFunction(() => globalThis.__hashcashWebBenchmark?.readyAt != null, undefined, { timeout: 10_000 });
    sample.dom = await page.evaluate(readDomTiming);
    assert(sample.dom?.clickAt !== null && sample.dom?.terminalSeconds >= 0 && sample.dom?.readySeconds >= sample.dom?.terminalSeconds,
      "Browser result/ready timestamps are missing or inconsistent");
    // This single verification GET happens after the real page has rendered
    // finished + saved (or exhausted), so it cannot add search-time HTTP load.
    observerClosed = true;
    page.off("response", observeStatusResponse);
    sample.statusObservations.sort((left, right) => left.observedSeconds - right.observedSeconds);
    const { job: final } = await jsonGet(config.baseUrl, "status");
    assert(final?.id === expectedJobId, "Another search replaced the finished sample before verification");
    sample.final = final;
    checkFinal(final, item, mode, participantName);
    if (config.expectReuse && phase === "measured") {
      assert(final.gpuCacheEnabled && final.gpuResourcesReused && final.nativeKernelsReused,
        "Measured sample did not reuse its prepared CUDA resources");
      if (mode === "native") assert(final.nativeResourcesReused, "Measured native sample rebuilt its C owner");
    }
    sample.metrics = {
      serverStartupSeconds: final.startupElapsed,
      serverSearchPreparationSeconds: final.searchPreparationElapsed,
      serverTotalPreparationSeconds: final.preparationElapsed,
      serverProofSeconds: final.result ? final.proofElapsed : null,
      serverProofAfterReadySeconds: final.result ? final.proofElapsed - final.startupElapsed : null,
      serverActiveProofSeconds: final.result ? final.proofSearchElapsed : null,
      serverLifecycleSeconds: final.elapsed,
      serverFixedActiveSeconds: item.id === "fixed" ? final.elapsed - final.preparationElapsed : null,
      serverCleanupAfterProofSeconds: final.result ? final.elapsed - final.proofElapsed : null,
      domTerminalSeconds: sample.dom.terminalSeconds, domReadySeconds: sample.dom.readySeconds,
      applicationStatusResponseProofObservedSeconds: proofObservedAt === null ? null : (proofObservedAt - clickedAt) / 1000,
      applicationStatusResponseFinishedObservedSeconds: sample.applicationStatusResponseFinishedObservedSeconds,
      postResponseSeconds: sample.postResponseSeconds,
    };
    sample.actualBothDevicesExecuted = BigInt(final.cpuChecked) > 0n && BigInt(final.gpuChecked) > 0n;
    sample.gpuAfter = await gpuState();
    const query = new URLSearchParams({ participantName, message: item.message, difficulty: String(item.difficulty), limit: "100" });
    sample.history = await jsonGet(config.baseUrl, `history?${query}`);
    const stored = sample.history.records.find((record) => record.jobId === final.id);
    if (final.result) {
      assert(stored?.id === final.recordId && stored.nonce === final.result.nonce && stored.hash === final.result.hash && stored.message === item.message,
        "Saved history does not match the independently verified proof");
      await page.getByRole("button", { name: "해시 다시 검증", exact: true }).click();
      await page.getByText("브라우저 SHA-256 재계산도 일치합니다.", { exact: true }).waitFor({ state: "visible" });
      sample.browserSha256Verified = true;
    } else assert(!stored, "Exhausted fixed-work search unexpectedly wrote history");
    if (phase === "measured" && repetition === 0) {
      sample.screenshot = join(outDir, `${basename(config.out, ".json")}-${item.id}-${mode}.png`);
      await page.screenshot({ path: sample.screenshot, fullPage: true });
    }
    sample.ok = true;
    sample.endedAt = new Date().toISOString();
    return sample;
  } catch (error) {
    sample.error = error.stack ?? String(error);
    sample.endedAt = new Date().toISOString();
    // Do not add a diagnostic HTTP request to a search that may still be
    // running after failure. Existing application responses remain the record.
    sample.failureLastApplicationObservation = sample.statusObservations.at(-1) ?? null;
    error.sample = sample;
    throw error;
  } finally {
    observerClosed = true;
    cancelTimeout(deadlineTimer);
    page.off("response", observeStatusResponse);
  }
}

async function main() {
  const config = options(process.argv.slice(2));
  if (config.help) { console.log(usage); return; }
  const cases = config.cases.map((id) => allCases.find((item) => item.id === id));
  const firstOrder = config.firstMode === "gpu" ? ["gpu", "native"] : ["native", "gpu"];
  const plan = [];
  for (const [caseIndex, item] of cases.entries()) {
    if (config.warmups) for (const mode of firstOrder) plan.push({ caseId: item.id, mode, phase: "warmup", repetition: 0 });
    for (let repetition = 0; repetition < config.repeats; repetition += 1) {
      const order = (repetition + caseIndex) % 2 ? [...firstOrder].reverse() : firstOrder;
      for (const mode of order) plan.push({ caseId: item.id, mode, phase: "measured", repetition });
    }
  }
  if (config.plan) { console.log(JSON.stringify({ config, cases, plan }, null, 2)); return; }
  const runId = randomUUID();
  config.out = resolve(config.out ?? `work/hashcash-web-benchmark/${runId}.json`);
  const outDir = dirname(config.out);
  await mkdir(outDir, { recursive: true });
  const [health, current] = await Promise.all([jsonGet(config.baseUrl, "health"), jsonGet(config.baseUrl, "status")]);
  assert(health.ok && health.cpuCount >= 4, "This comparison requires the local server and at least four CPU workers");
  assert(!current.job || current.job.finished, "Existing search is running; refusing to replace or stop it");
  const report = {
    schema: "hashcash-web-benchmark-v2", runId, startedAt: new Date().toISOString(), config, cases, plan,
    method: {
      browser: "Installed Microsoft Edge, Playwright chromium channel msedge, headless",
      initiation: "Actual web form selection and button click; no injected API start",
      addedTimedHttpStatusRequests: 0, applicationStatusPollMs: 350, cpuWorkers: 4,
      statusObservation: "Observe only the page's existing GET /status responses and initial POST response during the timed search; a verification GET runs only after DOM finished/saved readiness",
      preparation: "startupElapsed measures backend creation/self-tests/current native kernel preparation; searchPreparationElapsed separately measures GPU-only first preparation (kernel lookup/construction/source generation and compile) of new decimal digit kernels during search; preparationElapsed is their sum",
      activeProof: "proofSearchElapsed = proofElapsed - preparationElapsed: winning-batch completion, excluding dynamic preparation and observer/UI delay; native uses the C first-proof clock; not a per-thread exact instruction timestamp",
      fixedActive: "elapsed - preparationElapsed: exact 2^32 nonces through UINT64_MAX, including post-computation cleanup",
      processFirstSample: "Only ordinal 0 is the first sample in this backend process if the operator started a fresh server; OS CUDA/disk caches are uncontrolled",
      actualProofWork: "Identical input/start/difficulty, but parallel scheduling can find different valid nonces and process different counts; fixed case controls total work separately",
      rawValues: "Warmups and failed samples are retained; measured summaries exclude warmups and failed samples",
      gpuTelemetry: "nvidia-smi runs only before clicking and after finished/DOM-ready, outside the timed search",
    },
    health,
    machine: { hostname: os.hostname(), platform: os.platform(), release: os.release(), arch: os.arch(), cpuModel: os.cpus()[0]?.model, logicalCpuCount: os.cpus().length, totalMemory: os.totalmem(), node: process.version },
    git: { head: await command("git", ["rev-parse", "HEAD"]), status: await command("git", ["status", "--short"]) },
    sourceFingerprints: await fingerprints(), samples: [], completed: false,
  };
  async function flush() {
    report.summary = aggregate(report.samples, cases);
    await writeFile(config.out, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }
  await flush();
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto(config.baseUrl);
    await page.getByText("● 로컬 연산 서버 연결됨", { exact: true }).waitFor({ state: "visible" });
    const participantName = `웹 비교 ${runId}`;
    for (const [ordinal, entry] of plan.entries()) {
      try {
        const sample = await runSample({ page, config, item: cases.find((item) => item.id === entry.caseId), ...entry, participantName, ordinal, outDir });
        report.samples.push(sample);
        await flush();
        console.log(JSON.stringify({ completed: report.samples.length, total: plan.length, ...entry, metrics: sample.metrics, nonce: sample.final.result?.nonce ?? null, checked: sample.final.checked }));
      } catch (error) {
        if (error.sample) report.samples.push(error.sample);
        throw error;
      }
    }
    report.completed = true;
    report.endedAt = new Date().toISOString();
    report.finalSourceFingerprints = await fingerprints();
    await flush();
    console.log(`Saved ${config.out}`);
  } catch (error) {
    report.error = error.stack ?? String(error);
    report.endedAt = new Date().toISOString();
    await flush();
    throw error;
  } finally {
    await browser.close();
  }
}

try { await main(); }
catch (error) { console.error(error.stack ?? String(error)); process.exitCode = 1; }
