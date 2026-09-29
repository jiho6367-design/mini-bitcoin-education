import { hashText, isHash } from "./crypto";
import {
  applyTransaction,
  hasTransactionShape,
  outpoint,
  result,
  validateTransaction,
} from "./transaction";
import type {
  Block,
  Check,
  Transaction,
  TxOutput,
  UtxoSet,
  ValidationResult,
} from "./types";

export const transactionDigest = (transactions: Transaction[]): string =>
  hashText(
    JSON.stringify(
      transactions.map((t) => [
        t.id,
        t.version,
        t.sessionId,
        t.inputs.map((i) => [i.txId, i.outputIndex]),
        t.outputs.map((o) => [o.address, o.amount]),
        t.publicKey,
        t.signature,
      ]),
    ),
  );
export const headerText = (b: Block): string =>
  JSON.stringify([
    b.sessionId,
    b.height,
    b.previousHash,
    b.transactionsHash,
    b.timestamp,
    b.difficulty,
    b.nonce,
  ]);
export const blockHash = (b: Block): string => hashText(headerText(b));
export const validDifficulty = (d: number): boolean =>
  Number.isInteger(d) && d >= 1 && d <= 4;
export const targetHex = (d: number): string => {
  if (!validDifficulty(d)) throw new Error("난이도 범위는 1~4입니다.");
  return "0".repeat(d) + "f".repeat(64 - d);
};
export const validPow = (hash: string, difficulty: number): boolean =>
  validDifficulty(difficulty) && isHash(hash) && hash <= targetHex(difficulty);

