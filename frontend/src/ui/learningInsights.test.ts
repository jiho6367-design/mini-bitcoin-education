import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import scenarios from "../test-fixtures/lab-scenarios.json";
import type {
  DraftResult,
  LabSnapshot,
  Transaction,
  ValidationResult,
} from "../api/types";
import type { TransactionLesson } from "./learning";
import {
  allIncluded,
  lessonTotals,
  transactionPresence,
} from "./learningInsights";
import { TransactionTrace } from "./TransactionTrace";
import { LearningGuide } from "./LearningGuide";

const data = scenarios.learning as unknown as {
  draft: DraftResult;
  signed: Transaction;
  validation: ValidationResult;
  wrong: Transaction;
  wrongValidation: ValidationResult;
  submitted: LabSnapshot;
  minedAtA: LabSnapshot;
  delivered: LabSnapshot;
  initial: LabSnapshot;
  secondDraft: DraftResult;
  block: import("../api/types").Block;
};
const lesson: TransactionLesson = {
  ...data.draft,
  origin: "A",
  phase: "validated",
  signed: data.signed,
  validation: data.validation,
};
describe("실제 거래 기준 학습 안내", () => {
  it("일부 노드 포함 뒤에는 전파를 안내하고 이전 채굴 성공을 새 거래에 적용하지 않는다", () => {
    const mining = {
      status: "found" as const,
      block: data.block,
      attempts: 1,
      elapsedMs: 1,
      observationMs: 0,
      mode: "fast" as const,
      samples: [],
    };
    const partial = renderToStaticMarkup(
      createElement(LearningGuide, {
        page: 1,
        lab: data.minedAtA,
        lesson,
        mining,
        nodeId: "A",
        navigate: () => {},
      }),
    );
    expect(partial).toContain("일부 노드에 블록이 반영됐습니다");
    expect(partial).not.toContain("대기 상태 살펴보기");
    const next = renderToStaticMarkup(
      createElement(LearningGuide, {
        page: 3,
        lab: data.delivered,
        lesson: { ...data.secondDraft, origin: "A", phase: "draft" },
        mining,
        nodeId: "A",
        navigate: () => {},
      }),
    );
    expect(next).not.toContain("블록을 찾았습니다");
    expect(next).toContain("준비 단계 살펴보기");
  });
  it("Mempool, 채굴 노드와 미수신 노드, 전체 포함을 구분한다", () => {
    expect(transactionPresence(data.submitted, lesson, "A").state).toBe(
      "pending",
    );
    expect(transactionPresence(data.submitted, lesson, "B").state).toBe(
      "unseen",
    );
    expect(transactionPresence(data.minedAtA, lesson, "A").state).toBe(
      "included",
    );
    expect(allIncluded(data.minedAtA, lesson)).toBe(false);
    expect(allIncluded(data.delivered, lesson)).toBe(true);
  });
  it("이전 거래 완료를 새 거래나 거부된 실험에 옮기지 않는다", () => {
    const second: TransactionLesson = {
      ...data.secondDraft,
      origin: "A",
      phase: "draft",
    };
    expect(allIncluded(data.delivered, second)).toBe(false);
    const failed = {
      ...lesson,
      checked: data.wrong,
      validation: data.wrongValidation,
    };
    expect(transactionPresence(data.submitted, failed, "A").state).toBe(
      "rejected",
    );
    expect(transactionPresence(data.submitted, failed, "B").state).toBe(
      "unseen",
    );
  });
  it("출력이 이후 소비돼도 거래의 블록 포함 상태를 유지한다", () => {
    const consumed = structuredClone(data.delivered);
    consumed.nodes.A.utxos = {};
    expect(transactionPresence(consumed, lesson, "A").state).toBe("included");
  });
  it("입력이 여러 개이거나 거스름돈이 없어도 실제 값을 합산한다", () => {
    const multiple: TransactionLesson = {
      ...lesson,
      signed: undefined,
      inputs: [
        { ...lesson.inputs[0], amount: 40 },
        { ...lesson.inputs[0], amount: 60 },
      ],
      draft: {
        ...lesson.draft,
        outputs: [{ ...lesson.draft.outputs[0], amount: 100 }],
      },
    };
    expect(lessonTotals(multiple)).toEqual({
      input: 100,
      sent: 100,
      change: 0,
    });
  });
  it("기준 노드와 현재 노드를 명시하고 예약·블록 포함을 설명한다", () => {
    const tracked = {
      ...lesson,
      baseline: {
        node: "A" as const,
        balances: structuredClone(data.initial.nodes.A.balances),
      },
    };
    const pending = renderToStaticMarkup(
      createElement(TransactionTrace, {
        lab: data.submitted,
        nodeId: "A",
        lesson: tracked,
      }),
    );
    expect(pending).toContain("입력 전체가 예약");
    expect(pending).toContain("100 / 0");
    const otherNode = renderToStaticMarkup(
      createElement(TransactionTrace, {
        lab: data.delivered,
        nodeId: "B",
        lesson: tracked,
      }),
    );
    expect(otherNode).toContain("작성 당시 Node A → 현재 Node B");
    expect(otherNode).toContain("70 / 70");
    expect(tracked.baseline.balances).toEqual(data.initial.nodes.A.balances);
  });
});
