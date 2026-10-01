import {
  NODE_IDS,
  type Block,
  type LabSnapshot,
  type NodeId,
  type Transaction,
  type UnsignedTransaction,
  type UTXO,
  type ValidationResult,
} from "../api/types";

/** 화면 이동과 무관하게, 현재 관찰하는 거래 한 건의 실제 데이터를 보관한다. */
export interface TransactionLesson {
  origin: NodeId;
  phase: "draft" | "signed" | "validated" | "mempool";
  draft: UnsignedTransaction;
  signed?: Transaction;
  checked?: Transaction;
  inputs: UTXO[];
  validation?: ValidationResult;
  experiment?: string;
  // 백엔드가 계산한 서명 원문과 그 SHA-256.
  signingText: string;
  signingHash: string;
  baseline?: {
    node: NodeId;
    balances: LabSnapshot["nodes"][NodeId]["balances"];
  };
}
export interface LearningFlow {
  current: number;
  completed: number[];
  failed: boolean;
  summary: string;
}
export function learningFlow(
  lab: LabSnapshot | null,
  lesson: TransactionLesson | null,
  mining: { status: string; block?: Block },
): LearningFlow {
  const flow = (
    current: number,
    summary: string,
    failed = false,
  ): LearningFlow => ({
    current,
    completed: Array.from({ length: current }, (_, i) => i),
    failed,
    summary,
  });
  if (!lab) return flow(0, "지갑을 생성하고 실습을 시작하세요.");
  if (!lesson) return flow(0, "지갑 준비 완료 · 새 거래를 작성하세요.");
  if (lesson.phase === "draft")
    return flow(1, "거래 작성 완료 · 아직 서명하지 않았습니다.");
  if (!lesson.validation)
    return flow(2, "전자서명 완료 · 노드 검증을 실행하세요.");
  if (!lesson.validation.valid)
    return flow(
      2,
      `${lesson.experiment ?? "거래"} 검증 실패 · ${lesson.validation.code}`,
      true,
    );

  const id = lesson.checked?.id ?? lesson.signed?.id;
  const includes = (block: Block) =>
    block.transactions.some((tx) => tx.id === id);
  const accepted = NODE_IDS.filter((node) =>
    lab.nodes[node].chain.some(includes),
  );
  if (accepted.length === NODE_IDS.length)
    return flow(6, "선택 거래가 세 노드의 블록체인에 포함되었습니다.");
  if (accepted.length)
    return flow(
      5,
      `선택 거래의 블록을 수락한 노드: ${accepted.join(", ")} · 나머지 노드의 수신·검증 대기`,
    );
  if (
    (mining.status === "running" || mining.status === "paused") &&
    mining.block &&
    includes(mining.block)
  )
    return flow(4, "선택 거래가 고정된 채굴 후보에 포함되었습니다.");
  if (lesson.phase === "validated")
    return flow(
      2,
      "검증 통과 · Mempool 등록은 이미 완료됐습니다. 검증 결과를 관찰한 뒤 Mempool을 확인하세요.",
    );
  if (lab.nodes[lesson.origin].mempool.some((tx) => tx.id === id))
    return flow(
      3,
      `Node ${lesson.origin}의 Mempool에서 대기 중 · 확정 잔액은 아직 변하지 않았습니다.`,
    );
  return flow(
    3,
    "선택 거래가 현재 Mempool에 없습니다. 노드의 검증 결과를 확인하세요.",
  );
}
