import { useEffect, useRef, useState } from "react";
import { useTheme } from "../ui/useTheme";
import { HistoryPanel, type HistoryRecord } from "./HistoryPanel";

type Job = {
  id: string; status: "preparing" | "running" | "found" | "stopped" | "exhausted" | "error";
  backend: string; reason: string; message: string; difficulty: number; startNonce: string;
  checked: string; lastNonce: string | null; lastHash: string | null;
  elapsed: number; hashesPerSecond: number; error: string | null; finished: boolean;
  result: { nonce: string; hash: string; verified: boolean } | null;
  participantName: string; recordId: string | null; saveError: string | null;
  mode?: string; cpuChecked?: string; gpuChecked?: string;
  hybridOverlapSeconds?: number; startupElapsed?: number;
  hybridKernelOverlapLowerBound?: number; hybridTimingLimited?: boolean; cleanupWarning?: string | null;
};
type Sample = { nonce: string; hash: string };
const labels = { preparing: "연산 장치 확인 중", running: "조건을 찾는 중", found: "조건을 만족하는 nonce 발견", stopped: "탐색 중지됨", exhausted: "탐색 범위 완료", error: "탐색 오류" };
const format = (n: number) => new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(n);
const rate = (n: number) => n >= 1e9 ? `${format(n / 1e9)} GH/s` : n >= 1e6 ? `${format(n / 1e6)} MH/s` : n >= 1e3 ? `${format(n / 1e3)} kH/s` : `${format(n)} H/s`;
const numberText = (s: string) => BigInt(s).toLocaleString("ko-KR");
const participantStorageKey = "hashcash.participantName";

function rememberedParticipant() {
  try { return window.localStorage.getItem(participantStorageKey)?.trim().slice(0, 60) || "나"; }
  catch { return "나"; }
}

async function api<T>(path: string, data?: object): Promise<T> {
  const response = await fetch(`/api/hashcash/${path}`, data ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) } : undefined);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "서버 요청에 실패했습니다.");
  return body;
}

