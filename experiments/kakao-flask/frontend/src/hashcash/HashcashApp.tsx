import { useEffect, useRef, useState } from "react";
import { TeamNavigation } from "../ui/TeamNavigation";
import { useTheme } from "../ui/useTheme";
import type { ThemePreference } from "../theme";
import { proofHash, validateSettings, type HashcashMode } from "./core";
import { addHistory, HISTORY_KEY, NAME_KEY, parseHistory, type HistoryRecord } from "./history";
import "./hashcash.css";

type Settings = { message: string; difficulty: number; startNonce: string };
type WorkerUpdate = {
  type: "progress" | "found" | "exhausted" | "error";
  runId: string;
  checked: string;
  nonce: string | null;
  hash: string | null;
  elapsedMs: number;
  error?: string;
  backend?: "cpu" | "gpu" | "hybrid";
  deviceLabel?: string;
  reason?: string;
  cpuChecked?: string;
  gpuChecked?: string;
  initializing?: boolean;
  winnerBackend?: "cpu" | "gpu";
  nativeJobId?: string;
};
type Search = {
  id: string;
  settings: Settings;
  participantName: string;
  status: "running" | "found" | "stopped" | "exhausted" | "error";
  checked: string;
  nonce: string | null;
  hash: string | null;
  elapsedMs: number;
  requestedMode: HashcashMode;
  backend?: "cpu" | "gpu" | "hybrid";
  deviceLabel?: string;
  reason?: string;
  cpuChecked: string;
  gpuChecked: string;
  initializing: boolean;
  winnerBackend?: "cpu" | "gpu";
  nativeJobId?: string;
};
function stopNative(search: Search | null) {
  if (!search?.nativeJobId || search.status !== "running") return;
  void fetch("/api/hashcash-native/stop", { method: "POST", credentials: "same-origin", keepalive: true,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ jobId: search.nativeJobId }) }).catch(() => undefined);
}
const backendLabels = { cpu: "CPU", gpu: "GPU", hybrid: "CPU+GPU" };
const modeHints = {
  auto: "GPU를 확인해 사용할 수 있으면 GPU로 계산합니다 사용할 수 없으면 CPU로 전환합니다",
  cpu: "이 기기의 CPU로 계산합니다 GPU를 준비하지 않고 바로 시작합니다",
  gpu: "이 기기의 GPU로 여러 넌스를 함께 계산합니다 GPU를 사용할 수 없으면 CPU로 전환합니다",
  hybrid: "CPU와 GPU가 서로 다른 넌스 구간을 동시에 계산합니다 GPU를 사용할 수 없으면 CPU로 전환합니다",
  "python-native": "PC에서 실행한 팀 Flask의 Python·CUDA 엔진을 사용합니다 지원하지 않는 환경에서는 브라우저 CPU로 전환합니다",
  "c-native": "PC에서 실행한 팀 Flask의 C·CUDA 엔진을 사용합니다 지원하지 않는 환경에서는 브라우저 CPU로 전환합니다",
};
const statuses = {
  running: "넌스를 찾는 중",
  found: "조건에 맞는 넌스를 찾았습니다",
  stopped: "탐색을 중지했습니다",
  exhausted: "탐색 범위가 끝났습니다",
  error: "탐색을 마치지 못했습니다",
};
const numberText = (value: string) => BigInt(value).toLocaleString("ko-KR");
const decimalText = (value: number) => new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(value);

function readHistory() {
  try { return parseHistory(localStorage.getItem(HISTORY_KEY)); }
  catch { return []; }
}
function readName() {
  try { return localStorage.getItem(NAME_KEY)?.trim().slice(0, 60) || "나"; }
  catch { return "나"; }
}

