import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { NodeId } from "../api/types";
import type { RoomState } from "./useClassroom";
import {
  collectGuideTransactions, deriveGuideProgress, pickPendingGuideTransaction,
  type GuideProgress,
} from "./teamGuideProgress";
import "./teamPracticeGuide.css";

export type GuidePage = "거래" | "채굴" | "노드·블록체인" | "활동 기록";

interface GuideStep {
  title: string;
  instructions: ReactNode[];
  check: ReactNode;
  destination: GuidePage | null;
  action: string;
}

const steps: GuideStep[] = [
  {
    title: "역할 선택",
    instructions: [
      <>각자 <strong>팀 A·B·C 맡기</strong>에서 비어 있는 팀을 하나 선택하세요 나머지 팀원은 관찰할 수 있습니다</>,
      <>공통 채굴·전파 버튼을 누를 진행자 한 명을 정하세요 교사 계정은 필요 없습니다</>,
    ],
    check: "서로 다른 팀을 맡았는지 확인하세요 관찰자는 역할을 선택하지 않고 따라갈 수 있습니다",
    destination: null,
    action: "팀 선택으로 이동",
  },
  {
    title: "자동 전달",
    instructions: [
      <>진행자가 <strong>노드·블록체인 → 메시지 자동 전달</strong>을 켜세요</>,
      "다른 팀원은 같은 설정을 함께 사용합니다 전파 설정은 진행자 한 명이 조작하세요",
    ],
    check: "메시지 자동 전달이 체크돼 있는지 확인하세요",
    destination: "노드·블록체인",
    action: "전파 설정으로 이동",
  },
  {
    title: "거래 만들기",
    instructions: [
      <>이미 대기 중인 거래가 있으면 새로 작성하지 말고 <strong>4단계</strong>에서 이어가세요</>,
      <>송금할 팀 담당자가 <strong>자기 팀 초기 지갑</strong>과 받는 팀을 선택하세요 예를 들어 <strong>A → B</strong>로 보냅니다</>,
      <>잔액 범위 안에서 예를 들어 <strong>송금액 10 · 수수료 1</strong>을 입력하세요</>,
      <><strong>1. 거래 작성 → 2. 전자서명 → 3. 검증·전파</strong> 순서로 누르세요</>,
    ],
    check: "거래 검증이 승인됐는지 확인하세요 B·C 담당자는 같은 거래가 전달되는 과정을 관찰하세요",
    destination: "거래",
    action: "거래 화면으로 이동",
  },
  {
    title: "거래 전달 확인",
    instructions: [
      <>거래 화면의 <strong>관찰 거래</strong>에서 방금 만들었거나 이전에 대기 중이던 거래를 선택하세요</>,
      <>A·B·C 모두 <strong>Mempool 대기</strong>인지 확인하세요 이미 채굴했다면 블록 포함 표시를 확인하세요</>,
    ],
    check: "아직 미수신·미등록인 노드가 있으면 자동 전달 설정을 확인하고 잠시 기다리세요",
    destination: "거래",
    action: "거래 화면으로 이동",
  },
  {
    title: "함께 채굴",
    instructions: [
      <>진행자가 <strong>안내 실습 · 빠르게</strong>를 선택하고 <strong>세 후보 준비</strong>를 누르세요</>,
      <>A·B·C 담당자가 각각 <strong>내 채굴 참여</strong>를 켠 뒤 진행자가 <strong>전체 시작 / 계속</strong>을 누르세요</>,
      "새 후보를 준비할 때마다 각 담당자는 채굴 참여를 다시 켜야 합니다",
    ],
    check: "첫 블록을 찾으면 안내 실습은 자동으로 일시정지됩니다 발견한 블록과 전파 과정을 확인하세요",
    destination: "채굴",
    action: "채굴 화면으로 이동",
  },
  {
    title: "결과 확인",
    instructions: [
      <>자동 전파를 기다린 뒤 거래 화면에서 같은 <strong>관찰 거래</strong>를 선택하세요</>,
      <>A·B·C 모두 <strong>블록 포함</strong>인지 확인하고 송금한 팀과 받은 팀의 잔액을 비교하세요</>,
      "기록은 자동 저장됩니다 같은 참여 링크로 이어서 실습하고 서버 재시작 후에는 역할을 다시 선택하세요",
    ],
    check: "세 노드가 같은 거래를 블록에 반영했는지 함께 확인하세요",
    destination: "거래",
    action: "거래 화면으로 이동",
  },
];

const bookmarkKey = (room: RoomState) => `team-practice-guide-target:${room.code}:${room.lab.sessionId}`;
const shortId = (id: string) => `${id.slice(0, 10)}…${id.slice(-6)}`;

function initialTarget(room: RoomState): string | null {
  try {
    const saved = localStorage.getItem(bookmarkKey(room));
    if (saved === "") return null;
    if (saved && /^[0-9a-f]{64}$/.test(saved)) return saved;
  } catch { /* The live guide also works without browser storage. */ }
  return pickPendingGuideTransaction(room);
}

