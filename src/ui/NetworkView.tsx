import { useId, useState } from "react";
import {
  calculateNonce,
  createCandidate,
  validateChain,
  validPow,
} from "../core/block";
import { balance } from "../core/transaction";
import type { Lab } from "../core/lab";
import { NODE_IDS, type NetworkMessage, type NodeId } from "../core/types";
import {
  messageKind,
  messageLabel,
  messageRoute,
  nodePositions,
  selectedQueuedMessage,
} from "./network";
import { Code, Notice, Panel, short, Validation } from "./shared";

export function NetworkDiagram({
  lab,
  selectedMessage = lab.queue[0] ?? null,
}: {
  lab: Lab;
  selectedMessage?: NetworkMessage | null;
}) {
  const arrowId = useId().replaceAll(":", "");
  const recent = lab.lastDelivery;
  const selectedRoute = selectedMessage
    ? messageRoute(selectedMessage.from, selectedMessage.to)
    : null;
  const recentRoute = recent
    ? messageRoute(recent.message.from, recent.message.to)
    : null;
  const recentColor = recent?.validation.valid
    ? "var(--success)"
    : "var(--danger)";
  const routeLabel = selectedMessage
    ? `선택 대기 메시지 ${messageLabel(selectedMessage)}`
    : recent
      ? `최근 수신 메시지 ${messageLabel(recent.message)}`
      : "전달 대기 메시지가 없습니다";
  return (
    <div className="network-diagram">
      <svg
        viewBox="0 0 600 220"
        role="img"
        aria-label={`독립 노드 A, B, C 사이의 모의 메시지 전달. ${routeLabel}`}
      >
        <defs>
          {[
            ["pending", "var(--pending)"],
            ["recent", recentColor],
          ].map(([kind, color]) => (
            <marker
              key={kind}
              id={`${arrowId}-${kind}`}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="5"
              markerHeight="5"
              orient="auto"
            >
              <path d="M0 0 L10 5 L0 10 Z" fill={color} />
            </marker>
          ))}
        </defs>
        <path
          d="M100 100 L300 100 L500 100 M100 100 Q300 236 500 100"
          fill="none"
          stroke="var(--diagram-edge)"
          strokeWidth="2"
          strokeDasharray="6 6"
        />
        {recent && recentRoute && (
          <path
            data-testid="recent-message-route"
            data-from={recent.message.from}
            data-to={recent.message.to}
            data-kind={recent.message.kind}
            d={recentRoute.path}
            fill="none"
            stroke={recentColor}
            strokeWidth="5"
            strokeDasharray="8 6"
            markerEnd={`url(#${arrowId}-recent)`}
          />
        )}
        {selectedMessage && selectedRoute && (
          <g>
            <path
              data-testid="selected-message-route"
              data-from={selectedMessage.from}
              data-to={selectedMessage.to}
              data-kind={selectedMessage.kind}
              d={selectedRoute.path}
              fill="none"
              stroke="var(--pending)"
              strokeWidth="3"
              markerEnd={`url(#${arrowId}-pending)`}
            />
            <rect
              x={selectedRoute.labelX - 23}
              y={selectedRoute.labelY - 11}
              width="46"
              height="22"
              rx="5"
              fill="var(--pending-bg)"
            />
            <text
              x={selectedRoute.labelX}
              y={selectedRoute.labelY + 5}
              textAnchor="middle"
              fill="var(--pending-text)"
              fontSize="13"
            >
              {messageKind(selectedMessage.kind)}
            </text>
          </g>
        )}
        {NODE_IDS.map((id) => (
          <g key={id}>
            <circle
              cx={nodePositions[id].x}
              cy="100"
              r="33"
              fill="var(--diagram-node-bg)"
              stroke="var(--diagram-node-stroke)"
            />
            <text
              x={nodePositions[id].x}
              y="107"
              textAnchor="middle"
              fill="var(--diagram-node-text)"
              fontSize="23"
              fontWeight="700"
            >
              {id}
            </text>
            <text
              x={nodePositions[id].x}
              y="155"
              textAnchor="middle"
              fill="var(--diagram-caption)"
              fontSize="13"
            >
              높이 {lab.nodes[id].chain.length - 1} · 대기{" "}
              {lab.nodes[id].mempool.length}
            </text>
          </g>
        ))}
      </svg>
      <div className="network-caption" aria-live="polite">
        <p data-testid="selected-message-caption">
          {selectedMessage
            ? `선택 대기 경로: ${messageLabel(selectedMessage)}`
            : "선택할 대기 메시지가 없습니다."}
        </p>
        {recent && (
          <p data-testid="recent-message-caption">
            최근 수신: {messageLabel(recent.message)} ·{" "}
            {recent.validation.valid ? "✓ 승인" : "✕ 거부"}
          </p>
        )}
      </div>
      <p className="network-legend muted">
        주황 실선: 선택한 전달 대기 경로 · 초록/빨강 점선: 최근 수신의
        승인/거부. 화살표는 도착 노드를 가리키며 이동 속도나 실제 인터넷 통신을
        표현하지 않습니다.
      </p>
    </div>
  );
}
export function MempoolView({
  lab,
  goMining,
}: {
  lab: Lab;
  goMining: () => void;
}) {
  return (
    <>
      <Notice>
        Mempool은 노드마다 다릅니다. 전달되기 전에는 A에만 거래가 있을 수
        있습니다. Mempool 등록은 블록 확정이 아닙니다.
      </Notice>
      <div className="three-col">
        {NODE_IDS.map((id) => (
          <Panel key={id} title={`Node ${id}`} eyebrow="LOCAL MEMPOOL">
            <div className="big-number">
              {lab.nodes[id].mempool.length}
              <span>대기 거래</span>
            </div>
            {lab.nodes[id].mempool.length === 0 ? (
              <p className="empty compact">대기 거래가 없습니다.</p>
            ) : (
              lab.nodes[id].mempool.map((tx) => (
                <details key={tx.id} className="tx-row">
                  <summary>{short(tx.id, 8)}</summary>
                  <Code>{tx.id}</Code>
                  <p>
                    입력 {tx.inputs.length}개 → 출력 {tx.outputs.length}개
                  </p>
                  {tx.outputs.map((o, i) => (
                    <p key={i}>
                      {o.amount} 코인 ·{" "}
                      {lab.wallets.find((w) => w.address === o.address)?.name ??
                        short(o.address)}
                    </p>
                  ))}
                </details>
              ))
            )}
          </Panel>
        ))}
      </div>
      <div className="two-col">
        <Panel title="가장 최근 검증">
          <Validation value={lab.lastValidation} />
        </Panel>
        <Panel title="다음은 작업증명입니다" eyebrow="NEXT STEP">
          <p>
            채굴 노드가 대기 거래로 후보 블록을 구성하고, 난이도 조건을 만족하는
            Nonce를 찾습니다.
          </p>
          <p>
            채굴자가 만든 블록도 다른 노드가 다시 검증합니다. 채굴 성공만으로
            모든 노드에 기록되지는 않습니다.
          </p>
          <button onClick={goMining}>Mining으로 이동 →</button>
          <NetworkDiagram lab={lab} />
        </Panel>
      </div>
    </>
  );
}
export function NetworkView({
  lab,
  nodeId,
  automatic,
  setAutomatic,
  touch,
}: {
  lab: Lab;
  nodeId: NodeId;
  automatic: boolean;
  setAutomatic: (value: boolean) => void;
  touch: () => void;
}) {
  const [selected, setSelected] = useState(0);
  const [selectedMessageId, setSelectedMessageId] = useState<number | null>(
    null,
  );
  const selectedMessage = selectedQueuedMessage(lab.queue, selectedMessageId);
  const nextMessage = lab.queue[0];
  const node = lab.nodes[nodeId];
  const block = node.chain[Math.min(selected, node.chain.length - 1)];
  const verification = validateChain(node.chain, lab.genesis);
  return (
    <>
      <div className="two-col">
        <Panel title="세 노드, 세 번의 검증" eyebrow="SIMULATED NETWORK">
          <NetworkDiagram lab={lab} selectedMessage={selectedMessage} />
          <label className="toggle">
            <input
              type="checkbox"
              checked={automatic}
              onChange={(e) => setAutomatic(e.target.checked)}
            />{" "}
            메시지 자동 전달 <span>700ms 간격</span>
          </label>
          <button
            className="secondary"
            disabled={automatic || !lab.queue.length}
            onClick={() => {
              lab.deliverNext();
              touch();
            }}
          >
            메시지 1개 전달
          </button>
          <p data-testid="next-message-caption">
            다음 실제 전달:{" "}
            {nextMessage ? messageLabel(nextMessage) : "대기 없음"}
          </p>
          <p className="muted">
            목록 선택은 경로 미리보기입니다. 전달 버튼과 자동 전달은 대기열의 첫
            메시지를 처리합니다. 수신 전후 노드의 높이·Mempool·잔액을
            비교하세요.
          </p>
          {lab.lastDelivery && (
            <div className="delivery-result">
              <strong>
                Node {lab.lastDelivery.message.to}의 최근 수신 검증 ·{" "}
                {lab.lastDelivery.validation.valid ? "✓ 승인" : "✕ 거부"}
              </strong>
              <p>{lab.lastDelivery.validation.message}</p>
              <details className="delivery-validation">
                <summary>이 메시지의 검증 단계 보기</summary>
                <Validation value={lab.lastDelivery.validation} />
              </details>
            </div>
          )}
        </Panel>
        <Panel
          title={`전달 대기열 · ${lab.queue.length}`}
          eyebrow="MESSAGE QUEUE"
        >
          {lab.queue.length === 0 ? (
            <div className="empty">
              모든 메시지가 전달되었습니다.
              <small>수신 노드는 자기 UTXO와 체인으로 검증합니다.</small>
            </div>
          ) : (
            <ol className="message-list">
              {lab.queue.map((m, index) => (
                <li key={m.id}>
                  <button
                    className={`message-choice ${selectedMessage?.id === m.id ? "selected" : ""}`}
                    aria-pressed={selectedMessage?.id === m.id}
                    aria-label={`경로 보기 ${messageLabel(m)}`}
                    onClick={() => setSelectedMessageId(m.id)}
                  >
                    <strong>
                      {m.from} → {m.to}
                    </strong>
                    <span>
                      {messageKind(m.kind)} #{m.id}
                    </span>
                    <span className="tag">
                      {index === 0 ? "다음 전달" : "전달 대기"}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>
      <div className="three-col">
        {NODE_IDS.map((id) => (
          <Panel
            key={id}
            title={`Node ${id}`}
            className={id === nodeId ? "selected-node" : ""}
          >
            <div className="node-stat">
              <span>체인 높이</span>
              <b>{lab.nodes[id].chain.length - 1}</b>
            </div>
            <Code>{short(lab.nodes[id].chain.at(-1)!.hash)}</Code>
            {lab.wallets.map((w) => (
              <div className="node-stat" key={w.address}>
                <span>{w.name}</span>
                <b>{balance(lab.nodes[id].utxos, w.address)} 코인</b>
              </div>
            ))}
          </Panel>
        ))}
      </div>
      <Panel title={`Node ${nodeId}의 블록체인`} eyebrow="BLOCK EXPLORER">
        <div className="chain-strip">
          {node.chain.map((b, i) => (
            <button
              className={`block-tile ${block.height === i ? "active" : ""}`}
              key={b.hash}
              onClick={() => setSelected(i)}
            >
              <span>
                {i === 0 ? "GENESIS" : `BLOCK ${i.toString().padStart(2, "0")}`}
              </span>
              <strong>
                {i === 0 ? "초기 배정" : `${b.transactions.length}개 거래`}
              </strong>
              <small>{short(b.hash, 8)}</small>
              {i > 0 && (
                <span className="block-link" aria-hidden="true">
                  ←
                </span>
              )}
            </button>
          ))}
        </div>
        <div className={`result ${verification.valid ? "good" : "bad"}`}>
          {verification.valid ? "✓" : "✕"} {verification.message}
        </div>
        <div className="block-detail">
          <div>
            <span className="label">블록 #{block.height} · HASH</span>
            <Code>{block.hash}</Code>
          </div>
          <div>
            <span className="label">PREVIOUS HASH</span>
            <Code>{block.previousHash}</Code>
          </div>
          <div>
            <span className="label">거래 목록 해시</span>
            <Code>{block.transactionsHash}</Code>
          </div>
        </div>
        <div className="metrics mini">
          <div>
            <span>Nonce</span>
            <strong>{block.nonce}</strong>
          </div>
          <div>
            <span>난이도</span>
            <strong>{block.difficulty}</strong>
          </div>
          <div>
            <span>후속 블록</span>
            <strong>{node.chain.length - block.height - 1}</strong>
          </div>
        </div>
        {block.height === 0 ? (
          <Notice>
            Genesis는 신뢰하는 실습 시작점입니다. 첫 지갑에 100을 배정하며 PoW와
            일반 거래 검증의 예외입니다.
          </Notice>
        ) : (
          block.transactions.map((tx) => (
            <details key={tx.id}>
              <summary>거래 {short(tx.id)} · 블록에 포함됨</summary>
              <p className="label">거래 ID</p>
              <Code>{tx.id}</Code>
              <p className="label">입력</p>
              {tx.inputs.map((i) => (
                <Code key={`${i.txId}:${i.outputIndex}`}>
                  {i.txId}:{i.outputIndex}
                </Code>
              ))}
              <p className="label">출력</p>
              {tx.outputs.map((o, i) => (
                <div key={i}>
                  <b>{o.amount} 코인</b>
                  <Code>{o.address}</Code>
                </div>
              ))}
              <p className="label">공개키</p>
              <Code>{tx.publicKey}</Code>
              <p className="label">전자서명</p>
              <Code>{tx.signature}</Code>
            </details>
          ))
        )}
      </Panel>
      <Panel title="네트워크 이벤트" eyebrow="LATEST 20 / MAX 200">
        <ol className="event-log">
          {lab.events
            .slice(-20)
            .reverse()
            .map((e) => (
              <li key={e.id}>
                <span className="event-id">
                  {String(e.id).padStart(3, "0")}
                </span>
                <span
                  className={`event-dot ${e.ok ? "" : "failure"}`}
                  aria-hidden="true"
                />
                <strong>{e.node}</strong>
                <div>
                  <span className={e.ok ? "" : "bad-text"}>{e.kind}</span>
                  <small>{e.message}</small>
                </div>
              </li>
            ))}
        </ol>
      </Panel>
      <Panel title="유효하지 않은 블록을 받으면?">
        <p>
          선택 노드의 대기 거래로 후보를 만들되 목표값을 만족하지 않는 실제
          해시를 사용합니다. 노드가 작업증명을 거부하는지 확인하세요.
        </p>
        <button
          className="secondary"
          disabled={!node.mempool.length}
          onClick={() => {
            const candidate = createCandidate(
              node.chain[node.chain.length - 1],
              node.mempool,
            );
            let bad = calculateNonce(candidate, 0);
            while (validPow(bad.hash, bad.difficulty))
              bad = calculateNonce(candidate, bad.nonce + 1);
            lab.receiveBlock(nodeId, bad);
            touch();
          }}
        >
          잘못된 PoW 블록 수신 실험
        </button>
        <Validation value={node.lastValidation} />
      </Panel>
      <Notice>
        세 노드는 브라우저 안의 모의 노드입니다. 실제 인터넷 P2P 통신과 체인
        분기 선택·재구성은 구현하지 않습니다.
      </Notice>
    </>
  );
}
