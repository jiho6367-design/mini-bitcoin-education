import { useEffect, useRef, useState } from "react";

export type HistoryRecord = {
  id: string;
  jobId: string;
  participantName: string;
  message: string;
  difficulty: number;
  nonce: string;
  hash: string;
  backend: string;
  checked: string;
  elapsed: number;
  createdAt: string;
};

type HistoryResponse = { records: HistoryRecord[]; participants: string[]; total: number };
type Props = {
  currentMessage: string;
  currentParticipant: string;
  refreshKey: string;
  active: boolean;
  onLoad: (record: HistoryRecord) => void;
};

const dateText = (value: string) => new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
const numberText = (value: string) => BigInt(value).toLocaleString("ko-KR");
const secondsText = (value: number) => new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(value);

function RecordCard({ record, active, onLoad }: { record: HistoryRecord; active: boolean; onLoad: (record: HistoryRecord) => void }) {
  const [verification, setVerification] = useState("");
  const [verifying, setVerifying] = useState(false);

  async function verify() {
    setVerifying(true);
    try {
      const bytes = new TextEncoder().encode(record.message + record.nonce);
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
      const hash = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
      setVerification(hash === record.hash && hash.startsWith("0".repeat(record.difficulty))
        ? "저장된 메시지로 다시 계산한 해시가 일치합니다."
        : "저장된 해시와 재계산 결과가 일치하지 않습니다.");
    } catch {
      setVerification("브라우저에서 SHA-256을 재계산할 수 없습니다.");
    } finally {
      setVerifying(false);
    }
  }

  return <article className="hc-history-record" data-testid="history-record" data-record-id={record.id}>
    <div className="hc-record-heading"><strong>{record.participantName}</strong><span className="hc-record-difficulty">난이도 {record.difficulty}</span><time dateTime={record.createdAt}>{dateText(record.createdAt)}</time></div>
    <dl className="hc-record-proof">
      <div className="hc-record-message"><dt>메시지</dt><dd><code>{JSON.stringify(record.message)}</code></dd></div>
      <div className="hc-record-nonce"><dt>nonce</dt><dd><code data-testid="history-nonce">{record.nonce}</code></dd></div>
      <div className="hc-record-hash"><dt>SHA-256</dt><dd><code data-testid="history-hash"><mark>{record.hash.slice(0, record.difficulty)}</mark>{record.hash.slice(record.difficulty)}</code></dd></div>
    </dl>
    <div className="hc-record-bottom"><p><span>{record.backend}</span><span>{numberText(record.checked)}회 계산 · {secondsText(record.elapsed)}초</span></p><div className="hc-record-actions"><button className="hc-secondary" onClick={() => void verify()} disabled={verifying}>{verifying ? "검증 중…" : "기록 해시 검증"}</button><button className="hc-secondary" onClick={() => onLoad(record)} disabled={active}>조건 불러오기</button></div></div>
    {verification && <p className="hc-record-verification" role="status">{verification}</p>}
  </article>;
}