function saveTarget(key: string, id: string | null) {
  try { localStorage.setItem(key, id || ""); } catch { /* Keep the current page usable. */ }
}

/** A shared observation target, never an instruction to execute a transaction. */
export function useGuideTransaction(room: RoomState | null) {
  const key = room ? bookmarkKey(room) : null;
  const [selection, setSelection] = useState<{ key: string | null; id: string | null }>({ key: null, id: null });
  const known = useRef<{ key: string | null; ids: Set<string> }>({ key: null, ids: new Set() });
  const transactionId = room && selection.key !== key ? initialTarget(room) : selection.id;

  useEffect(() => {
    if (!room || !key) return;
    const transactions = collectGuideTransactions(room);
    if (known.current.key !== key) {
      known.current = { key, ids: new Set(transactions.map((transaction) => transaction.id)) };
      setSelection({ key, id: initialTarget(room) });
      return;
    }
    const fresh = transactions.filter((transaction) => !known.current.ids.has(transaction.id));
    for (const transaction of transactions) known.current.ids.add(transaction.id);
    if (selection.key === key && selection.id === null && fresh.length)
      setSelection({ key, id: fresh[0].id });
  }, [room, key, selection.key, selection.id]);

  useEffect(() => {
    // Empty is a deliberate "wait for a new transaction" bookmark only when
    // the learner chooses it, not a side effect of opening an untouched room.
    if (key && selection.key === key && selection.id) saveTarget(key, selection.id);
  }, [key, selection]);

  const selectTransaction = useCallback((id: string | null) => {
    const activeKey = key || selection.key;
    if (!activeKey) return;
    if (room && id === null) {
      known.current = { key: activeKey, ids: new Set(collectGuideTransactions(room).map((transaction) => transaction.id)) };
    }
    setSelection({ key: activeKey, id });
    saveTarget(activeKey, id);
  }, [room, key, selection.key]);

  return { transactionId, selectTransaction };
}

