import { expect, it } from "vitest";
import type { LabSnapshot } from "../api/types";
import scenarios from "../test-fixtures/lab-scenarios.json";
import { walletAvailability } from "./walletAvailability";

// Real engine fixtures contain a signed 30-coin transfer with fee 0, funded by one 100-coin input.
const data = scenarios.learning as unknown as {
  initial: LabSnapshot; submitted: LabSnapshot; minedAtA: LabSnapshot; delivered: LabSnapshot;
};
const address = data.initial.wallets[0].address;

it("실제로 접수한 거래는 송금액이 아니라 입력 100 전체를 예약한다", () => {
  expect(walletAvailability(data.initial.nodes.A, address)).toMatchObject({
    known: true, confirmed: 100, spendable: 100, reserved: 0, reservedInputs: 0,
  });
  expect(walletAvailability(data.submitted.nodes.A, address)).toMatchObject({
    known: true, confirmed: 100, spendable: 0, reserved: 100, reservedInputs: 1,
  });
});

it("같은 대기 거래를 아직 받지 않은 노드는 예약 금액도 다르게 표시한다", () => {
  expect(walletAvailability(data.submitted.nodes.B, address)).toMatchObject({
    confirmed: 100, spendable: 100, reserved: 0,
  });
});

it("실제 채굴·전파 후에는 거스름돈이 확정되고 예약이 풀린다", () => {
  for (const node of Object.values(data.delivered.nodes)) {
    expect(walletAvailability(node, address)).toMatchObject({
      confirmed: 70, spendable: 70, reserved: 0, reservedInputs: 0,
    });
  }
});

it("성숙 대기 중인 실제 채굴 보상을 대기 거래 예약으로 오인하지 않는다", () => {
  const miner = data.minedAtA.minerWallets!.A.address;
  expect(walletAvailability(data.minedAtA.nodes.A, miner)).toMatchObject({
    confirmed: 50, spendable: 0, reserved: 0, immature: 50,
  });
});

it("다른 지갑 입력과 중복 예약 키는 이 지갑의 예약 금액에 더하지 않는다", () => {
  const node = structuredClone(data.submitted.nodes.A);
  const key = node.reserved[0];
  node.utxos["other:0"] = { txId: "other", outputIndex: 0, address: "other-wallet", amount: 999 };
  node.reserved.push(key, "other:0", "missing:0");
  expect(walletAvailability(node, address)).toMatchObject({ reserved: 100, reservedInputs: 1 });
});

it("잔액 정보가 없을 때는 0으로 단정하지 않고 확인 중으로 남긴다", () => {
  expect(walletAvailability(undefined, address)).toMatchObject({
    known: false, confirmed: null, spendable: null, reserved: null, immature: null,
  });
  expect(walletAvailability(data.initial.nodes.A, "unknown-wallet").known).toBe(false);
});
