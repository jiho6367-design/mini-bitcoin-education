export interface Wallet {
  name: string;
  privateKey: string;
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
export interface NodeState {
  id: NodeId;
  chain: Block[];
  utxos: UtxoSet;
  mempool: Transaction[];
  seen: Set<string>;
  lastValidation: ValidationResult | null;
}
export interface NetworkMessage {
  id: number;
  from: NodeId;
  to: NodeId;
  kind: "transaction" | "block";
  payload: Transaction | Block;
}
export interface LabEvent {
  id: number;
  node: NodeId | "LAB";
  kind: string;
  message: string;
  ok: boolean;
}
