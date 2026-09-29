import { useState } from "react";
import {
  blockHash,
  transactionDigest,
  validateChain,
  validPow,
} from "../core/block";
import { transactionId, verifySignature } from "../core/crypto";
import type { Lab } from "../core/lab";
import type { Block, NodeId } from "../core/types";
import { Code, Notice, Panel, Validation } from "./shared";

export function TamperView({ lab, nodeId }: { lab: Lab; nodeId: NodeId }) {
  const [copy, setCopy] = useState<Block[] | null>(null);
  const [height, setHeight] = useState(1);
  const [amount, setAmount] = useState("999");
  const [mode, setMode] = useState<"transaction" | "header">("transaction");
  const chain = lab.nodes[nodeId].chain;
  const h = Math.min(height, chain.length - 1);
  const original = chain[h];
  const altered = copy?.[h];
  const tx = altered?.transactions[0];
  function change() {
    const next = structuredClone(chain);
    if (mode === "transaction")
      next[h].transactions[0].outputs[0].amount = Number(amount);
    else next[h].timestamp += 1;
    setCopy(next);
  }
  const checks = altered
    ? ([
        ["거래 전자서명", tx ? verifySignature(tx) : true],
        ["거래 ID 일치", tx ? tx.id === transactionId(tx) : true],
        [
          "거래 목록 해시",
          altered.transactionsHash === transactionDigest(altered.transactions),
        ],
        ["헤더와 블록 해시 일치", altered.hash === blockHash(altered)],
        [
          "현재 헤더의 작업증명",
          validPow(blockHash(altered), altered.difficulty),
        ],
        ...(copy && copy[h + 1]
          ? [
              [
                "다음 블록의 Previous Hash 연결",
                copy[h + 1].previousHash === blockHash(altered),
              ],
            ]
          : []),
      ] as [string, boolean][])
    : [];
  return (
    <>
      <Notice>
        정상 체인은 보존하고 복사본만 수정합니다. 개별 검사와 체인 전체 검증을
        함께 보며, 첫 실패 이후에도 어떤 관계가 깨지는지 확인하세요.
      </Notice>
      {chain.length < 2 ? (
        <Panel title="먼저 거래가 담긴 블록을 채굴하세요">
          <p>
            과거 블록의 변경을 관찰하려면 Genesis 다음에 최소 한 블록이
            필요합니다. 두 블록 이상 채굴하면 다음 블록과의 연결 실패도 확인할
            수 있습니다.
          </p>
        </Panel>
      ) : (
        <>
          <Panel title="변조 실험 설정" eyebrow="TAMPER LAB">
            <div className="form-grid">
              <label>
                변조할 블록
                <select
                  value={h}
                  onChange={(e) => {
                    setHeight(Number(e.target.value));
                    setCopy(null);
                  }}
                >
                  {chain.slice(1).map((b) => (
                    <option value={b.height} key={b.height}>
                      블록 #{b.height}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                변경할 데이터
                <select
                  value={mode}
                  onChange={(e) => {
                    setMode(e.target.value as "transaction" | "header");
                    setCopy(null);
                  }}
                >
                  <option value="transaction">첫 거래의 송금액</option>
                  <option value="header">블록 시각 +1ms</option>
                </select>
              </label>
              {mode === "transaction" && (
                <label>
                  변경 금액
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                  />
                </label>
              )}
            </div>
            <div className="actions">
              <button
                disabled={
                  mode === "transaction" &&
                  (!Number.isSafeInteger(Number(amount)) ||
                    Number(amount) <= 0 ||
                    Number(amount) ===
                      original.transactions[0].outputs[0].amount)
                }
                onClick={change}
              >
                복사본 변조·검증
              </button>
              <button
                className="secondary"
                disabled={!copy}
                onClick={() => {
                  if (!copy) return;
                  const next = structuredClone(copy);
                  next[h].transactionsHash = transactionDigest(
                    next[h].transactions,
                  );
                  next[h].hash = blockHash(next[h]);
                  setCopy(next);
                }}
              >
                해시만 재계산
              </button>
              <button
                className="secondary"
                disabled={!copy}
                onClick={() => setCopy(null)}
              >
                복사본 지우기
              </button>
            </div>
          </Panel>
          <div className="two-col">
            <Panel title="원본 · 정상 체인">
              <div className="result good">✓ 원본 검증 통과</div>
              <p>
                첫 출력 금액 <b>{original.transactions[0].outputs[0].amount}</b>{" "}
                코인
              </p>
              <p className="label">블록 해시</p>
              <Code>{original.hash}</Code>
              <p className="label">시각</p>
              <Code>{original.timestamp}</Code>
            </Panel>
            <Panel title="실험용 복사본">
              {copy && altered ? (
                <>
                  <p>
                    첫 출력 금액{" "}
                    <b>{altered.transactions[0].outputs[0].amount}</b> 코인
                  </p>
                  <p className="label">저장된 블록 해시</p>
                  <Code>{altered.hash}</Code>
                  <p className="label">실제 헤더를 다시 해시한 값</p>
                  <Code>{blockHash(altered)}</Code>
                  <Validation value={validateChain(copy, lab.genesis)} />
                </>
              ) : (
                <p className="empty">
                  데이터를 바꾸고 검증 결과를 비교해 보세요.
                </p>
              )}
            </Panel>
          </div>
          {altered && (
            <Panel title="어떤 관계가 깨졌을까요?">
              <ul className="diagnostics">
                {checks.map(([label, ok]) => (
                  <li key={label} className={ok ? "good-text" : "bad-text"}>
                    <strong>{ok ? "✓ 일치" : "✕ 실패"}</strong>
                    <span>{label}</span>
                  </li>
                ))}
              </ul>
              {!copy?.[h + 1] && (
                <p className="muted">
                  아직 후속 블록이 없습니다. 블록을 하나 더 채굴한 뒤 이전
                  블록을 수정하면 연결 실패를 볼 수 있습니다.
                </p>
              )}
            </Panel>
          )}
        </>
      )}
      <Panel title="변조 탐지는 왜 가능한가요?" eyebrow="WHAT THIS MEANS">
        <p>
          거래 변경은 서명을 깨뜨립니다. 헤더 변경은 블록 해시를 바꿉니다. 그
          해시를 고쳐도 작업증명을 다시 만족시켜야 하며, 다음 블록의 연결도
          고쳐야 합니다.
        </p>
        <p>
          데이터를 수정하는 행위 자체가 불가능한 것은 아닙니다. 실제 Bitcoin은
          노드의 검증과 누적 작업량을 통한 체인 선택으로 과거 기록의 변경을
          어렵게 만듭니다. 이 실습은 체인 분기 경쟁과 재구성을 다루지 않습니다.
        </p>
      </Panel>
    </>
  );
}
