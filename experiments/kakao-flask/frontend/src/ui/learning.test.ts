import { describe, expect, it } from "vitest";
import type {
  Block,
  DraftResult,
  LabSnapshot,
  Transaction,
  ValidationResult,
} from "../api/types";
import scenarios from "../test-fixtures/lab-scenarios.json";
import { learningFlow, restoreLessons, type TransactionLesson } from "./learning";

// 백엔드의 실제 규칙으로 만든 실습 상태 backend/scripts/export_frontend_fixtures.py 참고
const data = scenarios.learning as unknown as {
  initial: LabSnapshot;
  draft: DraftResult;
  signed: Transaction;
  validation: ValidationResult;
  submitted: LabSnapshot;
  wrong: Transaction;
  wrongValidation: ValidationResult;
  afterWrong: LabSnapshot;
  candidate: Block;
  block: Block;
  minedAtA: LabSnapshot;
  oneMessageLeft: LabSnapshot;
  delivered: LabSnapshot;
  secondDraft: DraftResult;
  otherNodeAccepted: LabSnapshot;
};
const drafted = (result: DraftResult): TransactionLesson => ({
  origin: "A",
  phase: "draft",
  ...result,
});
const approved = (): TransactionLesson => ({
  ...drafted(data.draft),
  phase: "validated",
  signed: data.signed,
  checked: data.signed,
  validation: data.validation,
});
const idle = { status: "idle" };
describe("선택 거래의 학습 흐름", () => {
  it("작성·서명·검증 관찰·Mempool을 구분하고 실패 검증은 완료 처리하지 않는다", () => {
    const lesson = drafted(data.draft);
    expect(learningFlow(data.initial, lesson, idle).current).toBe(1);
    expect(
      learningFlow(
        data.initial,
        { ...lesson, phase: "signed", signed: data.signed },
        idle,
      ).current,
    ).toBe(2);
    expect(data.validation.valid).toBe(true);
    const validated: TransactionLesson = {
      ...lesson,
      signed: data.signed,
      phase: "validated",
      validation: data.validation,
    };
    expect(learningFlow(data.submitted, validated, idle).current).toBe(3);
    expect(data.submitted.nodes.A.mempool).toHaveLength(1); // 표시 단계는 실제 등록을 지연하지 않는다
    expect(
      learningFlow(data.submitted, { ...validated, phase: "mempool" }, idle)
        .current,
    ).toBe(3);
    expect(data.wrongValidation.code).toBe("SIGNATURE");
    const failed = learningFlow(
      data.afterWrong,
      { ...validated, validation: data.wrongValidation },
      idle,
    );
    expect(failed.failed).toBe(true);
    expect(failed.current).toBe(2);
    expect(failed.completed).not.toContain(2);
  });
  it("선택 거래가 포함된 채굴 후보와 실제 노드 블록 수락만으로 진행한다", () => {
    const lesson = { ...approved(), phase: "mempool" as const };
    expect(
      learningFlow(data.submitted, lesson, {
        status: "paused",
        block: data.candidate,
      }).current,
    ).toBe(4);
    expect(
      learningFlow(data.submitted, lesson, {
        status: "running",
        block: { ...data.candidate, transactions: [] },
      }).current,
    ).toBe(3);
    expect(
      learningFlow(data.minedAtA, lesson, {
        status: "found",
        block: data.block,
      }).current,
    ).toBe(5);
    expect(data.oneMessageLeft.queue).toHaveLength(1);
    expect(learningFlow(data.oneMessageLeft, lesson, idle).current).toBe(5);
    expect(learningFlow(data.delivered, lesson, idle).current).toBe(6);
  });
  it("두 번째 거래와 새 작성에서는 기존 체인 높이를 완료로 오해하지 않는다", () => {
    const first = approved();
    expect(learningFlow(data.delivered, first, idle).current).toBe(6);
    expect(learningFlow(data.delivered, null, idle).current).toBe(0);
    expect(
      learningFlow(data.delivered, drafted(data.secondDraft), idle).current,
    ).toBe(1);
    expect(learningFlow(null, first, idle).current).toBe(0);
  });
  it("다른 노드의 검증 결과가 현재 거래의 실패 판정을 덮어쓰지 않는다", () => {
    const failed: TransactionLesson = {
      ...approved(),
      checked: data.wrong,
      validation: data.wrongValidation,
    };
    expect(data.otherNodeAccepted.lastValidation?.valid).toBe(true);
    expect(learningFlow(data.otherNodeAccepted, failed, idle).failed).toBe(
      true,
    );
  });
});

describe("저장 snapshot에서 학습 관찰 복원", () => {
  it("소비된 입력도 체인에서 찾아 원래 금액과 포함 단계를 재구성한다", () => {
    const restored = restoreLessons(data.delivered);
    const lesson = restored.find(item => item.signed?.id === data.signed.id)!;
    expect(lesson).toBeDefined();
    expect(lesson.inputs.reduce((sum, input) => sum + input.amount, 0)).toBe(100);
    expect(lesson.restored).toBe(true);
    expect(learningFlow(data.delivered, lesson, idle).current).toBe(6);
    expect(restored.every(item => item.signed?.inputs.length)).toBe(true);
  });
  it("멤풀 거래의 단계는 대기로 복원하고 알 수 없는 입력은 추정하지 않는다", () => {
    const lesson = restoreLessons(data.submitted).find(item => item.signed?.id === data.signed.id)!;
    expect(learningFlow(data.submitted, lesson, idle).current).toBe(3);
    const damaged = structuredClone(data.submitted);
    damaged.nodes.A.mempool[0].inputs = [{ txId: "not-in-snapshot", outputIndex: 0 }];
    expect(restoreLessons(damaged).some(item => item.signed?.id === data.signed.id)).toBe(false);
  });
});