export function HashcashApp() {
  const { preference, selectTheme } = useTheme();
  const [message, setMessage] = useState("You are the best of best!!!!");
  const [participantName, setParticipantName] = useState(rememberedParticipant);
  const [difficulty, setDifficulty] = useState(3);
  const [startNonce, setStartNonce] = useState("0");
  const [mode, setMode] = useState("auto");
  const [workers, setWorkers] = useState(1);
  const [cpuCount, setCpuCount] = useState(1);
  const [connected, setConnected] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [verification, setVerification] = useState("");
  const [savePending, setSavePending] = useState(false);
  const active = pending || (job !== null && !job.finished);
  const unsavedResult = Boolean(job?.result && job.finished && !job.recordId && job.saveError);
  const sampleJob = useRef("");
  const lastSample = useRef("");
  const requestEpoch = useRef(0);
  const requestPending = useRef(false);
  const currentJobId = useRef("");

  useEffect(() => {
    try { window.localStorage.setItem(participantStorageKey, participantName); }
    catch { /* 탐색과 서버 기록은 브라우저 저장소 없이도 사용할 수 있습니다. */ }
  }, [participantName]);

  useEffect(() => {
    let disposed = false;
    let polling = false;
    api<{ cpuCount: number }>("health").then((health) => {
      if (disposed) return;
      setCpuCount(health.cpuCount); setWorkers(health.cpuCount); setConnected(true);
    }).catch(() => { if (!disposed) setError("백엔드에 연결할 수 없습니다. 터미널에서 npm.cmd run hashcash로 실행하세요."); });
    const poll = async () => {
      if (polling || requestPending.current) return;
      const epoch = requestEpoch.current;
      polling = true;
      try {
        const { job: next } = await api<{ job: Job | null }>("status");
        if (disposed || epoch !== requestEpoch.current || requestPending.current) return;
        currentJobId.current = next?.id ?? "";
        setConnected(true); setJob(next);
        if (next && next.id !== sampleJob.current) {
          sampleJob.current = next.id; lastSample.current = ""; setSamples([]); setVerification("");
        }
        if (next?.lastNonce && next.lastHash && next.lastNonce !== lastSample.current) {
          lastSample.current = next.lastNonce;
          setSamples((previous) => [{ nonce: next.lastNonce!, hash: next.lastHash! }, ...previous].slice(0, 6));
        }
      } catch { if (!disposed && epoch === requestEpoch.current && !requestPending.current) setConnected(false); }
      finally { polling = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 350);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);

  async function start() {
    if (unsavedResult) { setError("새 탐색 전에 발견 결과의 기록 저장을 다시 시도하세요."); return; }
    const name = participantName.trim();
    if (!name) { setError("기록 이름을 입력하세요."); return; }
    setParticipantName(name);
    requestEpoch.current += 1; requestPending.current = true; currentJobId.current = "";
    setJob(null); setSamples([]);
    setPending(true); setError(""); setVerification("");
    try {
      const { job: next } = await api<{ job: Job }>("start", { message, difficulty, startNonce, mode, workers, participantName: name });
      currentJobId.current = next.id; setJob(next); setSamples([]);
    } catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); }
    finally { requestPending.current = false; setPending(false); }
  }
  async function stop() {
    try { await api("stop", {}); }
    catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); }
  }
  async function verify() {
    if (!job?.result) return;
    const epoch = requestEpoch.current;
    const jobId = job.id;
    try {
      const bytes = new TextEncoder().encode(job.message + job.result.nonce);
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
      const hash = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
      if (epoch !== requestEpoch.current || currentJobId.current !== jobId) return;
      setVerification(hash === job.result.hash && hash.startsWith("0".repeat(job.difficulty)) ? "브라우저 SHA-256 재계산도 일치합니다." : "재계산 결과가 일치하지 않습니다.");
    } catch { if (epoch === requestEpoch.current && currentJobId.current === jobId) setVerification("브라우저에서 SHA-256을 재계산할 수 없습니다."); }
  }

  async function retrySave() {
    if (!job?.result || !job.finished || savePending) return;
    requestEpoch.current += 1; requestPending.current = true;
    const epoch = requestEpoch.current;
    const jobId = job.id;
    setSavePending(true);
    try {
      const { job: next } = await api<{ job: Job }>("history/save", { jobId });
      if (epoch === requestEpoch.current && currentJobId.current === jobId) setJob(next);
    } catch (exc) {
      if (epoch === requestEpoch.current && currentJobId.current === jobId) {
        setJob((current) => current?.id === jobId ? { ...current, saveError: exc instanceof Error ? exc.message : String(exc) } : current);
      }
    } finally {
      if (epoch === requestEpoch.current) requestPending.current = false;
      setSavePending(false);
    }
  }

  function loadHistoryRecord(record: HistoryRecord) {
    if (active) return;
    setParticipantName(record.participantName); setMessage(record.message); setDifficulty(record.difficulty); setStartNonce("0"); setError("");
  }

  const shownDifficulty = job?.difficulty ?? difficulty;
  const shownHash = job?.result?.hash ?? job?.lastHash;
  const zeroCount = Math.min(shownDifficulty, shownHash?.match(/^0*/)?.[0].length ?? 0);
  const meanHashes = 16 ** difficulty;
  return <div className="hc-app">
    <header className="hc-header"><a className="hc-brand" href="/"><span className="hc-mark">#</span>HASHCASH<span className="hc-muted"> / LAB</span></a><div className="hc-header-right"><span className="hc-connection">{connected ? "● 로컬 연산 서버 연결됨" : "연산 서버 연결 대기"}</span><label>테마 <select aria-label="화면 테마" value={preference} onChange={(e) => selectTheme(e.target.value as "system" | "light" | "dark")}><option value="system">시스템</option><option value="light">라이트</option><option value="dark">다크</option></select></label></div></header>
    <main><div className="hc-intro"><div><p className="hc-eyebrow">SHA-256 · PROOF OF WORK</p><h1>숫자 하나로,<br />해시의 조건을 맞추다.</h1><p className="hc-lead">메시지 뒤에 숫자를 붙이고 다시 계산합니다.<br />해시가 목표한 개수의 0으로 시작하면, 그 숫자가 유효한 nonce입니다.</p></div><div className="hc-formula"><span>메시지</span><b>+</b><span>10진수 nonce</span><b>→</b><strong>SHA-256</strong><small>UTF-8 인코딩 · 구분자 없이 이어 붙이기</small></div></div>
      <div className="hc-workspace"><section className="hc-panel hc-controls" aria-labelledby="settings-title"><div className="hc-panel-heading"><span className="hc-step">01</span><h2 id="settings-title">탐색 조건</h2></div>
        <label className="hc-field">기록 이름<input aria-label="기록 이름" value={participantName} onChange={(e) => setParticipantName(e.target.value)} maxLength={60} disabled={active} autoComplete="nickname" /><small>이름·별칭별로 발견 결과를 자동 저장합니다.</small></label>
        <label className="hc-field">메시지<textarea value={message} onChange={(e) => setMessage(e.target.value)} disabled={active} rows={3} /><small>띄어쓰기와 줄바꿈도 그대로 해시에 포함됩니다.</small></label>
        <label className="hc-field">목표 난이도 <span className="hc-field-note">앞의 16진수 0 개수</span><input type="number" min={1} max={64} value={difficulty} onChange={(e) => setDifficulty(Number(e.target.value))} disabled={active} /><small>난이도 {difficulty} = {difficulty * 4}비트의 0 · 평균 {meanHashes < 1e12 ? format(meanHashes) : meanHashes.toExponential(2)}회 계산</small></label>
        <div className="hc-target"><span>목표 해시</span><code>{"0".repeat(Math.min(16, Math.max(0, difficulty)))}{difficulty > 16 ? `… (${difficulty}개)` : "…"}</code></div>
        <label className="hc-field">연산 장치<select value={mode} onChange={(e) => { setMode(e.target.value); if (e.target.value === "hybrid") setWorkers(1); else if (e.target.value === "native") setWorkers(Math.min(4, cpuCount)); }} disabled={active}><option value="auto">자동 · GPU 사용 가능 시 GPU</option><option value="cpu">CPU</option><option value="gpu">GPU 시도 · 사용 불가 시 CPU</option><option value="hybrid">Python · CPU + GPU 동시 연산</option><option value="native">C · CPU + GPU 동시 연산</option></select><small>{mode === "native" ? `작업 배분과 결과 전달을 C에서 처리합니다. CPU 작업 ${Math.min(4, cpuCount)}개부터 시작합니다. C 또는 GPU를 사용할 수 없으면 이유와 실제 연산 장치를 표시합니다.` : mode === "hybrid" ? "CPU와 GPU가 다른 nonce를 탐색합니다. 동시 연산이 더 느릴 수도 있어 CPU 작업 1개부터 시작합니다." : mode === "cpu" ? "설정한 수의 CPU 작업으로 탐색합니다." : "NVIDIA CUDA를 검사하고, 사용 불가하면 CPU로 전환합니다."}</small></label>
        <div className="hc-field-row"><label className="hc-field">CPU 작업 수<input type="number" min={1} max={cpuCount} value={workers} onChange={(e) => setWorkers(Number(e.target.value))} disabled={active} /></label><label className="hc-field">시작 nonce<input inputMode="numeric" value={startNonce} onChange={(e) => setStartNonce(e.target.value)} disabled={active} /></label></div>
        <div className="hc-actions"><button className="hc-primary" onClick={() => void start()} disabled={active || !connected || unsavedResult}>탐색 시작 <span>→</span></button><button className="hc-secondary" onClick={() => void stop()} disabled={!active}>중지</button></div>{unsavedResult && <p className="hc-error" role="status">새 탐색 전에 발견 결과의 기록 저장을 다시 시도하세요.</p>}<p className="hc-subtle">난이도가 1 늘면 평균 계산량은 16배입니다. 실제 발견 시간은 매번 달라지며 보장되지 않습니다.</p>{error && <p className="hc-error" role="alert">{error}</p>}
      </section><section className="hc-panel hc-search" aria-labelledby="search-title"><div className="hc-panel-heading"><span className="hc-step">02</span><h2 id="search-title">탐색 관찰</h2><span className={`hc-status ${job?.status ?? "idle"}`} role="status">{job ? labels[job.status] : "입력 후 시작하세요"}</span></div>
        <div className="hc-engine"><span className={active ? "hc-pulse" : "hc-dot"} /><div><strong>{job?.backend ?? "연산 장치 자동 선택"}</strong><p>{job?.reason ?? "메시지와 난이도를 정하면, 실제 해시 계산을 시작합니다."}</p></div></div>
        <div className="hc-metrics"><div><span>확인된 계산 수</span><strong>{job ? numberText(job.checked) : "0"}</strong><small>완료 보고를 받은 계산</small></div><div><span>평균 처리 속도</span><strong>{rate(job?.hashesPerSecond ?? 0)}</strong><small>장치 준비 시간 포함</small></div><div><span>경과 시간</span><strong>{format(job?.elapsed ?? 0)}<em>초</em></strong><small>서버에서 측정한 시간</small></div></div>
        {(job?.mode === "hybrid" || job?.mode === "native") && <div className="hc-device-work" aria-label="장치별 완료 계산" data-testid="device-work"><p>CPU <strong data-testid="cpu-checked">{numberText(job.cpuChecked ?? "0")}</strong>회</p><p>GPU <strong data-testid="gpu-checked">{numberText(job.gpuChecked ?? "0")}</strong>회</p><p>실제 연산 겹침 <strong>{job.finished ? new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 3 }).format(job.hybridKernelOverlapLowerBound ?? 0) : "측정 중"}</strong>{job.finished ? "초 이상" : ""}</p><small>완료된 배치의 계산 수입니다. CPU 계산과 GPU 커널 실행이 겹친 시간의 하한을 종료 후 표시합니다.{job.hybridTimingLimited ? job.mode === "native" ? " 처음 기록된 완료 배치 일부 기준입니다." : " 최근 완료 배치 기준입니다." : ""}</small></div>}
        {job?.cleanupWarning && <p className="hc-subtle" role="status">{job.cleanupWarning}</p>}
        <div className={`hc-hash-display ${job?.result ? "success" : ""}`}><div className="hc-hash-title"><span>{job?.result ? "발견한 nonce" : "최근 계산한 nonce"}</span><b>{job?.result?.nonce ?? job?.lastNonce ?? "—"}</b></div><div className="hc-hash-label">SHA-256 결과 <span>{job?.result ? "서버 재검증 통과" : "실제 계산 표본"}</span></div><code className="hc-hash" data-testid="display-hash">{shownHash ? <><mark>{shownHash.slice(0, zeroCount)}</mark>{shownHash.slice(zeroCount)}</> : <span className="hc-empty-hash">해시 결과가 여기에 표시됩니다.</span>}</code></div>
        {job?.result && <div className="hc-proof"><p><strong>목표 조건 충족</strong> · 앞의 0 {job.difficulty}개 이상</p><p>입력: <code>{JSON.stringify(job.message)} + "{job.result.nonce}"</code></p><button className="hc-secondary" onClick={() => void verify()}>해시 다시 검증</button>{verification && <p role="status">{verification}</p>}<div className={`hc-save-state ${job.saveError ? "failed" : ""}`}><p><strong>발견 결과 자동 저장</strong></p>{job.recordId ? <p role="status">{job.participantName || "나"}의 난이도 {job.difficulty} 기록으로 저장 완료했습니다.</p> : job.saveError ? <><p className="hc-error" role="alert">기록을 저장하지 못했습니다: {job.saveError}</p><button className="hc-secondary" onClick={() => void retrySave()} disabled={savePending || !job.finished}>{savePending ? "저장 중…" : "기록 저장 다시 시도"}</button></> : <p role="status">{job.finished ? "기록 저장을 확인하는 중…" : "탐색 정리 후 기록을 저장합니다…"}</p>}</div></div>}{job?.error && <p className="hc-error" role="alert">{job.error}</p>}
        <div className="hc-samples"><div className="hc-sample-heading"><h3>최근 계산 표본</h3><span>화면은 약 0.35초마다 갱신</span></div>{samples.length ? <ol>{samples.map((sample) => <li key={sample.nonce}><span>{numberText(sample.nonce)}</span><code>{sample.hash}</code></li>)}</ol> : <p className="hc-placeholder">탐색을 시작하면 실제 계산한 nonce와 해시를 보여줍니다.</p>}<p className="hc-subtle">표본만 화면에 표시해 출력 비용을 줄입니다. 병렬 탐색은 조건을 만족하는 nonce를 찾으며 최소값을 보장하지 않습니다.</p></div>
      </section></div><HistoryPanel currentMessage={message} currentParticipant={participantName} refreshKey={job?.recordId ?? ""} active={active} onLoad={loadHistoryRecord} /><footer className="hc-footer"><p>독립 해시캐시 실습 · 실제 메시지와 nonce로 계산한 단일 SHA-256</p><a href="https://github.com/k9102/Nonce" target="_blank" rel="noreferrer">비교 원본: k9102/Nonce ↗</a></footer>
    </main>
  </div>;
}
