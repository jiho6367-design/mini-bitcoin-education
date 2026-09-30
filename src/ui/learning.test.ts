import { describe, expect, it } from "vitest";
import { calculateNonce, createCandidate, validPow } from "../core/block";
import { createWallet, hashText, signTransaction } from "../core/crypto";
import { Lab } from "../core/lab";
import { buildTransaction } from "../core/transaction";
import { learningFlow, type TransactionLesson } from "./learning";

const alice = createWallet("Alice", "1".padStart(64, "0"));
const bob = createWallet("Bob", "2".padStart(64, "0"));
const setup = () => new Lab([alice, bob], 1, 1000, hashText("learning-flow"));
function draft(lab: Lab, amount = 30): TransactionLesson {
  const tx = buildTransaction(
    alice,
    bob.address,
    amount,
    lab.nodes.A.utxos,
    lab.nodes.A.mempool,
    lab.sessionId,
  );
  return {
    origin: "A",
    phase: "draft",
    draft: tx,
    inputs: tx.inputs.map((i) =>
      structuredClone(lab.nodes.A.utxos[`${i.txId}:${i.outputIndex}`]),
    ),
  };
}
function approved(lab: Lab): TransactionLesson {
  const lesson = draft(lab);
  const signed = signTransaction(lesson.draft, alice.privateKey);
  return {
    ...lesson,
    phase: "validated",
    signed,
    checked: signed,
    validation: lab.submit("A", signed),
  };
}
function accept(lab: Lab) {
  const candidate = createCandidate(
    lab.nodes.A.chain[0],
    lab.nodes.A.mempool,
    1001,
  );
  for (let n = 0; n < 1_000_000; n++) {
    const block = calculateNonce(candidate, n);
    if (validPow(block.hash, 1)) {
      expect(lab.acceptMined("A", block).valid).toBe(true);
      return block;
    }
  }
  throw new Error("고정 후보 해답 탐색 한도 초과");
}
const idle = { status: "idle" };
describe("선택 거래의 학습 흐름", () => {
  it("작성·서명·검증 관찰·Mempool을 구분하고 실패 검증은 완료 처리하지 않는다", () => {
    const lab = setup();
    const lesson = draft(lab);
    expect(learningFlow(lab, lesson, idle).current).toBe(1);
    const signed = signTransaction(lesson.draft, alice.privateKey);
    expect(
      learningFlow(lab, { ...lesson, phase: "signed", signed }, idle).current,
    ).toBe(2);
    const validation = lab.submit("A", signed);
    const validated: TransactionLesson = {
      ...lesson,
      signed,
      phase: "validated",
      validation,
    };
    expect(learningFlow(lab, validated, idle).current).toBe(2);
    expect(lab.nodes.A.mempool).toHaveLength(1); // 표시 단계는 실제 등록을 지연하지 않는다.
    expect(
      learningFlow(lab, { ...validated, phase: "mempool" }, idle).current,
    ).toBe(3);
    const wrong = signTransaction(lesson.draft, bob.privateKey);
    const failed = learningFlow(
      lab,
      { ...validated, validation: lab.submit("A", wrong) },
      idle,
    );
    expect(failed.failed).toBe(true);
    expect(failed.current).toBe(2);
    expect(failed.completed).not.toContain(2);
  });
  it("선택 거래가 포함된 채굴 후보와 실제 노드 블록 수락만으로 진행한다", () => {
    const lab = setup();
    const lesson = { ...approved(lab), phase: "mempool" as const };
    const candidate = createCandidate(
      lab.nodes.A.chain[0],
      lab.nodes.A.mempool,
      1001,
    );
    expect(
      learningFlow(lab, lesson, { status: "paused", block: candidate }).current,
    ).toBe(4);
    expect(
      learningFlow(lab, lesson, {
        status: "running",
        block: { ...candidate, transactions: [] },
      }).current,
    ).toBe(3);
    const block = accept(lab);
    expect(learningFlow(lab, lesson, { status: "found", block }).current).toBe(
      5,
    );
    while (lab.queue.length > 1) lab.deliverNext();
    expect(learningFlow(lab, lesson, idle).current).toBe(5);
    lab.deliverNext();
    expect(learningFlow(lab, lesson, idle).current).toBe(6);
  });
  it("두 번째 거래와 새 작성에서는 기존 체인 높이를 완료로 오해하지 않는다", () => {
    const lab = setup();
    const first = approved(lab);
    accept(lab);
    while (lab.queue.length) lab.deliverNext();
    expect(learningFlow(lab, first, idle).current).toBe(6);
    expect(learningFlow(lab, null, idle).current).toBe(0);
    expect(learningFlow(lab, draft(lab, 10), idle).current).toBe(1);
    expect(learningFlow(null, first, idle).current).toBe(0);
  });
  it("다른 노드의 검증 결과가 현재 거래의 실패 판정을 덮어쓰지 않는다", () => {
    const lab = setup();
    const lesson = approved(lab);
    const wrong = signTransaction(lesson.draft, bob.privateKey);
    const failed: TransactionLesson = {
      ...lesson,
      checked: wrong,
      validation: lab.submit("A", wrong),
    };
    lab.deliverNext();
    expect(lab.lastValidation?.valid).toBe(true);
    expect(learningFlow(lab, failed, idle).failed).toBe(true);
  });
});
