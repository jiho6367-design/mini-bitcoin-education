import { describe, expect, it } from "vitest";
import {
  blockHash,
  calculateNonce,
  createCandidate,
  targetHex,
  transactionDigest,
  validateBlock,
  validateChain,
  validPow,
} from "./block";
import {
  createWallet,
  hashText,
  signTransaction,
  signingText,
  transactionId,
  verifySignature,
} from "./crypto";
import { Lab } from "./lab";
import {
  balance,
  buildTransaction,
  reservedInputs,
  validateTransaction,
} from "./transaction";
import type { Block, Transaction } from "./types";

const alice = createWallet("Alice", "1".padStart(64, "0"));
const bob = createWallet("Bob", "2".padStart(64, "0"));
const carol = createWallet("Carol", "3".padStart(64, "0"));
const setup = () =>
  new Lab([alice, bob, carol], 1, 1000, hashText("test-session"));
const transfer = (lab: Lab, amount = 30) =>
  signTransaction(
    buildTransaction(
      alice,
      bob.address,
      amount,
      lab.nodes.A.utxos,
      [],
      lab.sessionId,
    ),
    alice.privateKey,
  );
function mined(candidate: Block): Block {
  for (let n = 0; n < 1_000_000; n++) {
    const b = calculateNonce(candidate, n);
    if (validPow(b.hash, b.difficulty)) return b;
  }
  throw new Error("고정 테스트 벡터의 Nonce 탐색 한도를 초과했습니다.");
}
function resign(tx: Transaction): Transaction {
  return signTransaction(tx, alice.privateKey);
}

