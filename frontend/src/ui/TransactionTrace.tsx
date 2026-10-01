import type { LabSnapshot, NodeId } from "../api/types";
import type { TransactionLesson } from "./learning";
import { lessonTotals } from "./learningInsights";
import { Panel, short } from "./shared";

export function TransactionTrace({
  lab,
  nodeId,
  lesson,
}: {
  lab: LabSnapshot;
  nodeId: NodeId;
  lesson: TransactionLesson;
}) {
  const totals = lessonTotals(lesson);
  const baseline = lesson.baseline;
  const tx = lesson.checked ?? lesson.signed ?? lesson.draft;
  const id = lesson.checked?.id ?? lesson.signed?.id;
  const node = lab.nodes[nodeId];
  const included = node.chain.some((b) =>
    b.transactions.some((t) => t.id === id),
  );
  const pending = node.mempool.some((t) => t.id === id);
  const reserved = new Set(node.reserved);
  const name = (address: string) =>
    lab.wallets.find((w) => w.address === address)?.name ?? short(address);
  return (
    <Panel
      title="입력 하나가 새 출력으로 바뀌는 과정"
      eyebrow="FOLLOW THE UTXO"
      className="transaction-trace"
    >
      <p>
        관찰 노드 {nodeId}의 실제 UTXO·Mempool·블록 포함 상태입니다. 입력 전체를
        사용하고 새 출력을 만듭니다.
      </p>
      <div className="transaction-summary" data-testid="utxo-totals">
        <div>
          <span>입력 합계</span>
          <strong>{totals.input} 코인</strong>
        </div>
        <div>
          <span>수신자에게</span>
          <strong>{totals.sent} 코인</strong>
        </div>
        <div>
          <span>거스름돈</span>
          <strong>{totals.change} 코인</strong>
        </div>
      </div>
      {baseline && (
        <div className="balance-comparison" data-testid="balance-comparison">
          <h3>작성 시점과 지금 비교</h3>
          <p>
            기준: 작성 당시 Node {baseline.node} → 현재 Node {nodeId}. 다른
            거래와 노드의 수신 상태도 현재 잔액에 영향을 줍니다.
          </p>
          <div className="comparison-scroll">
            <table>
              <thead>
                <tr>
                  <th>지갑</th>
                  <th>작성 당시 확정 / 가능</th>
                  <th>현재 확정 / 가능</th>
                </tr>
              </thead>
              <tbody>
                {lab.wallets
                  .filter(
                    (w) =>
                      lesson.inputs.some((i) => i.address === w.address) ||
                      tx.outputs.some((o) => o.address === w.address),
                  )
                  .map((w) => (
                    <tr key={w.address}>
                      <th>{w.name}</th>
                      <td>
                        {baseline.balances[w.address]?.confirmed ?? 0} /{" "}
                        {baseline.balances[w.address]?.spendable ?? 0}
                      </td>
                      <td
                        key={`${node.balances[w.address]?.confirmed}-${node.balances[w.address]?.spendable}`}
                        className="change-flash"
                      >
                        {node.balances[w.address]?.confirmed ?? 0} /{" "}
                        {node.balances[w.address]?.spendable ?? 0}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <p>
            {pending
              ? "◷ 아직 블록 포함 전입니다. 입력 전체가 예약되어 확정 잔액과 송금 가능액이 다를 수 있습니다."
              : included
                ? "✓ 블록에 포함돼 입력은 소비되고 새 출력이 생겼습니다. 새 출력도 이후 거래에서 소비될 수 있습니다."
                : "○ 이 노드에 선택 거래가 등록되지 않았습니다. 작성만으로 잔액이 바뀌지는 않습니다."}
          </p>
        </div>
      )}
      <div className="utxo-flow">
        <div>
          <h3>이전 출력 → 이번 거래의 입력</h3>
          {lesson.inputs.map((input) => {
            const key = `${input.txId}:${input.outputIndex}`;
            const state = !node.utxos[key]
              ? "소비됨"
              : reserved.has(key)
                ? "대기 거래에 예약됨"
                : "미사용";
            return (
              <div className="trace-card" key={key}>
                <strong>
                  {name(input.address)} · {input.amount} 코인
                </strong>
                <span data-testid="input-state">{state}</span>
                <code>
                  {short(input.txId, 8)}:{input.outputIndex}
                </code>
              </div>
            );
          })}
        </div>
        <div className="trace-arrow" aria-hidden="true">
          →
        </div>
        <div>
          <h3>수신자 출력 + 거스름돈 출력</h3>
          {tx.outputs.map((output, index) => (
            <div className="trace-card" key={index}>
              <strong>
                {name(output.address)} · {output.amount} 코인{" "}
                {index > 0 ? "(거스름돈)" : "(수신자)"}
              </strong>
              <span data-testid="output-state">
                {included
                  ? node.utxos[`${id}:${index}`]
                    ? "블록 포함 · 사용 가능"
                    : "블록 포함 후 소비됨"
                  : pending
                    ? "대기 중 · 아직 사용 불가"
                    : "작성된 출력 · 미등록"}
              </span>
            </div>
          ))}
        </div>
      </div>
      <p className="trace-explanation">
        작성 전: 기존 출력 사용 가능 → Mempool: 입력 전체 예약·새 출력 사용 불가
        → 블록 포함: 입력 소비·새 출력 생성. 이 모델은 미확정 거스름돈을 다시
        사용하지 않습니다.
      </p>
    </Panel>
  );
}
