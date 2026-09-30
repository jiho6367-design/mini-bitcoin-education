import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { createWallet, hashText, signTransaction } from "../core/crypto";
import { Lab } from "../core/lab";
import { buildTransaction } from "../core/transaction";
import { NODE_IDS, type NetworkMessage, type NodeId } from "../core/types";
import { NetworkDiagram } from "./NetworkView";
import { messageRoute, nodePositions, selectedQueuedMessage } from "./network";

const alice = createWallet("Alice", "1".padStart(64, "0"));
const bob = createWallet("Bob", "2".padStart(64, "0"));
const setup = () => new Lab([alice, bob], 1, 1000, hashText("network-display"));
function submit(lab: Lab, from: NodeId = "A") {
  const tx = signTransaction(
    buildTransaction(
      alice,
      bob.address,
      30,
      lab.nodes[from].utxos,
      [],
      lab.sessionId,
    ),
    alice.privateKey,
  );
  lab.submit(from, tx);
  return tx;
}

describe("실제 메시지 경로 표시", () => {
  it("A/B/C의 여섯 출발·도착 방향을 구분하고 도착 노드에 화살표를 연결한다", () => {
    const lab = setup();
    const tx = submit(lab);
    const paths = new Set<string>();
    for (const from of NODE_IDS) {
      for (const to of NODE_IDS.filter((id) => id !== from)) {
        const message: NetworkMessage = {
          id: 10,
          from,
          to,
          kind: "transaction",
          payload: tx,
        };
        const route = messageRoute(from, to);
        paths.add(route.path);
        // 출발점과 끝점이 실제 노드의 원 가장자리와 닿는다.
        expect(Math.abs(route.start.x - nodePositions[from].x)).toBe(35);
        expect(Math.abs(route.end.x - nodePositions[to].x)).toBe(35);
        expect(Math.sign(route.end.x - route.start.x)).toBe(
          Math.sign(nodePositions[to].x - nodePositions[from].x),
        );
        const html = renderToStaticMarkup(
          createElement(NetworkDiagram, { lab, selectedMessage: message }),
        );
        expect(html).toContain(
          `data-from="${from}" data-to="${to}" data-kind="transaction"`,
        );
        expect(html).toContain(`d="${route.path}"`);
        expect(html).toContain("marker-end=");
        expect(html).toContain(`선택 대기 경로: ${from} → ${to} · 거래 #10`);
      }
    }
    expect(paths.size).toBe(6);
  });

  it("큐 선택은 미리보기이며 FIFO 다음 전달을 바꾸지 않는다", () => {
    const lab = setup();
    submit(lab);
    const [next, selected] = lab.queue;
    expect(selectedQueuedMessage(lab.queue, null)).toBe(next);
    expect(selectedQueuedMessage(lab.queue, selected.id)).toBe(selected);
    const selectedHtml = renderToStaticMarkup(
      createElement(NetworkDiagram, { lab, selectedMessage: selected }),
    );
    expect(selectedHtml).toContain('data-from="A" data-to="C"');
    expect(lab.deliverNext()?.valid).toBe(true);
    expect(lab.lastDelivery?.message.to).toBe("B");
    expect(lab.nodes.B.mempool).toHaveLength(1);
    expect(lab.nodes.C.mempool).toHaveLength(0);
    expect(selectedQueuedMessage(lab.queue, next.id)).toBe(selected);
  });

  it("블록 종류와 최근 수신의 실제 승인·거부 결과를 표시한다", () => {
    const lab = setup();
    submit(lab, "C");
    // 실제 큐에 있는 거래 사본의 서명을 훼손하여 수신 노드의 검증 실패를 만든다.
    const invalid = lab.queue[0];
    if (invalid.kind !== "transaction")
      throw new Error("거래 테스트 메시지 필요");
    invalid.payload = {
      ...invalid.payload,
      signature: "0".repeat(128),
    } as typeof invalid.payload;
    const result = lab.deliverNext();
    expect(result?.valid).toBe(false);
    const blockMessage: NetworkMessage = {
      id: 20,
      from: "B",
      to: "C",
      kind: "block",
      payload: lab.genesis,
    };
    const html = renderToStaticMarkup(
      createElement(NetworkDiagram, { lab, selectedMessage: blockMessage }),
    );
    expect(html).toContain('data-from="B" data-to="C" data-kind="block"');
    expect(html).toContain("선택 대기 경로: B → C · 블록 #20");
    expect(html).toContain(
      'data-testid="recent-message-route" data-from="C" data-to="A" data-kind="transaction"',
    );
    expect(html).toContain(`최근 수신: C → A · 거래 #${invalid.id} · ✕ 거부`);
    expect(lab.nodes.A.mempool).toHaveLength(0);
    lab.deliverNext();
    const acceptedHtml = renderToStaticMarkup(
      createElement(NetworkDiagram, { lab }),
    );
    expect(acceptedHtml).toContain("최근 수신: C → B");
    expect(acceptedHtml).toContain("✓ 승인");
    expect(selectedQueuedMessage(lab.queue, invalid.id)).toBeNull();
    expect(acceptedHtml).not.toContain('data-testid="selected-message-route"');
  });
});