describe("키·서명·직렬화", () => {
  it("고정 키와 원문을 재현하고 정상 서명을 검증한다", () => {
    const lab = setup();
    const tx = transfer(lab);
    expect(alice.address).toMatch(/^mini_[a-f0-9]{64}$/);
    expect(hashText(signingText(tx))).toBe(hashText(signingText({ ...tx })));
    expect(verifySignature(tx)).toBe(true);
    expect(transactionId(tx)).toBe(tx.id);
    expect(signingText(tx)).not.toContain(alice.privateKey);
  });
  it("다른 개인키, 변조 원문, 변조 거래 ID를 거부한다", () => {
    const lab = setup();
    const tx = transfer(lab);
    const wrong = signTransaction(tx, bob.privateKey);
    expect(
      validateTransaction(wrong, lab.nodes.A.utxos, lab.sessionId).code,
    ).toBe("SIGNATURE");
    tx.outputs[0].amount++;
    expect(verifySignature(tx)).toBe(false);
    expect(validateTransaction(tx, lab.nodes.A.utxos, lab.sessionId).code).toBe(
      "TX_ID",
    );
  });
});
describe("거래·금액 보존", () => {
  it("100 입력을 30 수신·70 거스름돈으로 만든다", () => {
    const lab = setup();
    const tx = transfer(lab);
    expect(tx.outputs.map((o) => o.amount)).toEqual([30, 70]);
    expect(
      validateTransaction(tx, lab.nodes.A.utxos, lab.sessionId).valid,
    ).toBe(true);
    expect(transfer(lab, 100).outputs).toHaveLength(1);
  });
  it.each([0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, 101])(
    "송금 불가능 금액 %s를 차단한다",
    (amount) => {
      const lab = setup();
      expect(() => transfer(lab, amount)).toThrow();
    },
  );
  it("형식·다른 실습·입력 누락·중복·소유권·합계를 독립 검사한다", () => {
    const lab = setup();
    const base = transfer(lab);
    const check = (tx: unknown) =>
      validateTransaction(tx, lab.nodes.A.utxos, lab.sessionId).code;
    expect(check(null)).toBe("FORMAT");
    expect(
      check({ ...base, outputs: [{ address: bob.address, amount: -1 }] }),
    ).toBe("FORMAT");
    expect(check(resign({ ...base, sessionId: hashText("elsewhere") }))).toBe(
      "SESSION",
    );
    expect(
      check(
        resign({
          ...base,
          inputs: [{ txId: hashText("unknown"), outputIndex: 0 }],
        }),
      ),
    ).toBe("MISSING_UTXO");
    expect(
      check(resign({ ...base, inputs: [...base.inputs, ...base.inputs] })),
    ).toBe("DUPLICATE_INPUT");
    expect(
      check(
        signTransaction({ ...base, publicKey: bob.publicKey }, bob.privateKey),
      ),
    ).toBe("OWNER");
    expect(
      check(
        resign({ ...base, outputs: [{ address: bob.address, amount: 101 }] }),
      ),
    ).toBe("VALUE");
    expect(
      check(
        resign({
          ...base,
          outputs: [
            { address: bob.address, amount: Number.MAX_SAFE_INTEGER },
            { address: bob.address, amount: 1 },
          ],
        }),
      ),
    ).toBe("VALUE");
  });
  it("Mempool은 확정 잔액을 변경하지 않고 입력을 예약한다", () => {
    const lab = setup();
    const tx = transfer(lab);
    lab.submit("A", tx);
    expect(balance(lab.nodes.A.utxos, alice.address)).toBe(100);
    expect(
      balance(
        lab.nodes.A.utxos,
        alice.address,
        reservedInputs(lab.nodes.A.mempool),
      ),
    ).toBe(0);
    expect(lab.submit("A", tx).code).toBe("DUPLICATE");
    expect(lab.submit("A", transfer(lab, 20)).code).toBe("DOUBLE_SPEND");
    expect(lab.nodes.A.mempool).toHaveLength(1);
  });
});
describe("작업증명·블록·체인", () => {
  it("단계 Nonce가 실제 해시와 일치하고 난이도 목표값이 대응한다", () => {
    const lab = setup();
    const candidate = createCandidate(lab.genesis, [transfer(lab)], 1001);
    expect(calculateNonce(candidate, 0).hash).toBe(blockHash(candidate));
    expect(calculateNonce(candidate, 1).hash).not.toBe(
      calculateNonce(candidate, 0).hash,
    );
    expect(targetHex(2)).toBe("00" + "f".repeat(62));
    expect(validPow("0".repeat(64), 0)).toBe(false);
    expect(() => calculateNonce(candidate, -1)).toThrow();
  });
  it("PoW, 연결, 난이도, 거래 해시, 헤더, 시각을 검증한다", () => {
    const lab = setup();
    const b = mined(createCandidate(lab.genesis, [transfer(lab)], 1001));
    const check = (v: Block) =>
      validateBlock(v, lab.genesis, lab.nodes.A.utxos).code;
    expect(check(b)).toBe("VALID");
    expect(check({ ...b, previousHash: hashText("bad") })).toBe("LINK");
    expect(check({ ...b, difficulty: 2 })).toBe("DIFFICULTY");
    expect(check({ ...b, transactionsHash: hashText("bad") })).toBe(
      "TRANSACTIONS_HASH",
    );
    expect(check({ ...b, hash: hashText("bad") })).toBe("BLOCK_HASH");
    expect(check({ ...b, timestamp: 999 })).toBe("TIME");
    expect(check({ ...b, allocations: [] })).toBe("BLOCK_FORMAT");
    let invalid = calculateNonce(b, 0);
    while (validPow(invalid.hash, 1))
      invalid = calculateNonce(b, invalid.nonce + 1);
    expect(check(invalid)).toBe("POW");
  });
  it("블록의 뒤쪽 거래가 실패하면 앞쪽 거래도 반영하지 않는다", () => {
    const lab = setup();
    const tx = transfer(lab);
    const b = mined(
      createCandidate(lab.genesis, [tx, transfer(lab, 20)], 1001),
    );
    const before = structuredClone(lab.nodes.A.utxos);
    expect(lab.receiveBlock("A", b).code).toBe("TX_DOUBLE_SPEND");
    expect(lab.nodes.A.utxos).toEqual(before);
    expect(lab.nodes.A.chain).toHaveLength(1);
  });
  it("Mempool에 없어도 블록을 검증하고 충돌 대기 거래를 정리한다", () => {
    const lab = setup();
    const tx = transfer(lab);
    lab.receiveTransaction("B", transfer(lab, 20));
    const block = mined(createCandidate(lab.genesis, [tx], 1001));
    expect(lab.receiveBlock("B", block).valid).toBe(true);
    expect(lab.receiveBlock("C", block).valid).toBe(true);
    expect(lab.nodes.B.mempool).toHaveLength(0);
    expect(balance(lab.nodes.C.utxos, bob.address)).toBe(30);
    expect(lab.receiveTransaction("C", tx).code).toBe("MISSING_UTXO");
  });
  it("신뢰 Genesis 및 과거 거래 변조를 탐지한다", () => {
    const lab = setup();
    lab.receiveBlock(
      "A",
      mined(createCandidate(lab.genesis, [transfer(lab)], 1001)),
    );
    const chain = structuredClone(lab.nodes.A.chain);
    expect(validateChain(chain, lab.genesis).valid).toBe(true);
    chain[1].transactions[0].outputs[0].amount++;
    expect(validateChain(chain, lab.genesis).code).toBe("TRANSACTIONS_HASH");
    chain[1].transactionsHash = transactionDigest(chain[1].transactions);
    chain[1] = mined(chain[1]);
    expect(validateChain(chain, lab.genesis).code).toBe("TX_TX_ID");
    chain[0].allocations![0].amount = 999;
    expect(validateChain(chain, lab.genesis).code).toBe("GENESIS");
    expect(balance(lab.nodes.A.utxos, alice.address)).toBe(70);
  });
});
describe("독립 노드와 메시지", () => {
  it("다른 노드의 수신이 현재 노드의 실패 설명을 덮어쓰지 않는다", () => {
    const lab = setup();
    lab.submit("A", transfer(lab));
    const wrong = signTransaction(transfer(lab), bob.privateKey);
    lab.submit("A", wrong);
    lab.deliverNext();
    lab.deliverNext();
    expect(lab.nodes.A.lastValidation?.code).toBe("SIGNATURE");
    expect(lab.nodes.B.lastValidation?.code).toBe("VALID");
  });
  it("전파 전 상태는 독립적이고 전달 후 70/30과 체인이 일치한다", () => {
    const lab = setup();
    const tx = transfer(lab);
    lab.submit("A", tx);
    expect(lab.nodes.B.mempool).toHaveLength(0);
    expect(lab.queue).toHaveLength(2);
    tx.outputs[0].amount = 999;
    expect(lab.nodes.A.mempool[0].outputs[0].amount).toBe(30);
    lab.deliverNext();
    lab.deliverNext();
    expect(lab.nodes.B.mempool).toHaveLength(1);
    const block = mined(lab.candidate("A"));
    expect(lab.acceptMined("A", block).valid).toBe(true);
    expect(lab.nodes.B.chain).toHaveLength(1);
    expect(() => lab.candidate("B")).toThrow("전달");
    lab.deliverNext();
    lab.deliverNext();
    for (const node of Object.values(lab.nodes)) {
      expect(node.chain.at(-1)?.hash).toBe(block.hash);
      expect(balance(node.utxos, alice.address)).toBe(70);
      expect(balance(node.utxos, bob.address)).toBe(30);
      expect(node.mempool).toHaveLength(0);
      expect(validateChain(node.chain, lab.genesis).valid).toBe(true);
    }
    expect(lab.receiveBlock("B", block).code).toBe("DUPLICATE");
    const bad = structuredClone(block);
    bad.transactions[0].outputs[0].amount++;
    expect(lab.receiveBlock("B", bad).valid).toBe(false);
    expect(JSON.stringify(lab.queue)).not.toContain(alice.privateKey);
    expect(JSON.stringify(lab.events)).not.toContain(alice.privateKey);
  });
});
