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

/** 화면 이동과 무관하게, 현재 관찰하는 거래 한 건의 실제 데이터를 보관한다 */
export interface TransactionLesson {
  restored?: boolean;
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
  if (!lab) return flow(0, "지갑을 생성하고 실습을 시작하세요");
  if (!lesson) return flow(0, "지갑 준비 완료 · 새 거래를 작성하세요");
  if (lesson.phase === "draft")
    return flow(1, "거래 작성 완료 · 아직 서명하지 않았습니다");
  if (!lesson.validation)
    return flow(2, "전자서명 완료 · 노드 검증을 실행하세요");
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
    return flow(6, "선택 거래가 세 노드의 블록체인에 포함되었습니다");
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
    return flow(4, "선택 거래가 고정된 채굴 후보에 포함되었습니다");
  if (
    lab.race &&
    Object.values(lab.race.jobs).some((job) => includes(job.block))
  )
    return flow(
      4,
      "선택 거래가 채굴 후보에 포함되었습니다 노드별 경쟁을 관찰하세요",
    );
  if (lab.nodes[lesson.origin].mempool.some((tx) => tx.id === id))
    return flow(
      3,
      `검증 통과 · Node ${lesson.origin}의 Mempool에서 대기 중 · 확정 잔액은 아직 변하지 않았습니다`,
    );
  return flow(
    3,
    "선택 거래가 현재 Mempool에 없습니다 노드의 검증 결과를 확인하세요",
  );
}

/** 서버가 이미 보관·검증한 일반 거래에서 관찰 상태만 복원한다 암호 규칙을 재구현하지 않는다 */
export function restoreLessons(lab: LabSnapshot): TransactionLesson[] {
  const transactions = new Map<string, Transaction>();
  const outputs = new Map<string, UTXO>();
  for (const id of NODE_IDS) {
    for (const block of lab.nodes[id].chain) {
      for (const tx of block.transactions) {
        tx.outputs.forEach((output, index) => outputs.set(`${tx.id}:${index}`, {
          ...output, txId: tx.id, outputIndex: index,
          createdHeight: block.height, coinbase: tx.kind === "coinbase",
        }));
        if (tx.inputs.length && (!tx.kind || tx.kind === "regular")) transactions.set(tx.id, tx);
      }
    }
    Object.entries(lab.nodes[id].utxos).forEach(([key, output]) => outputs.set(key, output));
  }
  for (const id of NODE_IDS)
    for (const tx of lab.nodes[id].mempool) transactions.set(tx.id, tx);
  const lessons: TransactionLesson[] = [];
  for (const tx of transactions.values()) {
    const inputs = tx.inputs.map(input => outputs.get(`${input.txId}:${input.outputIndex}`));
    // 공개 snapshot만으로 금액을 알 수 없는 과거 분기의 입력을 추정하지 않는다
    if (inputs.some(input => !input)) continue;
    const origin = NODE_IDS.find(id => lab.nodes[id].mempool.some(t => t.id === tx.id)
      || lab.nodes[id].chain.some(b => b.transactions.some(t => t.id === tx.id)))!;
    lessons.push({ restored: true, origin, phase: "mempool", draft: tx, signed: tx, checked: tx,
      inputs: inputs as UTXO[], signingText: "", signingHash: "",
      validation: { valid: true, code: "RESTORED", message: "저장된 노드가 수락한 거래입니다 복원 과정에서 새 검증을 실행한 것은 아닙니다", steps: [] },
    });
  }
  return lessons.slice(-20);
}

export const lessonId = (lesson: TransactionLesson) => lesson.checked?.id ?? lesson.signed?.id ?? "";
