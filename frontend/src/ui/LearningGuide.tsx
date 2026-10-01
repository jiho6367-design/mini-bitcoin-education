import type { LabSnapshot, NodeId } from "../api/types";
import { NODE_IDS } from "../api/types";
import type { TransactionLesson } from "./learning";
import type { MiningState } from "./useLab";
import {
  allIncluded,
  lessonTotals,
  transactionPresence,
} from "./learningInsights";
import { Panel } from "./shared";

const topics = [
  [
    "거래 하나의 여정을 따라가세요",
    "예제 실습으로 지갑을 준비하세요.",
    "직접 작성한 거래가 세 노드의 기록에 포함되는 과정을 관찰합니다.",
  ],
  [
    "서명은 작성 권한을 증명합니다",
    "금액을 입력하고 거래 작성 → 전자서명 → 검증·전파를 실행하세요.",
    "입력 전체가 수신자 출력과 거스름돈으로 나뉩니다. 제출 직후에는 확정 잔액이 그대로입니다.",
  ],
  [
    "대기와 블록 포함은 다릅니다",
    "노드별 대기 거래를 비교한 뒤 채굴 화면으로 이동하세요.",
    "Mempool의 거래는 아직 블록에 포함되지 않았습니다. 예약된 입력은 다른 거래에 쓸 수 없습니다.",
  ],
  [
    "Nonce 하나가 바꾸는 해시",
    "한 번 계산하기 또는 천천히 학습 실행으로 실제 표본을 관찰하세요.",
    "실패 표본도 실제 계산 결과입니다. 해시가 목표값 이하인 순간에만 성공합니다.",
  ],
  [
    "각 노드가 따로 검증합니다",
    "메시지 1개 전달을 누르고 수신 노드의 상태를 비교하세요.",
    "채굴한 노드에 먼저 블록이 생깁니다. 다른 노드는 전달받아 검증한 뒤 반영합니다.",
  ],
  [
    "변경한 데이터와 실패 이유 연결하기",
    "복사본 변조·검증 후 해시만 재계산한 결과도 비교하세요.",
    "금액·서명·거래 목록 해시·블록 연결 중 어떤 검사가 실패했는지 확인합니다.",
  ],
];

export function LearningGuide({
  page,
  lab,
  lesson,
  mining,
  nodeId,
  navigate,
}: {
  page: number;
  lab: LabSnapshot | null;
  lesson: TransactionLesson | null;
  mining: MiningState;
  nodeId: NodeId;
  navigate: (page: number, action?: string) => void;
}) {
  const [title, defaultAction, observation] = topics[page];
  let action = defaultAction;
  let destination = page;
  let target =
    page === 0
      ? "start"
      : page === 1
        ? "draft"
        : page === 3
          ? "mine"
          : page === 4
            ? "deliver"
            : "tamper";
  let button = "조작 위치로 이동";
  const selectedId = lesson?.checked?.id ?? lesson?.signed?.id;
  const selectedInMining =
    !!selectedId &&
    !!mining.block?.transactions.some((tx) => tx.id === selectedId);
  if (page === 0 && lab) {
    action = "진행 중인 실습이 있습니다. 지갑·거래 화면에서 계속 관찰하세요.";
    destination = 1;
    button = "실습 이어 보기";
  }
  if (page === 1 && lab && lesson) {
    if (allIncluded(lab, lesson)) {
      action =
        "이번 거래가 모두 반영됐습니다. 아래 결과를 정리하거나 금액을 바꿔 새 거래를 작성하세요.";
    } else if (
      NODE_IDS.some(
        (id) => transactionPresence(lab, lesson, id).state === "included",
      )
    ) {
      action =
        "일부 노드에 블록이 반영됐습니다. 나머지 노드의 수신·검증을 확인하세요.";
      destination = 4;
      button = "전달 상태 살펴보기";
    } else if (
      selectedInMining &&
      (mining.status === "running" || mining.status === "paused")
    ) {
      action =
        "이 거래가 채굴 후보에 포함됐습니다. 실제 계산 결과를 관찰하세요.";
      destination = 3;
      button = "채굴 결과 살펴보기";
    } else if (lesson.validation?.valid) {
      action = "검증이 통과했습니다. Mempool의 대기 상태를 확인하세요.";
      destination = 2;
      button = "대기 상태 살펴보기";
    } else if (lesson.validation && !lesson.validation.valid) {
      action =
        "검증에서 거부됐습니다. 실패 이유를 읽고 거래를 다시 작성하세요.";
    } else if (lesson.signed) {
      action = "서명을 마쳤습니다. 검증·전파로 노드의 판단을 확인하세요.";
      target = "submit";
    } else {
      action = "입력과 출력을 확인하고 전자서명하세요.";
      target = "sign";
    }
  }
  if (page === 2) {
    destination = lab?.nodes[nodeId].canStartMining
      ? 3
      : lab?.queue.length
        ? 4
        : 1;
    button =
      destination === 3
        ? "채굴 준비 살펴보기"
        : destination === 4
          ? "노드 전달 살펴보기"
          : "거래부터 준비하기";
    if (destination === 1)
      action =
        "선택 노드에서 채굴할 거래가 없습니다. 새 거래를 작성하고 제출하세요.";
    else if (destination === 4)
      action = "먼저 대기 메시지를 전달하고 노드의 상태를 비교하세요.";
  }
  if (page === 3) {
    if (mining.status === "found" && selectedInMining) {
      action = "블록을 찾았습니다. 이제 다른 노드의 수신·검증을 확인하세요.";
      destination = 4;
      button = "전달 상태 살펴보기";
    } else if (mining.status === "running") {
      action =
        "계산 중입니다. 일시정지한 뒤 최근 표본을 골라 비교할 수 있습니다.";
      target = "pause";
    } else if (mining.status === "paused") {
      action =
        "계산을 멈춰 관찰 중입니다. 한 번 더 계산하거나 계속 채굴하세요.";
    } else if (!lab?.nodes[nodeId].canStartMining) {
      action =
        "채굴할 대기 거래가 필요합니다. 거래를 제출하고 이전 블록의 전달을 마치세요.";
      destination = lab?.queue.length ? 4 : 1;
      button = "준비 단계 살펴보기";
    }
  }
  if (page === 4 && !lab?.queue.length) {
    action =
      "전달 대기열이 비었습니다. 노드별 기록을 비교하거나 새 거래를 만들어 보세요.";
    destination = 1;
    button = "거래 화면으로 이동";
  }
  if (page === 5 && (lab?.nodes[nodeId].chain.length ?? 0) < 2) {
    action =
      "먼저 거래가 담긴 블록을 채굴하세요. 두 블록을 만들면 과거 블록의 연결도 비교할 수 있습니다.";
    destination = 1;
    button = "거래부터 준비하기";
  }
  return (
    <section className="lesson-guide" aria-label="학습 안내">
      <div>
        <span className="eyebrow">이번에 배울 것</span>
        <h2>{title}</h2>
      </div>
      <div className="guide-columns">
        <div>
          <strong>① 지금 해볼 행동</strong>
          <p>{action}</p>
          <button
            className="secondary"
            onClick={() =>
              navigate(destination, destination === page ? target : undefined)
            }
          >
            {button}
          </button>
        </div>
        <div>
          <strong>② 관찰할 변화</strong>
          <p>{observation}</p>
          <small>화면을 자유롭게 이동하며 비교할 수 있습니다.</small>
        </div>
      </div>
    </section>
  );
}