export function TeamPracticeGuide({
  page,
  onNavigate,
  onRoles,
  team,
  room,
  connected,
  selectedTransactionId,
  onSelectTransaction,
}: {
  page: string;
  onNavigate: (page: GuidePage) => void;
  onRoles: () => void;
  team: NodeId | null;
  room: RoomState;
  connected: boolean;
  selectedTransactionId: string | null;
  onSelectTransaction: (id: string | null) => void;
}) {
  const [reviewed, setReviewed] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(true);
  const toggle = useRef<HTMLButtonElement>(null);
  const transactions = collectGuideTransactions(room);
  const reliable = connected && !room.storage?.error;
  const computed = deriveGuideProgress(room, selectedTransactionId);
  const lastReliable = useRef<GuideProgress | null>(null);
  if (reliable) lastReliable.current = computed;
  const progress = reliable ? computed : lastReliable.current || {
    ...computed,
    steps: computed.steps.map((item) => ({ ...item, complete: false })),
    completeCount: 0,
    nextStep: 0,
  };
  const current = reviewed ?? progress.nextStep ?? steps.length - 1;
  const step = steps[current];
  const pending = transactions.find((transaction) => transaction.id !== selectedTransactionId && transaction.pendingAt.length);
  const nextAction = reliable ? progress.nextAction : "연결 확인 중 마지막 수신 상태를 표시하고 있습니다";
  const destination = progress.nextDestination;
  const moveToNext = () => {
    if (!reliable || !destination) return;
    setReviewed(null);
    setExpanded(true);
    if (destination === "roles") onRoles();
    else onNavigate(destination);
  };
  const close = () => {
    setExpanded(false);
    toggle.current?.focus();
  };
  return (
    <section
      id="team-practice-guide"
      className="team-practice-guide panel"
      aria-labelledby="team-practice-guide-title"
      tabIndex={-1}
      data-testid="team-practice-guide"
      data-current-step={current + 1}
      data-completed-count={progress.completeCount}
      data-reliable={reliable}
    >
      <div className="team-guide-header">
        <div>
          <h2 id="team-practice-guide-title">공통 실습 가이드</h2>
          <span className="team-guide-position">{!reliable && "마지막 확인 결과 · "}완료 {progress.completeCount} / {steps.length} · 현재 안내 {current + 1} / {steps.length}</span>
        </div>
        <button
          type="button"
          className="secondary"
          ref={toggle}
          aria-expanded={expanded}
          aria-controls="team-practice-guide-body"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "가이드 접기" : "가이드 펼치기"}
        </button>
      </div>
      <div
        className="team-guide-progress"
        role="progressbar"
        aria-label="공통 실습 완료 단계"
        aria-valuemin={0}
        aria-valuemax={steps.length}
        aria-valuenow={progress.completeCount}
        aria-valuetext={`${!reliable ? "마지막 확인 결과 " : ""}${steps.length}단계 중 ${progress.completeCount}단계 완료`}
      >
        <span style={{ width: `${progress.completeCount / steps.length * 100}%` }} />
      </div>
      <div className="team-guide-next" data-testid="guide-next-action" aria-live="polite" aria-atomic="true">
        <div>
          <strong>{reliable && progress.nextStep === null ? "선택한 거래 실습 완료" : "다음 할 일"}</strong>
          <p>{nextAction}</p>
        </div>
        {reliable && destination && <button type="button" className="secondary" onClick={moveToNext}>다음 할 일 보기</button>}
        {reviewed !== null && (!reliable || !destination) && <button type="button" className="secondary" onClick={() => setReviewed(null)}>진행 상황 보기</button>}
      </div>
      <div id="team-practice-guide-body" hidden={!expanded}>
        <p className="team-guide-intro">완료 표시는 선택한 거래의 실제 노드 상태로 갱신됩니다 안내를 넘겨도 실습은 실행되지 않습니다</p>
        <label className="team-guide-target">
          가이드 확인 거래
          <select
            aria-label="가이드 확인 거래"
            value={selectedTransactionId || ""}
            disabled={!reliable}
            onChange={(event) => { onSelectTransaction(event.target.value || null); setReviewed(null); }}
          >
            <option value="">새 거래 기다리기</option>
            {selectedTransactionId && !transactions.some((transaction) => transaction.id === selectedTransactionId) && (
              <option value={selectedTransactionId}>{shortId(selectedTransactionId)} · 기록 확인 중</option>
            )}
            {transactions.map((transaction) => (
              <option key={transaction.id} value={transaction.id}>
                {shortId(transaction.id)} · {transaction.pendingAt.length ? `대기 ${transaction.pendingAt.join("·")}` : `블록 ${transaction.confirmedAt.join("·")}`}
              </option>
            ))}
          </select>
        </label>
        {!selectedTransactionId && <p className="team-guide-target-help">새 거래가 접수되면 자동으로 따라갑니다 이전에 완료한 거래는 직접 선택해 확인하세요</p>}
        {progress.steps[5].complete && pending && (
          <button type="button" className="secondary team-guide-new-target" disabled={!reliable} onClick={() => { onSelectTransaction(pending.id); setReviewed(null); }}>
            다른 대기 거래 따라가기
          </button>
        )}
        <nav className="team-guide-nav" aria-label="공통 실습 안내 단계">
          {steps.map((item, index) => (
            <button
              type="button"
              key={item.title}
              className="team-guide-step-button"
              aria-label={`가이드 ${index + 1}단계 ${item.title}`}
              aria-describedby={`team-guide-completion-${index}`}
              aria-current={index === current ? "step" : undefined}
              data-complete={progress.steps[index].complete}
              onClick={() => setReviewed(index)}
            >
              <span aria-hidden="true">{progress.steps[index].complete ? "✓" : index + 1}</span>
              <span>{item.title}<small id={`team-guide-completion-${index}`}>{progress.steps[index].complete ? "완료" : "대기"}</small></span>
            </button>
          ))}
        </nav>
        <div className="team-guide-step" aria-live="polite" aria-atomic="true">
          <h3>{current + 1}. {step.title}</h3>
          <p className="team-guide-evidence" data-testid="guide-step-evidence">{reliable ? progress.steps[current].detail : "연결 확인 중 마지막 수신 상태"}</p>
          <ul>{step.instructions.map((instruction, index) => <li key={index}>{instruction}</li>)}</ul>
          <p className="team-guide-check"><strong>확인할 내용</strong>{step.check}</p>
        </div>
        {selectedTransactionId && (
          <div className="team-guide-node-status" aria-label="선택한 거래의 노드 상태">
            {progress.nodes.map((node) => (
              <span key={node.node} data-node={node.node} data-status={node.status}>
                <strong>노드 {node.node}</strong> · {node.status === "confirmed" ? `블록 #${node.height} 포함` : node.status === "pending" ? "Mempool 대기" : node.status === "invalid" ? "체인 검증 확인 필요" : node.status === "conflict" ? "다른 거래 내용 확인 필요" : "미수신·미등록"}
              </span>
            ))}
          </div>
        )}
        <div className="team-guide-state" aria-label="현재 실습 상태">
          <span>{connected ? "실시간 연결됨" : "연결 대기"}</span>
          <span>{team ? `내 역할 · 팀 ${team}` : "내 역할 · 관찰"}</span>
          <span>자동 전달 · {room.automatic ? "켜짐" : "꺼짐"}</span>
        </div>
        <div className="team-guide-actions">
          <button
            type="button"
            className={step.destination === page ? "secondary" : undefined}
            onClick={() => step.destination ? onNavigate(step.destination) : onRoles()}
          >
            {step.action}
          </button>
          <div className="team-guide-pagination">
            <button type="button" className="secondary" disabled={current === 0} onClick={() => setReviewed(current - 1)}>이전 단계</button>
            {current < steps.length - 1
              ? <button type="button" onClick={() => setReviewed(current + 1)}>다음 단계</button>
              : <button type="button" onClick={close}>안내 접기</button>}
          </div>
        </div>
      </div>
    </section>
  );
}
