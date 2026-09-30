import type { Lab } from "../core/lab";
import { outpoint, reservedInputs } from "../core/transaction";
import type { NodeId } from "../core/types";
import type { TransactionLesson } from "./learning";
import { Panel, short } from "./shared";

export function TransactionTrace({
  lab,
  nodeId,
  lesson,
}: {
  lab: Lab;
  nodeId: NodeId;
  lesson: TransactionLesson;
}) {
  const tx = lesson.checked ?? lesson.signed ?? lesson.draft;
  const id = lesson.checked?.id ?? lesson.signed?.id;
  const node = lab.nodes[nodeId];
  const included = node.chain.some((b) =>
    b.transactions.some((t) => t.id === id),
  );
  const pending = node.mempool.some((t) => t.id === id);
  const reserved = reservedInputs(node.mempool);
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
      <div className="utxo-flow">
        <div>
          <h3>이전 출력 → 이번 거래의 입력</h3>
          {lesson.inputs.map((input) => {
            const key = outpoint(input);
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