export function NodeLesson({
  lab,
  lesson,
}: {
  lab: LabSnapshot;
  lesson: TransactionLesson;
}) {
  return (
    <Panel title="이 거래는 지금 어디에 있나요?" className="node-lesson">
      <div className="three-col">
        {NODE_IDS.map((id) => {
          const presence = transactionPresence(lab, lesson, id);
          return (
            <div
              className={`presence-card ${presence.state}`}
              key={id}
              data-testid={`presence-${id}`}
            >
              <strong>Node {id}</strong>
              <p key={presence.label} className="change-flash">
                {presence.label}
              </p>
            </div>
          );
        })}
      </div>
      <p className="muted">
        현재 선택 거래 기준입니다. 수신과 검증은 한 요청에서 처리되며, 전달
        결과에서 승인·거부 이유를 확인합니다.
      </p>
    </Panel>
  );
}

export function LessonRecap({
  lab,
  lesson,
  newTransaction,
  tamper,
}: {
  lab: LabSnapshot;
  lesson: TransactionLesson;
  newTransaction: () => void;
  tamper: () => void;
}) {
  if (!allIncluded(lab, lesson)) return null;
  const totals = lessonTotals(lesson);
  return (
    <Panel title="이번 거래로 확인한 것" className="lesson-recap">
      <p>
        ✓ 선택 거래가 세 노드의 블록체인에 포함되었습니다. 아래 잔액은 각 노드의
        현재 값입니다.
      </p>
      <div className="transaction-summary">
        <div>
          <span>사용한 입력 합계</span>
          <strong>{totals.input} 코인</strong>
        </div>
        <div>
          <span>보낸 금액</span>
          <strong>{totals.sent} 코인</strong>
        </div>
        <div>
          <span>거스름돈</span>
          <strong>{totals.change} 코인</strong>
        </div>
      </div>
      <div className="three-col">
        {NODE_IDS.map((id) => (
          <div className="presence-card" key={id}>
            <strong>
              Node {id} · 블록 #
              {transactionPresence(lab, lesson, id).block!.height}
            </strong>
            {lab.wallets.map((w) => (
              <p key={w.address}>
                {w.name}: {lab.nodes[id].balances[w.address]?.confirmed ?? 0}{" "}
                코인
              </p>
            ))}
          </div>
        ))}
      </div>
      <p>
        작성 → 서명 → 검증·대기 → 채굴 → 다른 노드의 검증을 거쳤습니다. 다음에는
        다른 금액을 보내거나 과거 데이터를 바꿔 결과를 비교하세요.
      </p>
      <div className="actions">
        <button onClick={newTransaction}>새 거래 만들기</button>
        <button className="secondary" onClick={tamper}>
          변조 실험으로 이동
        </button>
      </div>
    </Panel>
  );
}
