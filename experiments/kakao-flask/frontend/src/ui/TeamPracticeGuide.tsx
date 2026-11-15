import { useRef, useState, type ReactNode } from "react";
import type { NodeId } from "../api/types";
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
      <>A 담당이 <strong>팀 A 초기 지갑</strong>에서 받는 팀 <strong>B</strong>를 선택하세요</>,
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
      <>거래 화면의 <strong>관찰 거래</strong>에서 방금 만든 거래를 선택하세요</>,
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

export function TeamPracticeGuide({
  page,
  onNavigate,
  onRoles,
  team,
  automatic,
  connected,
}: {
  page: string;
  onNavigate: (page: GuidePage) => void;
  onRoles: () => void;
  team: NodeId | null;
  automatic: boolean;
  connected: boolean;
}) {
  const [current, setCurrent] = useState(0);
  const [expanded, setExpanded] = useState(true);
  const toggle = useRef<HTMLButtonElement>(null);
  const step = steps[current];
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
    >
      <div className="team-guide-header">
        <div>
          <h2 id="team-practice-guide-title">공통 실습 가이드</h2>
          <span className="team-guide-position">현재 안내 {current + 1} / {steps.length}</span>
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
      <div id="team-practice-guide-body" hidden={!expanded}>
        <p className="team-guide-intro">각 화면에서 직접 조작하고 결과를 확인한 뒤 다음 단계로 이동하세요</p>
        <nav className="team-guide-nav" aria-label="공통 실습 안내 단계">
          {steps.map((item, index) => (
            <button
              type="button"
              key={item.title}
              className="team-guide-step-button"
              aria-label={`가이드 ${index + 1}단계 ${item.title}`}
              aria-current={index === current ? "step" : undefined}
              onClick={() => setCurrent(index)}
            >
              <span aria-hidden="true">{index + 1}</span>
              {item.title}
            </button>
          ))}
        </nav>
        <div className="team-guide-step" aria-live="polite" aria-atomic="true">
          <h3>{current + 1}. {step.title}</h3>
          <ul>{step.instructions.map((instruction, index) => <li key={index}>{instruction}</li>)}</ul>
          <p className="team-guide-check"><strong>확인할 내용</strong>{step.check}</p>
        </div>
        <div className="team-guide-state" aria-label="현재 실습 상태">
          <span>{connected ? "실시간 연결됨" : "연결 대기"}</span>
          <span>{team ? `내 역할 · 팀 ${team}` : "내 역할 · 관찰"}</span>
          <span>자동 전달 · {automatic ? "켜짐" : "꺼짐"}</span>
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
            <button type="button" className="secondary" disabled={current === 0} onClick={() => setCurrent(current - 1)}>이전 단계</button>
            {current < steps.length - 1
              ? <button type="button" onClick={() => setCurrent(current + 1)}>다음 단계</button>
              : <button type="button" onClick={close}>가이드 마치기</button>}
          </div>
        </div>
      </div>
    </section>
  );
}
