import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  NODE_IDS,
  type LabSnapshot,
  type NetworkMessage,
} from "../api/types";
import scenarios from "../test-fixtures/lab-scenarios.json";
import { NetworkDiagram } from "./NetworkView";
import { messageRoute, nodePositions, selectedQueuedMessage } from "./network";

// 백엔드의 실제 규칙으로 만든 실습 상태. backend/scripts/export_frontend_fixtures.py 참고.
const data = scenarios.network as unknown as {
  submitted: LabSnapshot;
  afterFirstDelivery: LabSnapshot;
  invalidMessageId: number;
  rejectedDelivery: LabSnapshot;
  acceptedDelivery: LabSnapshot;
};

describe("실제 메시지 경로 표시", () => {
  it("A/B/C의 여섯 출발·도착 방향을 구분하고 도착 노드에 화살표를 연결한다", () => {
    const lab = data.submitted;
    const paths = new Set<string>();
    for (const from of NODE_IDS) {
      for (const to of NODE_IDS.filter((id) => id !== from)) {
        const message: NetworkMessage = {
          id: 10,
          from,
          to,
          kind: "transaction",
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
    const before = data.submitted;
    const [next, selected] = before.queue;
    expect(selectedQueuedMessage(before.queue, null)).toBe(next);
    expect(selectedQueuedMessage(before.queue, selected.id)).toBe(selected);
    const selectedHtml = renderToStaticMarkup(
      createElement(NetworkDiagram, {
        lab: before,
        selectedMessage: selected,
      }),
    );
    expect(selectedHtml).toContain('data-from="A" data-to="C"');
    // 백엔드는 선택과 무관하게 대기열의 첫 메시지(A → B)를 전달했다.
    const after = data.afterFirstDelivery;
    expect(after.lastDelivery?.validation.valid).toBe(true);
    expect(after.lastDelivery?.message).toEqual(next);
    expect(after.nodes.B.mempool).toHaveLength(1);
    expect(after.nodes.C.mempool).toHaveLength(0);
    expect(selectedQueuedMessage(after.queue, next.id)).toEqual(selected);
  });

  it("블록 종류와 최근 수신의 실제 승인·거부 결과를 표시한다", () => {
    // 전달 도중 서명이 훼손된 거래 사본을 Node A가 거부한 상태.
    const rejected = data.rejectedDelivery;
    expect(rejected.lastDelivery?.validation.valid).toBe(false);
    const blockMessage: NetworkMessage = {
      id: 20,
      from: "B",
      to: "C",
      kind: "block",
    };
    const html = renderToStaticMarkup(
      createElement(NetworkDiagram, {
        lab: rejected,
        selectedMessage: blockMessage,
      }),
    );
    expect(html).toContain('data-from="B" data-to="C" data-kind="block"');
    expect(html).toContain("선택 대기 경로: B → C · 블록 #20");
    expect(html).toContain(
      'data-testid="recent-message-route" data-from="C" data-to="A" data-kind="transaction"',
    );
    expect(html).toContain(
      `최근 수신: C → A · 거래 #${data.invalidMessageId} · ✕ 거부`,
    );
    expect(rejected.nodes.A.mempool).toHaveLength(0);
    const accepted = data.acceptedDelivery;
    const acceptedHtml = renderToStaticMarkup(
      createElement(NetworkDiagram, { lab: accepted }),
    );
    expect(acceptedHtml).toContain("최근 수신: C → B");
    expect(acceptedHtml).toContain("✓ 승인");
    expect(
      selectedQueuedMessage(accepted.queue, data.invalidMessageId),
    ).toBeNull();
    expect(acceptedHtml).not.toContain('data-testid="selected-message-route"');
  });
});
