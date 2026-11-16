import { NODE_IDS, type Block, type NodeId, type Transaction } from "../api/types";
import type { RoomState } from "./useClassroom";

export interface GuideTransaction {
  id: string;
  transaction: Transaction;
  pendingAt: NodeId[];
  confirmedAt: NodeId[];
}

export interface GuideNodeProgress {
  node: NodeId;
  status: "missing" | "pending" | "confirmed" | "invalid" | "conflict";
  blockHash?: string;
  height?: number;
}

export interface GuideStepProgress {
  complete: boolean;
  detail: string;
}

export interface GuideProgress {
  steps: GuideStepProgress[];
  nextStep: number | null;
  nextAction: string;
  nextDestination: "roles" | "거래" | "채굴" | "노드·블록체인" | null;
  target: GuideTransaction | null;
  nodes: GuideNodeProgress[];
  completeCount: number;
}

interface Occurrence { transaction: Transaction; block?: Block }
interface IndexedTransaction {
  transaction: Transaction;
  occurrences: Partial<Record<NodeId, Occurrence[]>>;
}

function regular(transaction: Transaction) {
  return (!transaction.kind || transaction.kind === "regular") &&
    transaction.version === 1 && !!transaction.id && !!transaction.signature &&
    !!transaction.publicKey && transaction.inputs.length > 0;
}

// Compare semantic wire fields in a fixed order, independent of JSON object key order.
function fingerprint(transaction: Transaction) {
  return JSON.stringify({
    version: transaction.version, sessionId: transaction.sessionId,
    inputs: transaction.inputs.map(({ txId, outputIndex }) => ({ txId, outputIndex })),
    outputs: transaction.outputs.map(({ address, amount }) => ({ address, amount })),
    publicKey: transaction.publicKey, signature: transaction.signature,
    id: transaction.id, kind: transaction.kind || "regular",
    height: transaction.height, extraNonce: transaction.extraNonce,
  });
}

function evidence(room: RoomState, record: IndexedTransaction): GuideNodeProgress[] {
  const expected = fingerprint(record.transaction);
  return NODE_IDS.map((node) => {
    const occurrences = record.occurrences[node] || [];
    if (!room.lab.nodes[node].chainValidation.valid) return { node, status: "invalid" };
    if (!occurrences.length) return { node, status: "missing" };
    const blocks = occurrences.filter((entry) => entry.block);
    if (occurrences.some((entry) => fingerprint(entry.transaction) !== expected) || blocks.length > 1)
      return { node, status: "conflict" };
    const block = blocks[0]?.block;
    if (block?.hash) return { node, status: "confirmed", blockHash: block.hash, height: block.height };
    return { node, status: "pending" };
  });
}

function scan(room: RoomState) {
  const records = new Map<string, IndexedTransaction>();
  const add = (node: NodeId, transaction: Transaction, block?: Block) => {
    if (!regular(transaction)) return;
    let record = records.get(transaction.id);
    if (!record) {
      record = { transaction, occurrences: {} };
      records.set(transaction.id, record);
    }
    (record.occurrences[node] ||= []).push({ transaction, block });
  };
  for (const node of NODE_IDS) {
    for (const transaction of room.lab.nodes[node].mempool) add(node, transaction);
    for (const block of room.lab.nodes[node].chain)
      if (block.height > 0) for (const transaction of block.transactions) add(node, transaction, block);
  }
  return [...records.values()].map((record) => {
    const nodes = evidence(room, record);
    const transaction: GuideTransaction = {
      id: record.transaction.id, transaction: record.transaction,
      pendingAt: nodes.filter((entry) => entry.status === "pending").map((entry) => entry.node),
      confirmedAt: nodes.filter((entry) => entry.status === "confirmed").map((entry) => entry.node),
    };
    return { transaction, nodes };
  }).sort((a, b) => a.transaction.id < b.transaction.id ? -1 : a.transaction.id > b.transaction.id ? 1 : 0);
}

/** Active ledger evidence only: queued messages, candidates and race history do not prove acceptance. */
export function collectGuideTransactions(room: RoomState): GuideTransaction[] {
  return scan(room).map((entry) => entry.transaction);
}