export function HashcashApp() {
  const { preference, selectTheme } = useTheme();
  const [message, setMessage] = useState("You are the best of best!!!!");
  const [difficulty, setDifficulty] = useState(3);
  const [mode, setMode] = useState<HashcashMode>("auto");
  const [startNonce, setStartNonce] = useState("0");
  const [participantName, setParticipantName] = useState(readName);
  const [search, setSearch] = useState<Search | null>(null);
  const [error, setError] = useState("");
  const [verification, setVerification] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [history, setHistory] = useState<HistoryRecord[]>(readHistory);
  const [storageMessage, setStorageMessage] = useState("");
  const worker = useRef<Worker | null>(null);
  const currentRun = useRef("");
  const historyRef = useRef(history);
  const searchRef = useRef(search);
  const active = search?.status === "running";

  useEffect(() => { searchRef.current = search; }, [search]);

  useEffect(() => () => {
    currentRun.current = "";
    stopNative(searchRef.current);
    worker.current?.terminate();
    worker.current = null;
  }, []);

  useEffect(() => {
    const hide = () => {
      currentRun.current = "";
      stopNative(searchRef.current);
      worker.current?.terminate();
      worker.current = null;
      setVerifying(false);
      setSearch((previous) => previous?.status === "running" ? { ...previous, status: "stopped" } : previous);
    };
    const show = () => { currentRun.current = searchRef.current?.status === "found" ? searchRef.current.id : ""; };
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    return () => {
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("pageshow", show);
    };
  }, []);

  useEffect(() => {
    const update = (event: StorageEvent) => {
      if (event.key !== HISTORY_KEY) return;
      const next = parseHistory(event.newValue);
      historyRef.current = next;
      setHistory(next);
    };
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);

  function saveResult(result: Search) {
    if (!result.nonce || !result.hash) return;
    const record: HistoryRecord = {
      id: result.id,
      participantName: result.participantName,
      ...result.settings,
      nonce: result.nonce,
      hash: result.hash,
      checked: result.checked,
      elapsedMs: result.elapsedMs,
      requestedMode: result.requestedMode,
      backend: result.backend,
      deviceLabel: result.deviceLabel,
      reason: result.reason,
      cpuChecked: result.cpuChecked,
      gpuChecked: result.gpuChecked,
      winnerBackend: result.winnerBackend,
      createdAt: new Date().toISOString(),
    };
    let previous = historyRef.current;
    try {
      const stored = parseHistory(localStorage.getItem(HISTORY_KEY));
      const unique = new Map([...stored, ...previous].map((item) => [item.id, item]));
      previous = [...unique.values()].sort((first, second) => Date.parse(second.createdAt) - Date.parse(first.createdAt));
    } catch { /* Keep this tab's history */ }
    const next = addHistory(previous, record);
    historyRef.current = next;
    setHistory(next);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
      localStorage.setItem(NAME_KEY, result.participantName);
      setStorageMessage(`${result.participantName}의 결과를 이 브라우저에 저장했습니다`);
    } catch {
      setStorageMessage("브라우저 저장 공간을 사용할 수 없어 이번 결과는 현재 화면에서만 볼 수 있습니다");
    }
  }

  function start() {
    if (active) return;
    if (!globalThis.crypto?.subtle || typeof Worker === "undefined") {
      setError("이 브라우저에서는 해시 계산을 사용할 수 없습니다 최신 브라우저로 HTTPS 주소에 접속해 주세요");
      return;
    }
    let settings: Settings;
    try { settings = validateSettings({ message, difficulty, startNonce }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "입력값을 확인하세요"); return; }
    worker.current?.terminate();
    const id = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    currentRun.current = id;
    const initial: Search = {
      id, settings, participantName: participantName.trim() || "나", status: "running",
      checked: "0", nonce: null, hash: null, elapsedMs: 0,
      requestedMode: mode, cpuChecked: "0", gpuChecked: "0", initializing: true,
    };
    setSearch(initial);
    setStartNonce(settings.startNonce);
    setError("");
    setVerification("");
    setVerifying(false);
    setStorageMessage("");
    try {
      const nextWorker = new Worker(new URL("./hashcash.worker.ts", import.meta.url), { type: "module" });
      worker.current = nextWorker;
      let currentSearch = initial;
      nextWorker.onmessage = (event: MessageEvent<WorkerUpdate>) => {
        const update = event.data;
        if (currentRun.current !== id || update.runId !== id || worker.current !== nextWorker) return;
        const next: Search = {
          ...currentSearch,
          status: update.type === "progress" ? "running" : update.type,
          checked: update.checked, nonce: update.nonce, hash: update.hash, elapsedMs: update.elapsedMs,
          backend: update.backend ?? currentSearch.backend,
          deviceLabel: update.deviceLabel ?? currentSearch.deviceLabel,
          reason: update.reason ?? currentSearch.reason,
          cpuChecked: update.cpuChecked ?? currentSearch.cpuChecked,
          gpuChecked: update.gpuChecked ?? currentSearch.gpuChecked,
          initializing: update.initializing ?? false,
          winnerBackend: update.winnerBackend ?? currentSearch.winnerBackend,
          nativeJobId: update.nativeJobId ?? currentSearch.nativeJobId,
        };
        currentSearch = next;
        searchRef.current = next;
        setSearch(next);
        if (update.type !== "progress") {
          nextWorker.terminate();
          worker.current = null;
        }
        if (update.type === "error") setError(update.error || "계산을 마치지 못했습니다 다시 시작해 주세요");
        if (update.type === "found") saveResult(next);
      };
      nextWorker.onerror = () => {
        if (currentRun.current !== id || worker.current !== nextWorker) return;
        stopNative(searchRef.current);
        nextWorker.terminate();
        worker.current = null;
        setSearch((previous) => previous?.id === id ? { ...previous, status: "error" } : previous);
        setError("브라우저에서 계산을 시작하지 못했습니다 최신 브라우저에서 다시 시도해 주세요");
      };
      nextWorker.postMessage({ type: "start", runId: id, settings, mode });
    } catch {
      worker.current?.terminate();
      worker.current = null;
      setSearch({ ...initial, status: "error" });
      setError("브라우저에서 계산을 시작하지 못했습니다 HTTPS 주소에서 다시 접속해 주세요");
    }
  }

  function stop() {
    currentRun.current = "";
    stopNative(searchRef.current);
    worker.current?.terminate();
    worker.current = null;
    setSearch((previous) => previous?.status === "running" ? { ...previous, status: "stopped" } : previous);
  }

  async function verify() {
    if (!search?.nonce || !search.hash || search.status !== "found") return;
    const snapshot = search;
    setVerifying(true);
    setVerification("");
    try {
      const hash = await proofHash(snapshot.settings.message, snapshot.nonce!);
      if (currentRun.current !== snapshot.id) return;
      setVerification(hash === snapshot.hash && hash.startsWith("0".repeat(snapshot.settings.difficulty))
        ? "SHA-256을 다시 계산해 확인했습니다 해시와 목표 조건이 일치합니다"
        : "다시 계산한 해시가 일치하지 않습니다 조건을 확인해 다시 탐색해 주세요");
    } catch {
      if (currentRun.current === snapshot.id) setVerification("이 브라우저에서 SHA-256을 다시 계산하지 못했습니다");
    } finally {
      if (currentRun.current === snapshot.id) setVerifying(false);
    }
  }

  function loadRecord(record: HistoryRecord) {
    if (active) return;
    setMessage(record.message);
    setDifficulty(record.difficulty);
    setStartNonce(record.startNonce);
    setParticipantName(record.participantName);
    setMode(record.requestedMode ?? "cpu");
    setError("");
    document.getElementById("nonce-message")?.focus();
  }

  const shownHash = search?.hash;
  const target = search?.settings.difficulty ?? difficulty;
  const leadingZeros = Math.min(target, shownHash?.match(/^0*/)?.[0].length ?? 0);
  const attempts = 16 ** difficulty;
  return (
    <div className="classroom-shell nonce-shell">
      <a className="skip-link" href="#nonce-main">넌스 찾기 본문으로 바로가기</a>
      <header className="classroom-header">
        <div><strong>팀 블록체인 · 넌스 찾기</strong><p>숫자를 바꾸며 작업 증명 이해하기</p></div>
        <TeamNavigation hashcash />
        <label>화면 테마<select aria-label="화면 테마" value={preference} onChange={(event) => selectTheme(event.target.value as ThemePreference)}>
          <option value="system">시스템</option><option value="light">라이트</option><option value="dark">다크</option>
        </select></label>
      </header>
      <main id="nonce-main" tabIndex={-1}>
        <section className="nonce-intro" aria-labelledby="nonce-title">
          <h1 id="nonce-title">넌스 찾기</h1>
          <p>메시지 뒤에 붙일 숫자, 넌스(Nonce)를 찾습니다 해시가 정한 개수의 0으로 시작하면 성공입니다</p>
          <div className="nonce-formula"><span>메시지 + 넌스</span><span aria-hidden="true">→</span><strong>SHA-256</strong><span aria-hidden="true">→</span><code>000…</code></div>
        </section>
        <div className="nonce-workspace">
          <section className="panel nonce-settings" aria-labelledby="nonce-settings-title">
            <h2 id="nonce-settings-title">1 조건 입력</h2>
            <form onSubmit={(event) => { event.preventDefault(); start(); }}>
              <label htmlFor="nonce-message">메시지<textarea id="nonce-message" aria-describedby="nonce-message-hint" value={message} onChange={(event) => setMessage(event.target.value)} rows={3} disabled={active} /></label>
              <small id="nonce-message-hint">공백과 줄바꿈도 그대로 계산합니다</small>
              <label htmlFor="nonce-difficulty">목표 난이도<input id="nonce-difficulty" type="number" min={1} max={64} step={1} value={Number.isNaN(difficulty) ? "" : difficulty} onChange={(event) => setDifficulty(event.target.value === "" ? NaN : Number(event.target.value))} disabled={active} aria-describedby="nonce-difficulty-hint" /></label>
              <small id="nonce-difficulty-hint">해시 앞의 16진수 0 개수 · 처음에는 2~3을 추천합니다</small>
              <div className="nonce-target"><span>목표</span><code>{"0".repeat(Math.min(16, Math.max(0, Number.isFinite(difficulty) ? Math.trunc(difficulty) : 0)))}…</code><small>평균 {Number.isFinite(attempts) ? attempts < 1e9 ? decimalText(attempts) : attempts.toExponential(1) : "—"}회 계산</small></div>
              <label htmlFor="nonce-mode">연산 방식<select id="nonce-mode" value={mode} onChange={(event) => setMode(event.target.value as HashcashMode)} disabled={active} aria-describedby="nonce-mode-hint">
                <option value="auto">자동</option><option value="cpu">CPU</option><option value="gpu">GPU</option><option value="hybrid">CPU+GPU</option>
                <optgroup label="PC 고급 모드"><option value="python-native">Python CPU+GPU · PC 전용</option><option value="c-native">C CPU+GPU · PC 전용</option></optgroup>
              </select></label>
              <small id="nonce-mode-hint">{modeHints[mode]}</small>
              <details className="nonce-options"><summary>추가 설정</summary>
                <label htmlFor="nonce-start">시작 넌스<input id="nonce-start" inputMode="numeric" value={startNonce} onChange={(event) => setStartNonce(event.target.value)} disabled={active} autoComplete="off" /></label>
                <label htmlFor="nonce-name">기록 이름<input id="nonce-name" value={participantName} maxLength={60} onChange={(event) => setParticipantName(event.target.value)} disabled={active} autoComplete="nickname" /></label>
              </details>
              <div className="button-row nonce-actions"><button type="submit" disabled={active}>탐색 시작</button><button type="button" className="secondary" onClick={stop} disabled={!active}>중지</button></div>
              {error && <p className="nonce-error" role="alert">{error}</p>}
            </form>
            <p className="nonce-hint">난이도가 1 높아지면 평균 계산량은 16배가 됩니다 발견 시간은 매번 다릅니다</p>
          </section>
          <section className="panel nonce-observation" aria-labelledby="nonce-observation-title">
            <h2 id="nonce-observation-title">2 결과 확인</h2>
            <p className={`nonce-status ${search?.status === "found" ? "nonce-success" : ""}`} role="status" data-testid="nonce-status">{search ? search.initializing && active ? "연산 장치 확인 중" : statuses[search.status] : "입력 후 시작하세요"}</p>
            <div className="nonce-engine" aria-label="실제 연산 장치">
              <div className="nonce-engine-heading"><strong data-testid="nonce-backend">{search?.backend ? backendLabels[search.backend] : active ? "연산 장치 확인 중" : "시작하면 연산 장치를 표시합니다"}</strong>{search?.deviceLabel && <span data-testid="nonce-device">{search.deviceLabel}</span>}</div>
              {search?.reason && <p role="status" data-testid="nonce-engine-reason">{search.reason}</p>}
              <div className="nonce-device-counts" aria-label="장치별 완료 계산"><span>CPU <strong data-testid="nonce-cpu-checked">{numberText(search?.cpuChecked ?? "0")}</strong>회</span><span>GPU <strong data-testid="nonce-gpu-checked">{numberText(search?.gpuChecked ?? "0")}</strong>회</span></div>
            </div>
            <dl className="nonce-metrics">
              <div><dt>계산한 횟수</dt><dd data-testid="nonce-checked">{numberText(search?.checked ?? "0")}<small>회</small></dd></div>
              <div><dt>경과 시간</dt><dd>{decimalText((search?.elapsedMs ?? 0) / 1000)}<small>초</small></dd></div>
              <div><dt>평균 속도</dt><dd>{search && search.elapsedMs > 0 ? decimalText(Number(search.checked) / search.elapsedMs * 1000) : "0"}<small>회/초</small></dd></div>
            </dl>
            <div className={`nonce-proof ${search?.status === "found" ? "nonce-proof-found" : ""}`}>
              <span>{search?.status === "found" ? "발견한 넌스" : "최근 계산한 넌스"}</span>
              <strong data-testid="nonce-value">{search?.nonce ?? "—"}</strong>
              <span>SHA-256 해시</span>
              <code data-testid="nonce-hash">{shownHash ? <><mark>{shownHash.slice(0, leadingZeros)}</mark>{shownHash.slice(leadingZeros)}</> : "계산하면 64자리 해시가 여기에 표시됩니다"}</code>
            </div>
            {search?.status === "found" && <div className="nonce-result">
              <p>앞의 0이 {search.settings.difficulty}개 이상인 해시를 찾았습니다</p>
              {search.winnerBackend && <p>{backendLabels[search.winnerBackend]}에서 유효한 넌스를 발견했습니다</p>}
              <p className="nonce-input-proof">계산한 입력 <code>{JSON.stringify(search.settings.message)} + "{search.nonce}"</code></p>
              <button className="secondary" onClick={() => void verify()} disabled={verifying}>{verifying ? "다시 계산 중" : "해시 다시 검증"}</button>
              {verification && <p role="status" data-testid="nonce-verification">{verification}</p>}
            </div>}
            {storageMessage && <p role="status" className="nonce-storage-message">{storageMessage}</p>}
            <p className="nonce-hint">이 기기의 CPU·GPU로 실제 계산합니다 GPU는 여러 넌스를 한 번에 처리하므로 계산 수가 발견한 넌스보다 클 수 있습니다 CPU+GPU가 항상 더 빠르거나 가장 작은 넌스를 먼저 찾는 것은 아닙니다</p>
          </section>
        </div>
        <section className="panel nonce-history" aria-labelledby="nonce-history-title" data-testid="nonce-history">
          <h2 id="nonce-history-title">찾은 기록</h2>
          <p className="nonce-hint">최근 결과 50개를 이 브라우저에 저장합니다 다른 기기나 공동 실습 장부에는 저장되지 않습니다</p>
          {history.length === 0 ? <p className="nonce-empty">조건에 맞는 넌스를 찾으면 여기에 기록됩니다</p> : <ol>
            {history.map((record) => <li key={record.id} data-testid="nonce-history-record">
              <div className="nonce-record-heading"><strong>{record.participantName} · 난이도 {record.difficulty}</strong><time dateTime={record.createdAt}>{new Date(record.createdAt).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time></div>
              <p className="nonce-record-message">{record.message || "(빈 메시지)"}</p>
              <p>넌스 <strong>{record.nonce}</strong> · {numberText(record.checked)}회 계산</p>
              <p className="nonce-record-engine">{backendLabels[record.backend ?? "cpu"]}{record.deviceLabel ? ` · ${record.deviceLabel}` : ""}</p>
              <code>{record.hash}</code>
              <button className="secondary" onClick={() => loadRecord(record)} disabled={active}>결과 조건 불러오기</button>
            </li>)}
          </ol>}
        </section>
        <details className="panel nonce-native-info">
          <summary>Python·C 모드</summary>
          <p>연산 방식의 PC 고급 모드에서 선택합니다 팀 Flask를 PC에서 실행하고 CUDA·네이티브 엔진이 준비된 경우에만 Python·C 연산을 사용합니다</p>
          <p>무료 온라인 주소나 지원하지 않는 기기에서는 브라우저 CPU로 자동 전환합니다 별도 설치 없이 GPU를 쓰려면 일반 GPU·CPU+GPU 모드를 선택하세요 실제 연산 장치와 전환 이유는 결과에 표시됩니다</p>
        </details>
      </main>
    </div>
  );
}
