import {
  createCandidate,
  createGenesis,
  genesisUtxos,
  validateBlock,
} from "./block";
import { hashText } from "./crypto";
import {
  applyTransaction,
  reservedInputs,
  result,
  validateTransaction,
} from "./transaction";
import {
  NODE_IDS,
  type Block,
  type LabEvent,
  type NetworkMessage,
  type NodeId,
  type NodeState,
  type Transaction,
  type ValidationResult,
  type Wallet,
} from "./types";

export class Lab {
  readonly sessionId: string;
  readonly genesis: Block;
  readonly nodes: Record<NodeId, NodeState>;
  readonly wallets: Wallet[];
  queue: NetworkMessage[] = [];
  events: LabEvent[] = [];
  lastValidation: ValidationResult | null = null;
  lastDelivery: {
    message: NetworkMessage;
    validation: ValidationResult;
  } | null = null;
  private sequence = 0;

  constructor(
    wallets: Wallet[],
    difficulty = 2,
    timestamp = Date.now(),
    sessionId = hashText(crypto.randomUUID()),
  ) {
    if (wallets.length < 2)
      throw new Error("실습을 시작하려면 지갑 두 개가 필요합니다.");
    this.sessionId = sessionId;
    this.wallets = structuredClone(wallets);
    this.genesis = createGenesis(
      sessionId,
      wallets[0].address,
      difficulty,
      timestamp,
    );
    const makeNode = (id: NodeId): NodeState => ({
      id,
      chain: [structuredClone(this.genesis)],
      utxos: genesisUtxos(this.genesis),
      mempool: [],
      seen: new Set<string>(),
      lastValidation: null,
    });
    this.nodes = { A: makeNode("A"), B: makeNode("B"), C: makeNode("C") };
    this.log(
      "LAB",
      "초기화",
      "독립 노드 A·B·C 생성. 동일 Genesis에서 Alice에게 학습용 100 코인을 배정했습니다.",
      true,
    );
  }
  log(node: NodeId | "LAB", kind: string, message: string, ok: boolean): void {
    this.events.push({ id: ++this.sequence, node, kind, message, ok });
    if (this.events.length > 200) this.events.shift();
  }
  private broadcast(
    from: NodeId,
    kind: NetworkMessage["kind"],
    payload: Transaction | Block,
  ): void {
    NODE_IDS.filter((to) => to !== from).forEach((to) =>
      this.queue.push({
        id: ++this.sequence,
        from,
        to,
        kind,
        payload: structuredClone(payload),
      }),
    );
    this.log(
      from,
      "전파 대기",
      `${kind === "transaction" ? "거래" : "블록"} 사본을 다른 두 노드로 보냅니다.`,
      true,
    );
  }
  receiveTransaction(nodeId: NodeId, tx: Transaction): ValidationResult {
    const node = this.nodes[nodeId];
    const fingerprint = `tx:${hashText(JSON.stringify(tx))}`;
    if (node.seen.has(fingerprint))
      return this.finish(
        nodeId,
        result(
          true,
          "DUPLICATE",
          "이미 처리한 동일 거래입니다. 중복 저장하지 않습니다.",
        ),
      );
    const v = validateTransaction(
      tx,
      node.utxos,
      this.sessionId,
      reservedInputs(node.mempool),
    );
    if (v.valid) {
      node.mempool.push(structuredClone(tx));
      node.seen.add(fingerprint);
    }
    return this.finish(nodeId, v);
  }
  submit(nodeId: NodeId, tx: Transaction): ValidationResult {
    const v = this.receiveTransaction(nodeId, tx);
    if (v.valid && v.code !== "DUPLICATE")
      this.broadcast(nodeId, "transaction", tx);
    return v;
  }
  receiveBlock(nodeId: NodeId, block: Block): ValidationResult {
    const node = this.nodes[nodeId];
    const fingerprint = `block:${hashText(JSON.stringify(block))}`;
    if (node.seen.has(fingerprint))
      return this.finish(
        nodeId,
        result(
          true,
          "DUPLICATE",
          "이미 처리한 동일 블록입니다. 중복 저장하지 않습니다.",
        ),
      );
    const v = validateBlock(
      block,
      node.chain[node.chain.length - 1],
      node.utxos,
    );
    if (v.valid) {
      const next = structuredClone(node.utxos);
      block.transactions.forEach((tx) => applyTransaction(tx, next));
      node.chain.push(structuredClone(block));
      node.utxos = next;
      node.seen.add(fingerprint);
      const remaining: Transaction[] = [];
      for (const tx of node.mempool) {
        if (
          validateTransaction(
            tx,
            next,
            this.sessionId,
            reservedInputs(remaining),
          ).valid
        )
          remaining.push(tx);
      }
      node.mempool = remaining;
    }
    return this.finish(nodeId, v);
  }
  private finish(node: NodeId, validation: ValidationResult): ValidationResult {
    this.lastValidation = validation;
    this.nodes[node].lastValidation = validation;
    this.log(
      node,
      validation.valid ? "검증 승인" : "검증 거부",
      validation.message,
      validation.valid,
    );
    return validation;
  }
  deliverNext(): ValidationResult | null {
    const message = this.queue.shift();
    if (!message) return null;
    this.log(
      message.to,
      "수신",
      `${message.from} → ${message.to} · ${message.kind === "transaction" ? "거래" : "블록"} 수신 후 독립 검증`,
      true,
    );
    const validation =
      message.kind === "transaction"
        ? this.receiveTransaction(message.to, message.payload as Transaction)
        : this.receiveBlock(message.to, message.payload as Block);
    this.lastDelivery = structuredClone({ message, validation });
    return validation;
  }
  candidate(nodeId: NodeId): Block {
    if (this.queue.some((m) => m.kind === "block"))
      throw new Error("이전 블록의 전달을 먼저 완료하세요.");
    const hashes = NODE_IDS.map((id) => this.nodes[id].chain.at(-1)?.hash);
    if (new Set(hashes).size !== 1)
      throw new Error(
        "노드의 체인이 일치한 뒤 다음 채굴을 시작할 수 있습니다.",
      );
    const node = this.nodes[nodeId];
    return createCandidate(node.chain[node.chain.length - 1], node.mempool);
  }
  acceptMined(nodeId: NodeId, block: Block): ValidationResult {
    const v = this.receiveBlock(nodeId, block);
    if (v.valid && v.code !== "DUPLICATE")
      this.broadcast(nodeId, "block", block);
    return v;
  }
}
