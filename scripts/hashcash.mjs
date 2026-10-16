import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import process from "node:process";
import console from "node:console";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const { fetch, AbortSignal } = globalThis;

const projectPython = resolve(process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const python = process.env.HASHCASH_PYTHON || (existsSync(projectPython) ? projectPython : "python");
function port(value, fallback) {
  const raw = value ?? String(fallback);
  if (!/^\d+$/.test(raw) || Number(raw) < 1 || Number(raw) > 65535) throw new Error("포트는 1~65535 정수여야 합니다.");
  return Number(raw);
}
const frontendPort = port(process.env.HASHCASH_PORT, 5186);
const apiPort = port(process.env.HASHCASH_API_PORT, 9186);
if (frontendPort === apiPort) throw new Error("화면과 API 포트는 서로 달라야 합니다.");
const backendUrl = `http://127.0.0.1:${apiPort}`;
const children = [];
let stopping = false;
let ownsBackend = false;
const instance = randomUUID();
async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  // Let the backend terminate its CPU children before terminating its process.
  if (ownsBackend) {
    const health = await fetch(`${backendUrl}/api/hashcash/health`, { signal: AbortSignal.timeout(500) }).catch(() => null);
    ownsBackend = health?.ok && (await health.json().catch(() => ({}))).instance === instance;
  }
  if (ownsBackend) {
    await fetch(`${backendUrl}/api/hashcash/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal: AbortSignal.timeout(500) }).catch(() => null);
    for (let attempt = 0; attempt < 30; attempt++) {
      const response = await fetch(`${backendUrl}/api/hashcash/status`, { signal: AbortSignal.timeout(500) }).catch(() => null);
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
  const child = spawn(command, args, { stdio: "inherit", windowsHide: true,
    env: { ...process.env, HASHCASH_PORT: String(frontendPort), HASHCASH_API_PORT: String(apiPort) } });
  children.push(child);
  child.on("error", (error) => { console.error(error.message); stop(1); });
  child.on("exit", (code) => { if (!stopping) stop(code ?? 1); });
  return child;
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
try {
  // Refuse to reuse another server's state, including on a configured port.
  const check = await fetch(`${backendUrl}/api/hashcash/health`, { signal: AbortSignal.timeout(500) }).catch(() => null);
  if (check) throw new Error(`${apiPort} 포트가 사용 중입니다. HASHCASH_API_PORT로 다른 포트를 선택하세요.`);
  console.log(`Hashcash Python: ${python}`);
  launch(python, ["-m", "hashcash_lab.server", "--port", String(apiPort), "--frontend-port", String(frontendPort), "--instance", instance]);
  let ready = false;
  for (let attempt = 0; attempt < 100 && !stopping; attempt++) {
    await delay(100);
    const response = await fetch(`${backendUrl}/api/hashcash/health`, { signal: AbortSignal.timeout(500) }).catch(() => null);
    if (response?.ok && (await response.json()).instance === instance) { ready = true; ownsBackend = true; break; }
  }
  if (!stopping && !ready) throw new Error("Python 서버가 시작되지 않았습니다. Python 3.12 이상과 HASHCASH_PYTHON 경로를 확인하세요.");
  if (ready && !stopping) launch(process.execPath, ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", String(frontendPort), "--strictPort"]);
} catch (error) { console.error(error.message); stop(1); }