export function createGenesis(
  sessionId: string,
  address: string,
  difficulty: number,
  timestamp: number,
): Block {
  if (!validDifficulty(difficulty)) throw new Error("난이도 범위는 1~4입니다.");
  const allocations: TxOutput[] = [{ address, amount: 100 }];
  const genesis: Block = {
    sessionId,
    height: 0,
    previousHash: "0".repeat(64),
    transactionsHash: hashText(JSON.stringify(allocations)),
    timestamp,
    difficulty,
    nonce: 0,
    transactions: [],
    allocations,
    hash: "",
  };
  genesis.hash = blockHash(genesis);
  return genesis;
}
export function genesisUtxos(genesis: Block): UtxoSet {
  const utxos: UtxoSet = {};
  genesis.allocations?.forEach((o, outputIndex) => {
    utxos[`${genesis.hash}:${outputIndex}`] = {
      ...o,
      txId: genesis.hash,
      outputIndex,
    };
  });
  return utxos;
}
export function createCandidate(
  tip: Block,
  transactions: Transaction[],
  timestamp = Date.now(),
): Block {
  if (transactions.length === 0)
    throw new Error("채굴할 유효 거래가 Mempool에 없습니다.");
  return {
    sessionId: tip.sessionId,
    height: tip.height + 1,
    previousHash: tip.hash,
    transactionsHash: transactionDigest(transactions),
    timestamp: Math.max(tip.timestamp, timestamp),
    difficulty: tip.difficulty,
    nonce: 0,
    transactions: structuredClone(transactions),
    hash: "",
  };
}
export function calculateNonce(candidate: Block, nonce: number): Block {
  if (!Number.isSafeInteger(nonce) || nonce < 0)
    throw new Error("Nonce 범위를 초과했습니다. 새 후보를 만드세요.");
  const block = { ...candidate, nonce };
  block.hash = blockHash(block);
  return block;
}
function hasBlockShape(value: unknown): value is Block {
  if (typeof value !== "object" || value === null) return false;
  const b = value as Block;
  return (
    isHash(b.sessionId) &&
    Number.isSafeInteger(b.height) &&
    b.height > 0 &&
    isHash(b.previousHash) &&
    isHash(b.transactionsHash) &&
    Number.isSafeInteger(b.timestamp) &&
    b.timestamp >= 0 &&
    validDifficulty(b.difficulty) &&
    Number.isSafeInteger(b.nonce) &&
    b.nonce >= 0 &&
    isHash(b.hash) &&
    b.allocations === undefined &&
    Array.isArray(b.transactions) &&
    b.transactions.length > 0 &&
    b.transactions.every(hasTransactionShape)
  );
}
export function validateBlock(
  value: unknown,
  previous: Block,
  utxos: UtxoSet,
): ValidationResult {
  const steps: Check[] = [];
  const check = (ok: boolean, label: string, message: string) => {
    steps.push({ ok, label, message });
    return ok;
  };
  const fail = (code: string) =>
    result(false, code, steps[steps.length - 1].message, steps);
  if (
    !check(
      hasBlockShape(value),
      "블록 형식",
      "일반 블록에는 올바른 헤더와 서명된 거래가 필요하며 초기 배정을 넣을 수 없습니다.",
    )
  )
    return fail("BLOCK_FORMAT");
  const b = value as Block;
  if (
    !check(
      b.sessionId === previous.sessionId &&
        b.height === previous.height + 1 &&
        b.previousHash === previous.hash,
      "이전 블록 연결",
      "실습 식별자·높이·Previous Hash가 현재 체인과 일치해야 합니다.",
    )
  )
    return fail("LINK");
  if (
    !check(
      b.timestamp >= previous.timestamp,
      "생성 시각",
      "이전 블록보다 이른 시각일 수 없습니다.",
    )
  )
    return fail("TIME");
  if (
    !check(
      b.difficulty === previous.difficulty,
      "공통 난이도",
      "실습 시작 때 정한 난이도를 사용해야 합니다.",
    )
  )
    return fail("DIFFICULTY");
  if (
    !check(
      b.transactionsHash === transactionDigest(b.transactions),
      "거래 목록 해시",
      "블록에 담긴 실제 거래 목록과 헤더의 해시가 일치해야 합니다.",
    )
  )
    return fail("TRANSACTIONS_HASH");
  if (
    !check(
      b.hash === blockHash(b),
      "블록 해시",
      "헤더를 다시 계산한 해시가 저장된 해시와 일치해야 합니다.",
    )
  )
    return fail("BLOCK_HASH");
  if (
    !check(
      validPow(b.hash, b.difficulty),
      "작업증명",
      "해시가 실습의 목표값 이하여야 합니다.",
    )
  )
    return fail("POW");
  // 같은 블록 안에서 아직 확정되지 않은 출력을 소비하지 못하게 원래 UTXO로 검증한다.
  const used = new Set<string>();
  for (const tx of b.transactions) {
    const validation = validateTransaction(tx, utxos, b.sessionId, used);
    if (!validation.valid)
      return result(
        false,
        `TX_${validation.code}`,
        `블록 내 거래 거부: ${validation.message}`,
        [...steps, ...validation.steps],
      );
    tx.inputs.forEach((i) => used.add(outpoint(i)));
  }
  steps.push({
    ok: true,
    label: "모든 거래",
    message: "서명·소유권·금액 보존·이중 지출 검증을 모두 통과했습니다.",
  });
  return result(
    true,
    "VALID",
    "블록 검증 통과 · 체인에 추가할 수 있습니다.",
    steps,
  );
}
export function validateChain(
  chain: Block[],
  trustedGenesis: Block,
): ValidationResult {
  if (
    !chain.length ||
    JSON.stringify(chain[0]) !== JSON.stringify(trustedGenesis)
  )
    return result(false, "GENESIS", "실습 시작 시 공유한 Genesis와 다릅니다.");
  const utxos = genesisUtxos(trustedGenesis);
  for (let i = 1; i < chain.length; i++) {
    const v = validateBlock(chain[i], chain[i - 1], utxos);
    if (!v.valid)
      return { ...v, message: `블록 #${i} 검증 실패: ${v.message}` };
    chain[i].transactions.forEach((tx) => applyTransaction(tx, utxos));
  }
  return result(
    true,
    "VALID",
    `Genesis부터 블록 #${chain.length - 1}까지 검증 통과`,
  );
}
