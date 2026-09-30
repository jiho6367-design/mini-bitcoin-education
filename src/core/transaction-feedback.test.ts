import { describe, expect, it } from "vitest";
import { createWallet, hashText, signTransaction } from "./crypto";
import { Lab } from "./lab";
import {
  buildTransaction,
  reservedInputs,
  validateTransaction,
} from "./transaction";
import type { Transaction, UtxoSet } from "./types";

const alice = createWallet("Alice", "1".padStart(64, "0"));
const bob = createWallet("Bob", "2".padStart(64, "0"));

function fixture() {
  const lab = new Lab([alice, bob], 1, 1000, hashText("feedback-test"));
  const tx = signTransaction(
    buildTransaction(
      alice,
      bob.address,
      30,
      lab.nodes.A.utxos,
      [],
      lab.sessionId,
    ),
    alice.privateKey,
  );
  return { lab, tx };
}

const successMessages = [
  "입력·출력, 키·서명 형식과 양의 정수 금액이 올바릅니다.",
  "거래의 실습 식별자가 현재 실습과 일치합니다.",
  "서명된 거래를 다시 해시한 값이 거래 ID와 일치합니다.",
  "공개키로 검증한 서명이 거래 원문과 일치합니다.",
  "한 거래 안에서 같은 출력을 중복 사용하지 않습니다.",
  "모든 입력이 존재하는 확정 UTXO를 가리킵니다.",
  "다른 대기 거래나 같은 블록의 거래가 사용 중인 입력이 없습니다.",
  "모든 입력이 서명자의 공개키에 대응하는 주소 소유입니다.",
  "입력 합계 100 코인과 출력 합계 100 코인이 일치합니다. 수수료는 0입니다.",
];

type Rejection = {
  code: string;
  label: string;
  message: string;
  alter?: (tx: Transaction) => unknown;
  reserved?: (tx: Transaction) => Set<string>;
  utxos?: (utxos: UtxoSet) => UtxoSet;
};

const rejections: Rejection[] = [
  {
    code: "FORMAT",
    label: "형식·금액",
    message:
      "입력·출력, 키·서명 형식 또는 금액이 올바르지 않습니다. 금액은 1 이상의 안전한 정수여야 합니다.",
    alter: (tx) => ({ ...tx, outputs: [{ address: bob.address, amount: 0 }] }),
  },
  {
    code: "SESSION",
    label: "실습 식별자",
    message: "거래의 실습 식별자가 현재 실습과 일치하지 않습니다.",
    alter: (tx) =>
      signTransaction(
        { ...tx, sessionId: hashText("another-lab") },
        alice.privateKey,
      ),
  },
  {
    code: "TX_ID",
    label: "거래 ID",
    message: "서명된 거래를 다시 해시한 값이 거래 ID와 일치하지 않습니다.",
    alter: (tx) => ({ ...tx, id: hashText("wrong-id") }),
  },
  {
    code: "SIGNATURE",
    label: "전자서명",
    message: "공개키로 검증한 서명이 거래 원문과 일치하지 않습니다.",
    alter: (tx) => signTransaction(tx, bob.privateKey),
  },
  {
    code: "DUPLICATE_INPUT",
    label: "중복 입력",
    message: "같은 출력을 한 거래에서 두 번 사용하려고 합니다.",
    alter: (tx) =>
      signTransaction(
        { ...tx, inputs: [...tx.inputs, ...tx.inputs] },
        alice.privateKey,
      ),
  },
  {
    code: "MISSING_UTXO",
    label: "미사용 출력",
    message:
      "입력이 가리키는 확정 UTXO가 없습니다. 존재하지 않거나 이미 소비한 출력은 사용할 수 없습니다.",
    utxos: () => ({}),
  },
  {
    code: "DOUBLE_SPEND",
    label: "이중 지출",
    message:
      "선택한 입력을 다른 대기 거래나 같은 블록의 거래가 이미 사용 중입니다.",
    reserved: (tx) => reservedInputs([tx]),
  },
  {
    code: "OWNER",
    label: "소유권",
    message: "서명자의 공개키에 대응하는 주소가 소유하지 않은 입력이 있습니다.",
    alter: (tx) =>
      signTransaction({ ...tx, publicKey: bob.publicKey }, bob.privateKey),
  },
  {
    code: "VALUE",
    label: "금액 보존",
    message:
      "입력·출력 합계는 안전한 정수여야 하며, 수수료가 0이므로 두 합계가 같아야 합니다.",
    alter: (tx) =>
      signTransaction(
        { ...tx, outputs: [{ address: bob.address, amount: 101 }] },
        alice.privateKey,
      ),
  },
];

describe("거래 검증 판정과 교육용 설명", () => {
  it("정상 거래의 모든 단계가 통과 사실을 설명한다", () => {
    const { lab, tx } = fixture();
    const result = validateTransaction(tx, lab.nodes.A.utxos, lab.sessionId);
    expect(result.valid).toBe(true);
    expect(result.steps.every((step) => step.ok)).toBe(true);
    expect(result.steps.map((step) => step.message)).toEqual(successMessages);
  });

  it.each(rejections)(
    "$code의 실패 설명을 실제 실패 단계와 연결한다",
    (example) => {
      const { lab, tx } = fixture();
      const result = validateTransaction(
        example.alter?.(tx) ?? tx,
        example.utxos?.(lab.nodes.A.utxos) ?? lab.nodes.A.utxos,
        lab.sessionId,
        example.reserved?.(tx),
      );
      expect(result.valid).toBe(false);
      expect(result.code).toBe(example.code);
      expect(result.message).toBe(example.message);
      expect(result.steps.at(-1)).toEqual({
        ok: false,
        label: example.label,
        message: example.message,
      });
      const passed = result.steps.slice(0, -1);
      expect(passed.every((step) => step.ok)).toBe(true);
      expect(passed.map((step) => step.message)).toEqual(
        successMessages.slice(0, passed.length),
      );
    },
  );
});