/** A returning learner may follow a pending transaction; historical blocks are never auto-selected. */
export function pickPendingGuideTransaction(room: RoomState): string | null {
  return collectGuideTransactions(room).find((transaction) => transaction.pendingAt.length > 0)?.id || null;
}

export function deriveGuideProgress(room: RoomState, selectedTxId: string | null): GuideProgress {
  const transactions = scan(room);
  const selected = selectedTxId ? transactions.find((entry) => entry.transaction.id === selectedTxId) : undefined;
  const pending = transactions.find((entry) => entry.transaction.pendingAt.length > 0);
  const target = selected?.transaction || null;
  const nodes: GuideNodeProgress[] = selected?.nodes || NODE_IDS.map((node) => ({ node, status: "missing" }));
  const accepted = nodes.filter((node) => node.status === "pending" || node.status === "confirmed");
  const confirmed = nodes.filter((node) => node.status === "confirmed");
  const representatives = room.participants.filter((person) =>
    person.connected && person.role === "representative" && person.team && NODE_IDS.includes(person.team));
  const sameBlock = confirmed.length === NODE_IDS.length &&
    new Set(confirmed.map((node) => node.blockHash)).size === 1 &&
    new Set(confirmed.map((node) => node.height)).size === 1;
  const tips = NODE_IDS.map((node) => room.lab.nodes[node].chain.at(-1));
  const sameTip = tips.every((tip) => !!tip?.hash) &&
    new Set(tips.map((tip) => tip?.hash)).size === 1 &&
    new Set(tips.map((tip) => tip?.height)).size === 1;
  const sameLedger = sameBlock && sameTip;
  const steps: GuideStepProgress[] = [
    { complete: representatives.length > 0, detail: representatives.length
      ? `접속 중인 팀 대표 ${representatives.length}명 · 한 명이면 혼자 진행할 수 있습니다` : "혼자라면 팀 A 하나만 맡으세요 B·C 담당자는 없어도 됩니다" },
    { complete: room.automatic, detail: room.automatic ? "메시지 자동 전달 켜짐" : "메시지 자동 전달을 켜세요" },
    { complete: !!target && accepted.length > 0, detail: target && accepted.length
      ? "선택한 서명 거래가 노드에 접수됐습니다" : "확인할 거래를 선택하거나 작성·서명·검증·전파하세요" },
    { complete: !!target && accepted.length === NODE_IDS.length,
      detail: target ? `같은 거래 수신 확인 ${accepted.length} / ${NODE_IDS.length}` : "세 노드에서 같은 거래의 수신을 확인합니다" },
    { complete: !!target && confirmed.length > 0,
      detail: confirmed.length ? `선택한 거래의 블록 포함 확인 · 노드 ${confirmed.map((node) => node.node).join("·")}` : "이 거래를 포함한 유효한 블록을 찾아야 합니다" },
    { complete: !!target && sameLedger, detail: sameLedger
      ? `A·B·C 같은 거래와 최신 블록 #${tips[0]!.height} 검증 완료`
      : "세 노드가 같은 거래와 최신 블록을 검증한 뒤 완료됩니다" },
  ];
  const firstIncomplete = steps.findIndex((step) => !step.complete);
  const nextStep = firstIncomplete < 0 ? null : firstIncomplete;
  let nextAction = "세 노드에 같은 거래와 블록이 반영됐습니다 다음 실습에서는 새 거래를 확인하세요";
  let nextDestination: GuideProgress["nextDestination"] = null;
  if (nextStep === 0) {
    nextAction = "혼자라면 팀 A 맡기를 누르세요 함께라면 비어 있는 팀 하나를 맡으세요";
    nextDestination = "roles";
  } else if (nextStep === 1) {
    nextAction = "노드·블록체인에서 메시지 자동 전달을 켜세요 B·C 담당자 없이도 전달·검증됩니다";
    nextDestination = "노드·블록체인";
  } else if (nextStep === 2) {
    nextAction = selectedTxId && !target
      ? "선택한 거래가 현재 원장에 없습니다 확인할 거래를 다시 선택하세요"
      : !selectedTxId && pending
        ? "이미 대기 중인 거래가 있습니다 가이드 확인 거래에서 대기 거래를 선택해 이어가세요 예약된 코인은 거래 확정 뒤 사용할 수 있습니다"
        : "거래 화면에서 받는 팀 B를 선택하고 거래 작성 → 전자서명 → 검증·전파 순서로 누르세요 버튼은 앞 단계를 마치면 켜집니다";
    nextDestination = "거래";
  } else if (nextStep === 3) {
    nextAction = `노드 ${nodes.filter((node) => node.status === "missing").map((node) => node.node).join("·")}에 거래를 전달하세요 자동 전달이 켜져 있으면 수신을 기다리세요`;
    nextDestination = "노드·블록체인";
  } else if (nextStep === 4) {
    const race = room.lab.race;
    nextDestination = "채굴";
    const miningNodes = race?.singleNode ? [race.singleNode] : representatives.map((person) => person.team!);
    const candidates = race ? miningNodes.filter((node) => {
      const job = race.jobs[node];
      return job && (job.status === "running" || job.status === "paused") &&
        job.block.transactions.some((transaction) => target && fingerprint(transaction) === fingerprint(target.transaction));
    }) : [];
    if (!race)
      nextAction = "채굴 → 한 노드 채굴에서 노드 A·빠르게를 선택하고 선택 노드 후보 준비를 누르세요 그다음 채굴 시작 / 계속이 켜집니다";
    else if (race.status === "stopped")
      nextAction = "채굴이 중지됐습니다 선택 노드 후보 준비를 다시 누른 뒤 채굴 시작 / 계속을 누르세요";
    else if (!candidates.length)
      nextAction = race.singleNode
        ? race.jobs[race.singleNode]?.status === "found"
          ? "이전 후보의 계산이 끝났습니다 선택 노드 후보 준비를 눌러 이 거래를 새 후보에 포함하세요"
          : `노드 ${race.singleNode}의 현재 후보에 이 거래가 없습니다 선택 노드 후보 준비를 눌러 이 거래를 포함하세요`
        : "현재 경쟁 후보로 이 거래를 채굴할 담당자가 없습니다 한 노드 채굴에서 선택 노드 후보 준비를 누르거나 경쟁의 세 후보 준비를 다시 누르세요";
    else if (!race.singleNode && !candidates.some((node) => race.enabled[node]))
      nextAction = "팀 담당자가 내 채굴 참여를 켜세요";
    else if (race.status === "running")
      nextAction = "선택한 거래의 해시를 계산 중입니다 블록이 발견될 때까지 기다리세요";
    else nextAction = race.singleNode ? `노드 ${race.singleNode}의 채굴 시작 / 계속을 누르세요` : "진행자가 전체 시작 / 계속을 누르세요";
  } else if (nextStep === 5) {
    if (sameBlock && !sameTip) {
      nextAction = "노드의 최신 블록이 다릅니다 전파와 체인 선택 결과를 확인하세요";
      nextDestination = "노드·블록체인";
    } else if (confirmed.length === NODE_IDS.length && !sameBlock && !room.lab.queue.length) {
      nextAction = "노드마다 서로 다른 블록을 선택했습니다 다음 블록을 채굴해 체인 선택 결과를 확인하세요";
      nextDestination = "채굴";
    } else {
      nextAction = "발견한 블록의 전파를 기다리고 세 노드의 같은 블록 포함 표시를 확인하세요";
      nextDestination = "노드·블록체인";
    }
  }
  if (nextStep !== null && nextStep >= 2 && target && nodes.some((node) => node.status === "invalid" || node.status === "conflict")) {
    nextAction = "거래 내용이나 체인 검증이 다른 노드가 있습니다 노드·블록체인에서 확인하세요";
    nextDestination = "노드·블록체인";
  }
  if (nextStep !== null && nextStep >= 2 && room.limited && !steps[5].complete) {
    nextAction = "실습 실행 한도에 도달했습니다 저장된 거래와 결과를 확인하세요";
    nextDestination = "거래";
  }
  return { steps, nextStep, nextAction, nextDestination, target, nodes,
    completeCount: steps.filter((step) => step.complete).length };
}
