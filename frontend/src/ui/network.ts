import type { NetworkMessage, NodeId } from "../api/types";

export const nodePositions: Record<NodeId, { x: number; y: number }> = {
  A: { x: 100, y: 100 },
  B: { x: 300, y: 100 },
  C: { x: 500, y: 100 },
};

// 반대 방향은 선의 반대쪽을 지나도록 한다. A↔C는 바깥쪽 큰 곡선이다.
// 이 좌표는 전달 연산이나 시간을 표현하지 않고 실제 출발·도착만 표현한다.
export function messageRoute(from: NodeId, to: NodeId) {
  const start = nodePositions[from];
  const end = nodePositions[to];
  const direction = Math.sign(end.x - start.x);
  const gap = Math.abs(end.x - start.x);
  const lane = direction * 10;
  const x1 = start.x + direction * 35;
  const x2 = end.x - direction * 35;
  const y = start.y + lane;
  const bend = gap > 200 ? 136 : 42;
  const controlY = start.y + direction * bend;
  return {
    path: `M${x1} ${y} Q${(start.x + end.x) / 2} ${controlY} ${x2} ${y}`,
    labelX: (start.x + end.x) / 2,
    labelY: (y + controlY) / 2,
    start: { x: x1, y },
    end: { x: x2, y },
  };
}

export const messageKind = (kind: NetworkMessage["kind"]) =>
  kind === "block" ? "블록" : "거래";

export const messageLabel = (message: NetworkMessage) =>
  `${message.from} → ${message.to} · ${messageKind(message.kind)} #${message.id}`;

export function selectedQueuedMessage(
  queue: NetworkMessage[],
  selectedId: number | null,
) {
  return queue.find((message) => message.id === selectedId) ?? queue[0] ?? null;
}
