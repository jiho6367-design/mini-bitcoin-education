import {
  addressOf,
  isAddress,
  isHash,
  transactionId,
  verifySignature,
} from "./crypto";
import type {
  Check,
  Transaction,
  TxInput,
  UnsignedTransaction,
  UtxoSet,
  ValidationResult,
  Wallet,
} from "./types";

export const outpoint = (i: TxInput): string => `${i.txId}:${i.outputIndex}`;
const positive = (v: unknown): v is number =>
  Number.isSafeInteger(v) && (v as number) > 0;
const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null;
export function hasTransactionShape(v: unknown): v is Transaction {
  if (
    !record(v) ||
    v.version !== 1 ||
    !isHash(v.sessionId) ||
    !isHash(v.id) ||
    typeof v.publicKey !== "string" ||
    !/^(02|03)[0-9a-f]{64}$/.test(v.publicKey) ||
    typeof v.signature !== "string" ||
    !/^[0-9a-f]{128}$/.test(v.signature)
  )
    return false;
  return (
    Array.isArray(v.inputs) &&
    v.inputs.length > 0 &&
    v.inputs.every(
      (i) =>
        record(i) &&
        isHash(i.txId) &&
        Number.isSafeInteger(i.outputIndex) &&
        (i.outputIndex as number) >= 0,
    ) &&
    Array.isArray(v.outputs) &&
    v.outputs.length > 0 &&
    v.outputs.every(
      (o) => record(o) && isAddress(o.address) && positive(o.amount),
    )
  );
}
export function result(
  valid: boolean,
  code: string,
  message: string,
  steps: Check[] = [],
): ValidationResult {
  return { valid, code, message, steps };
}
export function validateTransaction(
  value: unknown,
  utxos: UtxoSet,
  sessionId: string,
  reserved = new Set<string>(),
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
      hasTransactionShape(value),
      "형식·금액",
      "입력·출력, 키·서명 형식과 양의 정수 금액을 확인합니다.",
    )
  )
    return fail("FORMAT");
  const tx = value as Transaction;
  if (
    !check(
      tx.sessionId === sessionId,
      "실습 식별자",
      "현재 실습에서 생성된 거래여야 합니다.",
    )
  )
    return fail("SESSION");
  if (
    !check(
      tx.id === transactionId(tx),
      "거래 ID",
      "서명된 거래를 다시 해시한 값이 거래 ID와 일치해야 합니다.",
    )
  )
    return fail("TX_ID");
  if (
    !check(
      verifySignature(tx),
      "전자서명",
      "공개키로 서명과 거래 원문의 일치 여부를 검증합니다.",
    )
  )
    return fail("SIGNATURE");
  const keys = tx.inputs.map(outpoint);
  if (
    !check(
      new Set(keys).size === keys.length,
      "중복 입력",
      "같은 출력을 한 거래에서 두 번 사용할 수 없습니다.",
    )
  )
    return fail("DUPLICATE_INPUT");
  if (
    !check(
      keys.every((k) => utxos[k] !== undefined),
      "미사용 출력",
      "존재하는 확정 UTXO만 사용할 수 있습니다. 이미 소비한 출력은 사용할 수 없습니다.",
    )
  )
    return fail("MISSING_UTXO");
  if (
    !check(
      keys.every((k) => !reserved.has(k)),
      "이중 지출",
      "다른 대기 거래 또는 같은 블록의 거래가 이미 사용하는 입력입니다.",
    )
  )
    return fail("DOUBLE_SPEND");
  let owner = "";
  try {
    owner = addressOf(tx.publicKey);
  } catch {
    /* 아래 소유권 검사에서 거부 */
  }
  if (
    !check(
      keys.every((k) => utxos[k].address === owner),
      "소유권",
      "모든 입력은 서명자의 공개키에 대응하는 주소 소유여야 합니다.",
    )
  )
    return fail("OWNER");
  const input = keys.reduce((sum, k) => sum + utxos[k].amount, 0);
  const output = tx.outputs.reduce((sum, o) => sum + o.amount, 0);
  if (
    !check(
      Number.isSafeInteger(input) &&
        Number.isSafeInteger(output) &&
        input === output,
      "금액 보존",
      "수수료가 0이므로 입력 합계와 출력 합계가 같아야 합니다.",
    )
  )
    return fail("VALUE");
  return result(
    true,
    "VALID",
    "모든 검증 통과 · Mempool에 들어갈 수 있는 거래입니다.",
    steps,
  );
}
export const reservedInputs = (mempool: Transaction[]): Set<string> =>
  new Set(mempool.flatMap((t) => t.inputs.map(outpoint)));
export function balance(
  utxos: UtxoSet,
  address: string,
  reserved = new Set<string>(),
): number {
  return Object.entries(utxos)
    .filter(([key, u]) => u.address === address && !reserved.has(key))
    .reduce((n, [, u]) => n + u.amount, 0);
}
export function buildTransaction(
  wallet: Wallet,
  recipient: string,
  amount: number,
  utxos: UtxoSet,
  mempool: Transaction[],
  sessionId: string,
): UnsignedTransaction {
  if (!isAddress(recipient))
    throw new Error("올바른 교육용 수신 주소를 선택하세요.");
  if (!positive(amount))
    throw new Error("금액은 1 이상의 안전한 정수여야 합니다.");
  const reserved = reservedInputs(mempool);
  const available = Object.entries(utxos)
    .filter(([key, u]) => u.address === wallet.address && !reserved.has(key))
    .sort(([a], [b]) => a.localeCompare(b));
  const inputs: TxInput[] = [];
  let total = 0;
  for (const [, u] of available) {
    inputs.push({ txId: u.txId, outputIndex: u.outputIndex });
    total += u.amount;
    if (total >= amount) break;
  }
  if (total < amount)
    throw new Error(
      `송금 가능 잔액 부족: ${total} 코인입니다. 대기 거래가 예약한 UTXO와 미확정 거스름돈은 사용할 수 없습니다.`,
    );
  const outputs = [{ address: recipient, amount }];
  if (total > amount)
    outputs.push({ address: wallet.address, amount: total - amount });
  return {
    version: 1,
    sessionId,
    inputs,
    outputs,
    publicKey: wallet.publicKey,
  };
}
export function applyTransaction(tx: Transaction, utxos: UtxoSet): void {
  tx.inputs.forEach((i) => {
    delete utxos[outpoint(i)];
  });
  tx.outputs.forEach((o, outputIndex) => {
    utxos[`${tx.id}:${outputIndex}`] = { ...o, txId: tx.id, outputIndex };
  });
}
