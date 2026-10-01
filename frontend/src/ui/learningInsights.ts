import { NODE_IDS, type LabSnapshot, type NodeId } from "../api/types";
import type { TransactionLesson } from "./learning";

export function transactionPresence(
  lab: LabSnapshot,
  lesson: TransactionLesson,
  nodeId: NodeId,
) {
  const id = lesson.checked?.id ?? lesson.signed?.id;
  const node = lab.nodes[nodeId];
  const block = id
    ? node.chain.find((b) => b.transactions.some((t) => t.id === id))
    : undefined;
  if (block)
    return { state: "included", label: `✓ 블록 #${block.height} 포함`, block };
  if (id && node.mempool.some((t) => t.id === id))
    return { state: "pending", label: "◷ Mempool 대기" };
  if (lesson.origin === nodeId && lesson.validation && !lesson.validation.valid)
    return { state: "rejected", label: "✕ 검증 거부" };
  return {
    state: "unseen",
    label: id ? "○ 미수신 · 미등록" : "○ 아직 제출 전",
  };
}

export function lessonTotals(lesson: TransactionLesson) {
  const tx = lesson.checked ?? lesson.signed ?? lesson.draft;
  return {
    input: lesson.inputs.reduce((sum, input) => sum + input.amount, 0),
    sent: tx.outputs[0]?.amount ?? 0,
    change: tx.outputs.slice(1).reduce((sum, output) => sum + output.amount, 0),
  };
}

export function allIncluded(lab: LabSnapshot, lesson: TransactionLesson) {
  return NODE_IDS.every(
    (id) => transactionPresence(lab, lesson, id).state === "included",
  );
}

export function failureExplanation(code: string) {
  const explanations: Record<string, string> = {
    SIGNATURE:
      "거래 내용과 서명·공개키의 관계를 확인하세요. 정상 거래를 다시 작성하고 서명하면 비교할 수 있습니다.",
    DOUBLE_SPEND:
      "같은 입력을 먼저 사용한 대기 거래가 있습니다. 입력은 금액 일부가 아니라 출력 전체 단위로 예약됩니다.",
    MISSING_UTXO:
      "이 노드에서 입력을 찾을 수 없습니다. 이미 소비됐는지, 필요한 블록을 전달받았는지 확인하세요.",
    TX_ID:
      "거래 내용이나 서명이 달라져 저장된 거래 ID와 다시 계산한 값이 맞지 않습니다.",
    TRANSACTIONS_HASH:
      "거래 내용이 바뀌어 블록에 저장된 거래 목록 해시와 다릅니다. 해시 재계산 후 다른 검사도 비교하세요.",
    BLOCK_HASH: "시각·Nonce 등 헤더 데이터와 저장된 블록 해시가 맞지 않습니다.",
    POW: "실제 블록 해시가 목표값을 초과했습니다. 해시가 일치해도 작업증명 조건까지 만족해야 합니다.",
    LINK: "이전 블록의 해시와 이 블록의 Previous Hash를 비교하세요. 앞 블록의 변경은 다음 블록과의 연결에 영향을 줍니다.",
    VALUE:
      "입력 합계와 출력 합계를 비교하세요. 이 실습은 수수료가 0이므로 두 합계가 같아야 합니다.",
  };
  return (
    explanations[code] ??
    "변경한 데이터와 아래 실패 항목을 비교하세요. 필요한 조건을 맞춘 뒤 다시 실행할 수 있습니다."
  );
}
