import { useEffect, useRef, useState } from "react";
import type { LabSnapshot, NodeId } from "../api/types";
import type { TransactionLesson } from "./learning";
import { useInteraction } from "./useInteraction";
import { TransactionTrace } from "./TransactionTrace";
import { Code, Notice, Panel, Validation } from "./shared";
import { errorMessage, type LabActions } from "./useLab";

function PrivateKey({
  address,
  reveal,
}: {
  address: string;
  reveal: LabActions["privateKey"];
}) {
  const [text, setText] = useState("");
  return (
    <details
      className="private-key"
      onToggle={(e) => {
        // 개인키는 실습 상태에 포함되지 않는다. 펼칠 때에만 백엔드에서 가져온다.
        if (!e.currentTarget.open || text) return;
        reveal(address)
          .then((result) => result && setText(result.privateKey))
          .catch((error: unknown) => setText(errorMessage(error)));
      }}
    >
      <summary>교육용 개인키 펼쳐 보기</summary>
      <p>
        백엔드가 보관하며 이 실습의 서명에만 사용합니다. 실제 자산을 보관하는
        지갑이 아닙니다.
      </p>
      <Code>{text || "개인키를 불러오는 중입니다."}</Code>
    </details>
  );
}

export function WalletView({
  lab,
  nodeId,
  addWallet,
  actions,
  lesson,
  setLesson,
  composer,
  setComposer,
  goMempool,
}: {
  lab: LabSnapshot;
  nodeId: NodeId;
  addWallet: () => Promise<void>;
  actions: LabActions;
  lesson: TransactionLesson | null;
  setLesson: (value: TransactionLesson | null) => void;
  composer: { sender: number; receiver: number; amount: string };
  setComposer: (value: {
    sender: number;
    receiver: number;
    amount: string;
  }) => void;
  goMempool: () => void;
}) {
  const { sender, receiver, amount } = composer;
  const interaction = useInteraction();
  const draft = lesson?.draft;
  const signed = lesson?.signed;
  const [error, setError] = useState("");
  // 작성 조건을 바꾸면 값이 달라져, 그 전에 보낸 요청의 늦은 응답을 버릴 수 있다.
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  const node = lab.nodes[nodeId];
  const submitted =
    !!signed &&
    (node.mempool.some((tx) => tx.id === signed.id) ||
      node.chain.some((b) => b.transactions.some((tx) => tx.id === signed.id)));
  function clear() {
    generation.current++;
    setLesson(null);
    setError("");
  }
  async function create() {
    const token = ++generation.current;
    try {
      const result = await actions.draft(
        nodeId,
        lab.wallets[sender].address,
        lab.wallets[receiver].address,
        Number(amount),
      );
      if (!result || token !== generation.current) return;
      setLesson({
        origin: nodeId,
        phase: "draft",
        ...result,
        baseline: { node: nodeId, balances: structuredClone(node.balances) },
      });
      setError("");
    } catch (e) {
      if (token !== generation.current) return;
      setError(errorMessage(e));
      setLesson(null);
    }
  }
  async function sign() {
    if (!lesson) return;
    const token = generation.current;
    try {
      const result = await actions.sign(lesson.draft);
      if (!result || token !== generation.current) return;
      setLesson({
        ...lesson,
        phase: "signed",
        signed: result.signed,
        validation: undefined,
        checked: undefined,
      });
    } catch (e) {
      if (token !== generation.current) return;
      setError(errorMessage(e));
    }
  }
  async function submit(badSignature = false) {
    if (!signed || !lesson) return;
    const token = generation.current;
    try {
      // 잘못된 서명 실험은 백엔드가 서명만 가짜 값으로 바꾼 사본을 만들어 제출한다.
      const forged = badSignature
        ? await actions.forgedSignature(nodeId, signed)
        : null;
      const result = badSignature
        ? forged
        : await actions.submit(nodeId, signed);
      if (!result || token !== generation.current) return;
      setLesson({
        ...lesson,
        origin: nodeId,
        checked: forged?.checked ?? signed,
        phase: "validated",
        validation: result.validation,
        experiment: badSignature ? "잘못된 서명" : undefined,
      });
    } catch (e) {
      if (token !== generation.current) return;
      setError(errorMessage(e));
    }
  }
  async function doubleSpend() {
    const token = ++generation.current;
    try {
      // 서명은 유효하지만 동일 입력을 다른 수신자에게 보내는 별도 거래를 백엔드가 만든다.
      const result = await actions.doubleSpend(nodeId);
      if (!result || token !== generation.current) return;
      setLesson({
        origin: nodeId,
        phase: "validated",
        draft: result.conflict,
        signed: result.conflict,
        checked: result.conflict,
        inputs: result.inputs,
        validation: result.validation,
        experiment: "이중 지출",
        signingText: result.signingText,
        signingHash: result.signingHash,
      });
    } catch (e) {
      if (token !== generation.current) return;
      setError(errorMessage(e));
    }
  }
  return (
    <>
      <div className="section-line">
        <p>
          지갑은 키를 관리합니다. 코인은 노드가 검증한 미사용 출력에 기록됩니다.
        </p>
        <button
          className="secondary"
          disabled={interaction.busy}
          onClick={() => interaction.run("지갑 추가", addWallet)}
        >
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
              <strong>{node.balances[wallet.address]?.confirmed ?? 0}</strong>
              <span>코인</span>
            </div>
            <div className="available">
              송금 가능{" "}
              <strong>{node.balances[wallet.address]?.spendable ?? 0}</strong>
              <span>Node {nodeId} 기준</span>
            </div>
            <Code>{wallet.address}</Code>
            <details>
              <summary>공개키·개인키·UTXO 보기</summary>
              <p className="label">공개키</p>
              <Code>{wallet.publicKey}</Code>
              <PrivateKey
                address={wallet.address}
                reveal={actions.privateKey}
              />
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
          {interaction.busy && (
            <p role="status">{interaction.pending.join(" · ")} 처리 중…</p>
          )}
          <div className="form-grid">
            <label>
              보내는 지갑
              <select
                value={sender}
                onChange={(e) => {
                  setComposer({ ...composer, sender: Number(e.target.value) });
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
                  setComposer({
                    ...composer,
                    receiver: Number(e.target.value),
                  });
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
                setComposer({ ...composer, amount: e.target.value });
                clear();
              }}
            />
            <small>정수 단위 · 수수료 0 · 선택 노드 {nodeId}에 제출</small>
          </label>
          <div className="actions">
            <button
              data-action="draft"
              disabled={interaction.busy}
              onClick={() => interaction.run("거래 작성", create)}
            >
              1. 거래 작성
            </button>
            <button
              className="secondary"
              data-action="sign"
              disabled={interaction.busy || !draft || !!signed}
              onClick={() => interaction.run("전자서명", sign)}
            >
              2. 전자서명
            </button>
            <button
              className="secondary"
              data-action="submit"
              disabled={interaction.busy || !signed || submitted}
              onClick={() => interaction.run("검증·전파", () => submit())}
            >
              3. 검증·전파
            </button>
          </div>
          <p className="action-hint">
            {!draft
              ? "먼저 거래를 작성하면 서명할 수 있습니다."
              : !signed
                ? "전자서명을 마치면 검증·전파할 수 있습니다."
                : submitted
                  ? "이 거래는 이미 제출됐습니다. 새 금액으로 거래를 다시 작성할 수 있습니다."
                  : "서명한 거래를 제출하면 노드가 검증합니다."}
          </p>
          {error && (
            <div role="alert" className="result bad">
              {error}
            </div>
          )}
          {submitted && lesson?.validation?.valid && (
            <div role="status" className="result good">
              ✓ 거래가 승인되었습니다. Mempool 화면에서 확인한 뒤 채굴하세요.
              <button className="secondary" onClick={goMempool}>
                검증 결과 확인 · Mempool로 이동
              </button>
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
          {lesson && (
            <details>
              <summary>서명 원문과 거래 데이터 확인</summary>
              <p className="label">서명 원문 (고정 필드 순서)</p>
              <Code>{lesson.signingText}</Code>
              <p className="label">원문 SHA-256</p>
              <Code>{lesson.signingHash}</Code>
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
        <Panel title="현재 거래의 검증 결과" eyebrow="02 / VERIFY">
          <p>
            현재 관찰 거래의 제출 노드:{" "}
            {lesson?.validation
              ? `Node ${lesson.origin}`
              : "아직 제출하지 않음"}
          </p>
          <Validation value={lesson?.validation ?? null} />
        </Panel>
      </div>
      {lesson && <TransactionTrace lab={lab} nodeId={nodeId} lesson={lesson} />}
      <Panel title="실패도 직접 확인해 보세요" className="experiment-strip">
        <p>
          올바른 노드는 잘못된 거래를 Mempool에 넣지 않습니다. 실패 실험은 원래
          거래를 변경하지 않습니다.
        </p>
        <div className="actions">
          <button
            className="secondary"
            disabled={interaction.busy || !signed}
            onClick={() => interaction.run("서명 실험", () => submit(true))}
          >
            잘못된 서명 실험
          </button>
          <button
            className="secondary"
            disabled={interaction.busy || !node.mempool.length}
            onClick={() => interaction.run("이중 지출 실험", doubleSpend)}
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