export function HistoryPanel({ currentMessage, currentParticipant, refreshKey, active, onLoad }: Props) {
  const [participant, setParticipant] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [onlyCurrentMessage, setOnlyCurrentMessage] = useState(false);
  const [records, setRecords] = useState<HistoryRecord[]>([]);
  const [participants, setParticipants] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const filteredMessage = onlyCurrentMessage ? currentMessage : null;
  const query = new URLSearchParams();
  if (participant) query.set("participantName", participant);
  if (difficulty) query.set("difficulty", difficulty);
  if (filteredMessage !== null) query.set("message", filteredMessage);
  const filterQuery = query.toString();
  const currentFilterQuery = useRef(filterQuery);

  useEffect(() => { currentFilterQuery.current = filterQuery; }, [filterQuery]);

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    setLoading(true); setError(""); setNotice(""); setRecords([]); setTotal(0);
    const fetchHistory = async () => {
      try {
        const response = await fetch(`/api/hashcash/history?${filterQuery}${filterQuery ? "&" : ""}limit=100`, { signal: controller.signal });
        const body: HistoryResponse & { error?: string } = await response.json();
        if (!response.ok) throw new Error(body.error || "발견 기록을 불러오지 못했습니다.");
        if (disposed) return;
        setRecords(body.records); setParticipants(body.participants); setTotal(body.total);
      } catch (exc) {
        if (!disposed) setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (!disposed) setLoading(false);
      }
    };
    void fetchHistory();
    return () => { disposed = true; controller.abort(); };
  }, [filterQuery, refreshKey, reload]);

  async function exportHistory() {
    const exportFilterQuery = filterQuery;
    setExporting(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/hashcash/history/export${filterQuery ? `?${filterQuery}` : ""}`);
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.error || "기록을 내보내지 못했습니다.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const now = new Date();
      const date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
      link.href = url; link.download = `hashcash-history-${date}.json`;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (currentFilterQuery.current === exportFilterQuery) setNotice("선택한 필터의 전체 기록을 JSON 파일로 내보냈습니다.");
    } catch (exc) {
      if (currentFilterQuery.current === exportFilterQuery) setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setExporting(false);
    }
  }

  const names = Array.from(new Set([...participants, participant, currentParticipant.trim()].filter(Boolean))).sort((a, b) => a.localeCompare(b, "ko-KR"));
  function loadRecord(record: HistoryRecord) {
    onLoad(record);
    setNotice("기록의 이름·메시지·난이도를 탐색 조건에 불러왔습니다. 시작 nonce는 0입니다.");
  }

  return <section className="hc-panel hc-history" aria-labelledby="history-title" data-testid="history-panel">
    <div className="hc-panel-heading"><span className="hc-step">03</span><h2 id="history-title">발견 기록</h2><span className="hc-history-count" data-testid="history-record-count">{loading ? "기록을 불러오는 중…" : `총 ${total.toLocaleString("ko-KR")}건`}</span></div>
    <div className="hc-history-intro"><p>찾은 nonce와 원본 메시지를 이름·난이도별로 자동 저장합니다.<br />기록은 이 연산 서버에 보관되어 서버를 다시 시작해도 남습니다.</p><div className="hc-history-tools"><button className="hc-secondary" onClick={() => setReload((value) => value + 1)} disabled={loading}>기록 새로고침</button><button className="hc-secondary" onClick={() => void exportHistory()} disabled={exporting || loading || total === 0}>{exporting ? "내보내는 중…" : "JSON 내보내기"}</button></div></div>
    <div className="hc-history-filters"><label className="hc-field">기록 이름 필터<select aria-label="기록 이름 필터" value={participant} onChange={(event) => setParticipant(event.target.value)}><option value="">전체 이름</option>{names.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label className="hc-field">기록 난이도 필터<select aria-label="기록 난이도 필터" value={difficulty} onChange={(event) => setDifficulty(event.target.value)}><option value="">전체 난이도</option>{Array.from({ length: 64 }, (_, index) => <option key={index + 1} value={index + 1}>난이도 {index + 1}</option>)}</select></label><label className="hc-history-checkbox"><input type="checkbox" aria-label="입력 조건으로 한정" checked={onlyCurrentMessage} onChange={(event) => setOnlyCurrentMessage(event.target.checked)} />현재 메시지만</label></div>
    <p className="hc-history-note">이름은 기록을 구분하는 별칭입니다. 이 서버를 사용하는 사람은 전체 기록을 볼 수 있습니다.{total > records.length && !loading ? ` 최근 ${records.length}건을 표시하며, JSON에는 필터에 맞는 전체 기록이 포함됩니다.` : ""}</p>
    {error && <p className="hc-error" role="alert">{error}</p>}
    {notice && <p className="hc-history-notice" role="status">{notice}</p>}
    {!loading && !error && records.length === 0 && <p className="hc-history-empty">{participant || difficulty || onlyCurrentMessage ? "선택한 조건에 맞는 발견 기록이 없습니다." : "아직 발견 기록이 없습니다. 이름을 입력하고 탐색을 시작해 보세요."}</p>}
    {records.length > 0 && <div className="hc-history-records">{records.map((record) => <RecordCard key={record.id} record={record} active={active} onLoad={loadRecord} />)}</div>}
  </section>;
}
