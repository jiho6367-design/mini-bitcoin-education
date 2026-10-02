import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import process from "node:process";
import console from "node:console";
import { randomUUID } from "node:crypto";

const { fetch, AbortSignal } = globalThis;

const python = process.env.HASHCASH_PYTHON || "python";
const children = [];
let stopping = false;
let ownsBackend = false;
const instance = randomUUID();
async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  // Let the backend terminate its CPU children before terminating its process.
  if (ownsBackend) {
    const health = await fetch("http://127.0.0.1:9186/api/hashcash/health", { signal: AbortSignal.timeout(500) }).catch(() => null);
    ownsBackend = health?.ok && (await health.json().catch(() => ({}))).instance === instance;
  }
  if (ownsBackend) {
    await fetch("http://127.0.0.1:9186/api/hashcash/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal: AbortSignal.timeout(500) }).catch(() => null);
    for (let attempt = 0; attempt < 30; attempt++) {
      const response = await fetch("http://127.0.0.1:9186/api/hashcash/status", { signal: AbortSignal.timeout(500) }).catch(() => null);
      if (!response) break;
      const { job } = await response.json().catch(() => ({ job: null }));
      if (!job || job.finished) break;
      await delay(100);
    }
  }
  for (const child of children) {
    if (child.exitCode !== null || !child.pid) continue;
    if (process.platform === "win32") spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
    else child.kill();
  }
  process.exitCode = code;
}
function launch(command, args) {
  const child = spawn(command, args, { stdio: "inherit", windowsHide: true });
  children.push(child);
  child.on("error", (error) => { console.error(error.message); stop(1); });
  child.on("exit", (code) => { if (!stopping) stop(code ?? 1); });
  return child;
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
try {
  // Refuse to reuse another server's state on the fixed lab port.
  const check = await fetch("http://127.0.0.1:9186/api/hashcash/health", { signal: AbortSignal.timeout(500) }).catch(() => null);
  if (check) throw new Error("9186 포트가 사용 중입니다. 기존 해시캐시 서버를 종료한 후 실행하세요.");
  launch(python, ["-m", "hashcash_lab.server", "--port", "9186", "--instance", instance]);
  let ready = false;
  for (let attempt = 0; attempt < 100 && !stopping; attempt++) {
    await delay(100);
    const response = await fetch("http://127.0.0.1:9186/api/hashcash/health", { signal: AbortSignal.timeout(500) }).catch(() => null);
    if (response?.ok && (await response.json()).instance === instance) { ready = true; ownsBackend = true; break; }
  }
  if (!stopping && !ready) throw new Error("Python 서버가 시작되지 않았습니다. Python 3.12 이상과 HASHCASH_PYTHON 경로를 확인하세요.");
  if (ready && !stopping) launch(process.execPath, ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5186", "--strictPort"]);
} catch (error) { console.error(error.message); stop(1); }
