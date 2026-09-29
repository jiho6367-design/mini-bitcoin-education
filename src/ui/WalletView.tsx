import { useState } from "react";
import {
  hashText,
  signTransaction,
  signingText,
  transactionId,
} from "../core/crypto";
import { balance, buildTransaction, reservedInputs } from "../core/transaction";
import type { Lab } from "../core/lab";
import type { NodeId, Transaction, UnsignedTransaction } from "../core/types";
import { Code, Notice, Panel, Validation } from "./shared";

export function WalletView({
  lab,
  nodeId,
  addWallet,
  touch,
}: {
  lab: Lab;
  nodeId: NodeId;
  addWallet: () => void;
  touch: () => void;
}) {
  const [sender, setSender] = useState(0);
  const [receiver, setReceiver] = useState(1);
  const [amount, setAmount] = useState("30");
  const [draft, setDraft] = useState<UnsignedTransaction | null>(null);
  const [signed, setSigned] = useState<Transaction | null>(null);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const node = lab.nodes[nodeId];
  function clear() {
    setDraft(null);
    setSigned(null);
    setSubmitted(false);
    setError("");
  }
  function create() {
    try {
      setDraft(
        buildTransaction(
          lab.wallets[sender],
          lab.wallets[receiver].address,
          Number(amount),
          node.utxos,
          node.mempool,
          lab.sessionId,
        ),
      );
      setSigned(null);
      setSubmitted(false);
      setError("");
    } catch (e) {
      setError((e as Error).message);
      setDraft(null);
      setSigned(null);
    }
  }
  function submit(badSignature = false) {
    if (!signed) return;
    const tx = structuredClone(signed);
    if (badSignature) {
      tx.signature = "ff".repeat(64);
      tx.id = transactionId(tx);
    }
    const validation = lab.submit(nodeId, tx);
    touch();
    if (!badSignature && validation.valid) setSubmitted(true);
  }
  return (
    <>
      <div className="section-line">
        <p>
          지갑은 키를 관리합니다. 코인은 노드가 검증한 미사용 출력에 기록됩니다.
        </p>
        <button className="secondary" onClick={addWallet}>
          ＋ 지갑 추가
        </button>
      </div>
      <div className="wallet-grid">
        {lab.wallets.map((wallet, index) => (
          <Panel key={wallet.address} className="wallet-card">
            <div className="wallet-top">
              <span className={`avatar avatar-${index % 3}`}>
                {wallet.name[0]}
              </span>
              <div>
                <h2>{wallet.name}</h2>
                <span className="muted">교육용 지갑</span>
              </div>
              <span className="tag">KEY PAIR</span>
            </div>
            <div className="balance" data-testid={`balance-${index}`}>
              <strong>{balance(node.utxos, wallet.address)}</strong>
              <span>코인</span>
            </div>
            <div className="available">
              송금 가능{" "}
              <strong>
                {balance(
                  node.utxos,
                  wallet.address,
                  reservedInputs(node.mempool),
                )}
              </strong>
              <span>Node {nodeId} 기준</span>
            </div>
            <Code>{wallet.address}</Code>
            <details>
              <summary>공개키·개인키·UTXO 보기</summary>
              <p className="label">공개키</p>
              <Code>{wallet.publicKey}</Code>
              <details className="private-key">
                <summary>교육용 개인키 펼쳐 보기</summary>
                <p>
                  이 실습에서 서명에만 사용합니다. 실제 자산을 보관하는 지갑이
                  아닙니다.
                </p>
                <Code>{wallet.privateKey}</Code>
              </details>
              <p className="label">확정 UTXO</p>
              {Object.values(node.utxos)
                .filter((u) => u.address === wallet.address)
                .map((u) => (
                  <div className="utxo" key={`${u.txId}:${u.outputIndex}`}>
                    <b>{u.amount} 코인</b>
                    <Code>
                      {u.txId}:{u.outputIndex}
                    </Code>
                  </div>
                ))}
            </details>
          </Panel>
        ))}
      </div>
      <div className="two-col">
        <Panel title="거래를 만들어 보세요" eyebrow="01 / TRANSACTION">
          <div className="form-grid">
            <label>
              보내는 지갑
              <select
                value={sender}
                onChange={(e) => {
                  setSender(Number(e.target.value));
                  clear();
                }}
              >
                {lab.wallets.map((w, i) => (
                  <option value={i} key={w.address}>
                    {w.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              받는 지갑
              <select
                value={receiver}
                onChange={(e) => {
                  setReceiver(Number(e.target.value));
                  clear();
                }}
              >
                {lab.wallets.map((w, i) => (
                  <option value={i} key={w.address}>
                    {w.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            송금액
            <input
              type="number"
              min="1"
              step="1"
              value={amount}
              onChange={(e) => {
                setAmount(e.target.value);
                clear();
              }}
            />
            <small>정수 단위 · 수수료 0 · 선택 노드 {nodeId}에 제출</small>
          </label>
          <div className="actions">
            <button onClick={create}>1. 거래 작성</button>
            <button
              className="secondary"
              disabled={!draft || !!signed}
              onClick={() => {
                if (draft)
                  setSigned(
                    signTransaction(draft, lab.wallets[sender].privateKey),
                  );
              }}
            >
              2. 전자서명
            </button>
            <button
              className="secondary"
              disabled={!signed || submitted}
              onClick={() => submit()}
            >
              3. 검증·전파
            </button>
          </div>
          {error && (
            <div role="alert" className="result bad">
              {error}
            </div>
          )}
          {submitted && (
            <div role="status" className="result good">
              ✓ 거래가 승인되었습니다. Mempool 화면에서 확인한 뒤 채굴하세요.
            </div>
          )}
          {draft && (
            <div className="transaction-summary">
              <div>
                <span>사용하는 입력</span>
                <strong>{draft.inputs.length}개</strong>
              </div>
              <div>
                <span>수신자 출력</span>
                <strong>{draft.outputs[0].amount} 코인</strong>
              </div>
              <div>
                <span>거스름돈</span>
                <strong>{draft.outputs[1]?.amount ?? 0} 코인</strong>
              </div>
            </div>
          )}
          {draft && (
            <details>
              <summary>서명 원문과 거래 데이터 확인</summary>
              <p className="label">서명 원문 (고정 필드 순서)</p>
              <Code>{signingText(draft)}</Code>
              <p className="label">원문 SHA-256</p>
              <Code>{hashText(signingText(draft))}</Code>
              <p>
                서명은 거래를 암호화하지 않습니다. 공개키로 작성 권한과 변경
                여부를 확인합니다.
              </p>
              {signed && (
                <>
                  <p className="label">전자서명</p>
                  <Code>{signed.signature}</Code>
                  <p className="label">거래 ID</p>
                  <Code>{signed.id}</Code>
                </>
              )}
            </details>
          )}
        </Panel>
        <Panel title={`Node ${nodeId}의 검증 결과`} eyebrow="02 / VERIFY">
          <Validation value={node.lastValidation} />
        </Panel>
      </div>
      <Panel title="실패도 직접 확인해 보세요" className="experiment-strip">
        <p>
          올바른 노드는 잘못된 거래를 Mempool에 넣지 않습니다. 실패 실험은 원래
          거래를 변경하지 않습니다.
        </p>
        <div className="actions">
          <button
            className="secondary"
            disabled={!signed}
            onClick={() => submit(true)}
          >
            잘못된 서명 실험
          </button>
          <button
            className="secondary"
            disabled={!node.mempool.length}
            onClick={() => {
              const original = structuredClone(node.mempool[0]);
              // 서명은 유효하지만 동일 입력을 다른 수신자에게 보내는 별도 거래를 만든다.
              original.outputs[0].address =
                original.outputs[0].address === lab.wallets[0].address
                  ? lab.wallets[1].address
                  : lab.wallets[0].address;
              const owner = lab.wallets.find(
                (w) => w.publicKey === original.publicKey,
              )!;
              lab.submit(nodeId, signTransaction(original, owner.privateKey));
              touch();
            }}
          >
            이중 지출 실험
          </button>
        </div>
      </Panel>
      <Notice>
        Genesis의 100 코인은 초기 학습용 배정입니다. 채굴 보상이 아닙니다. 확정
        전 거스름돈과 이미 예약된 입력은 이번 모델에서 다시 사용할 수 없습니다.
      </Notice>
    </>
  );
}
