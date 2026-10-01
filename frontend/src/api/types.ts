// 백엔드 API가 주고받는 JSON의 형태. 규칙 계산은 모두 백엔드가 수행한다.
export interface Wallet {
  name: string;
  publicKey: string;
  address: string;
}
export interface TxInput {
  txId: string;
  outputIndex: number;
}
export interface TxOutput {
  address: string;
  amount: number;
}
export interface UnsignedTransaction {
  version: 1;
  sessionId: string;
  inputs: TxInput[];
  outputs: TxOutput[];
  publicKey: string;
}
export interface Transaction extends UnsignedTransaction {
  signature: string;
  id: string;
}
export interface UTXO extends TxInput, TxOutput {}
export type UtxoSet = Record<string, UTXO>;
export interface Block {
  sessionId: string;
  height: number;
  previousHash: string;
  transactionsHash: string;
  timestamp: number;
  difficulty: number;
  nonce: number;
  transactions: Transaction[];
  hash: string;
  allocations?: TxOutput[];
}
export interface Check {
  label: string;
  ok: boolean;
  message: string;
}
export interface ValidationResult {
  valid: boolean;
  code: string;
  message: string;
  steps: Check[];
}
export type NodeId = "A" | "B" | "C";
export const NODE_IDS: NodeId[] = ["A", "B", "C"];
export interface NetworkMessage {
  id: number;
  from: NodeId;
  to: NodeId;
  kind: "transaction" | "block";
}
export interface Delivery {
  message: NetworkMessage;
  validation: ValidationResult;
}
export interface LabEvent {
  id: number;
  node: NodeId | "LAB";
  kind: string;
  message: string;
  ok: boolean;
}
export interface Balance {
  confirmed: number;
  spendable: number;
}
export interface NodeSnapshot {
  id: NodeId;
  chain: Block[];
  utxos: UtxoSet;
  mempool: Transaction[];
  lastValidation: ValidationResult | null;
  // 아래 값은 백엔드가 노드의 현재 상태에서 계산해 함께 보낸다.
  chainValidation: ValidationResult;
  reserved: string[];
  balances: Record<string, Balance>;
  canStartMining: boolean;
}

export type MiningMode = "fast" | "learning";
export type MiningCommand = "tick" | "pause" | "resume" | "step" | "stop";
export interface MiningSample {
  nonce: number;
  hash: string;
  passed: boolean;
}
export interface MiningSnapshot {
  jobId: string;
  seq: number;
  status: "running" | "paused" | "found" | "stopped" | "error";
  node: NodeId;
  mode: MiningMode;
  attempts: number;
  elapsedMs: number;
  observationMs: number;
  samples: MiningSample[];
  block: Block;
  message: string | null;
  // 다음 계산 요청까지 기다릴 시간. 계산이 이어지지 않으면 null.
  nextTickInMs: number | null;
}

export interface SetupSnapshot {
  labId: string;
  phase: "setup";
  revision: number;
  wallets: Wallet[];
}
export interface LabSnapshot {
  labId: string;
  phase: "running";
  revision: number;
  wallets: Wallet[];
  sessionId: string;
  genesis: Block;
  difficulty: number;
  targetHex: string;
  nodes: Record<NodeId, NodeSnapshot>;
  queue: NetworkMessage[];
  events: LabEvent[];
  lastValidation: ValidationResult | null;
  lastDelivery: Delivery | null;
  mining: MiningSnapshot | null;
  limits: { miningSampleLimit: number; learningIntervalMs: number };
}
export type AnySnapshot = SetupSnapshot | LabSnapshot;

export interface DraftResult {
  draft: UnsignedTransaction;
  inputs: UTXO[];
  signingText: string;
  signingHash: string;
}
export interface SubmitResult {
  validation: ValidationResult;
  lab: LabSnapshot;
}
export interface ForgedSignatureResult extends SubmitResult {
  checked: Transaction;
}
export interface DoubleSpendResult extends SubmitResult {
  conflict: Transaction;
  inputs: UTXO[];
  signingText: string;
  signingHash: string;
}
export interface MiningResult {
  mining: MiningSnapshot;
  // 채굴 성공으로 체인이 바뀐 경우에만 새 실습 상태가 함께 온다.
  lab: LabSnapshot | null;
}
export type TamperMode = "transaction" | "header";
export interface TamperRequest {
  height: number;
  mode: TamperMode;
  amount?: number;
  recomputeHashes: boolean;
}
export interface TamperReport {
  altered: Block;
  recomputedHash: string;
  checks: { label: string; ok: boolean }[];
  chainValidation: ValidationResult;
  hasNextBlock: boolean;
}
