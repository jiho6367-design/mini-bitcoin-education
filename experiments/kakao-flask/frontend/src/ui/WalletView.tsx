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
        // 개인키는 실습 상태에 포함되지 않는다 펼칠 때에만 백엔드에서 가져온다
        if (!e.currentTarget.open || text) return;
        reveal(address)
          .then((result) => result && setText(result.privateKey))
          .catch((error: unknown) => setText(errorMessage(error)));
      }}
    >
      <summary>교육용 개인키 펼쳐 보기</summary>
      <p>
        백엔드가 보관하며 이 실습의 서명에만 사용합니다 실제 자산을 보관하는
        지갑이 아닙니다
      </p>
      <Code>{text || "개인키를 불러오는 중입니다"}</Code>
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
  composer: { sender: string; receiver: string; amount: string; fee: string };
  setComposer: (value: {
    sender: string;
    receiver: string;
    amount: string;
    fee: string;
  }) => void;
  goMempool: () => void;
}) {
  const { amount, fee } = composer;
  const [manualInputs, setManualInputs] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [showMiners, setShowMiners] = useState(false);
  const wallets = [...lab.wallets, ...Object.values(lab.minerWallets ?? {})];
  const sender = composer.sender || lab.wallets[0]?.address;
  const receiver = composer.receiver || lab.wallets[1]?.address;
  const interaction = useInteraction();
  const draft = lesson?.draft;
  const signed = lesson?.signed;
  const [error, setError] = useState("");
  // 작성 조건을 바꾸면 값이 달라져, 그 전에 보낸 요청의 늦은 응답을 버릴 수 있다
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
        sender,
        receiver,
        Number(amount),
        Number(fee),
        manualInputs ? selected : undefined,
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
      // 잘못된 서명 실험은 백엔드가 서명만 가짜 값으로 바꾼 사본을 만들어 제출한다
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
      // 서명은 유효하지만 동일 입력을 다른 수신자에게 보내는 별도 거래를 백엔드가 만든다
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
  const available = Object.entries(node.utxos).filter(
    ([key, u]) =>
      u.address === sender &&
      !node.reserved.includes(key) &&
      (!u.coinbase ||
        node.chain.at(-1)!.height + 1 - (u.createdHeight ?? 0) >= 100),
  );
  const inputTotal = (
    manualInputs ? available.filter(([k]) => selected.includes(k)) : available
  ).reduce((sum, [, u]) => sum + u.amount, 0);
  return (
    <>
      <div className="section-line">
        <p>
          지갑은 키를 관리합니다 코인은 노드가 검증한 미사용 출력에 기록됩니다
        </p>
        <button
          className="secondary"
          disabled={interaction.busy}
          onClick={() => interaction.run("지갑 추가", addWallet)}
        >
          ＋ 지갑 추가
        </button>
      </div>
      <button
        className="text-button"
        onClick={() => setShowMiners(!showMiners)}
      >
        {showMiners ? "채굴자 보상 지갑 접기" : "채굴자 보상 지갑 펼치기"}
      </button>
      <div className="wallet-grid">
        {wallets
          .filter((_, index) => index < lab.wallets.length || showMiners)
          .map((wallet, index) => (
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
                  setComposer({ ...composer, sender: e.target.value });
                  setSelected([]);
                  clear();
                }}
              >
                {wallets.map((w) => (
                  <option value={w.address} key={w.address}>
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
                    receiver: e.target.value,
                  });
                  clear();
                }}
              >
                {wallets.map((w) => (
                  <option value={w.address} key={w.address}>
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
            <small>교육용 정수 단위 · 선택 노드 {nodeId}에 제출</small>
          </label>
          <label>
            수수료
            <input
              aria-label="수수료"
              type="number"
              min="0"
              step="1"
              value={fee}
              onChange={(e) => {
                setComposer({ ...composer, fee: e.target.value });
                clear();
              }}
            />
          </label>
          <div className="input-picker">
            <label>
              <input
                type="checkbox"
                checked={manualInputs}
                onChange={(e) => {
                  setManualInputs(e.target.checked);
                  setSelected([]);
                  clear();
                }}
              />{" "}
              UTXO 직접 선택
            </label>
            <p>
              UTXO는 아직 쓰지 않은 출력입니다 TXID와 출력 번호로 식별하며,
              금액과 소유 조건은 별도 정보입니다
            </p>
            {available.map(([key, u]) => (
              <label className="utxo-choice" key={key}>
                {manualInputs && (
                  <input
                    type="checkbox"
                    checked={selected.includes(key)}
                    onChange={(e) => {
                      setSelected(
                        e.target.checked
                          ? [...selected, key]
                          : selected.filter((k) => k !== key),
                      );
                      clear();
                    }}
                  />
                )}
                <span>
                  <strong>{u.amount} 코인</strong> · 출력 #{u.outputIndex} ·{" "}
                  {u.coinbase ? "성숙한 채굴 보상" : "미사용"}
                  <small>
                    TXID {u.txId.slice(0, 10)}… · 사용 조건: 이 지갑의 유효한
                    서명
                  </small>
                </span>
              </label>
            ))}
            {!available.length && (
              <p>
                사용 가능한 출력이 없습니다 예약 상태·노드 수신 상태·보상
                성숙을 확인하세요
              </p>
            )}
            <p>
              {manualInputs ? "선택 합계" : "자동 선택 가능 합계"} {inputTotal}{" "}
              · 보낼 금액 {amount || 0} + 수수료 {fee || 0}. 실제 입력과
              거스름돈은 작성 결과에서 확인하세요
            </p>
          </div>
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
              ? "먼저 거래를 작성하면 서명할 수 있습니다"
              : !signed
                ? "전자서명을 마치면 검증·전파할 수 있습니다"
                : submitted
                  ? "이 거래는 이미 제출됐습니다 새 금액으로 거래를 다시 작성할 수 있습니다"
                  : "서명한 거래를 제출하면 노드가 검증합니다"}
          </p>
          {error && (
            <div role="alert" className="result bad">
              {error}
            </div>
          )}
          {submitted && lesson?.validation?.valid && (
            <div role="status" className="result good">
              ✓ 거래가 승인되었습니다 Mempool 화면에서 확인한 뒤 채굴하세요
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
              {lesson.restored && <p>저장된 거래를 관찰 중입니다 제출 전 작성 폼과 당시 서명 화면은 복원하지 않습니다 새 송금은 거래를 다시 작성하세요</p>}
              <p className="label">서명 원문 (고정 필드 순서)</p>
              <Code>{lesson.signingText || "이 기록에는 서명 원문 표시 정보가 없습니다"}</Code>
              <p className="label">원문 SHA-256</p>
              <Code>{lesson.signingHash || "서명된 거래 ID와 전자서명을 아래에서 확인하세요"}</Code>
              <p>
                서명은 거래를 암호화하지 않습니다 공개키로 작성 권한과 변경
                여부를 확인합니다
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
        <Panel title="서명과 출력의 변화" eyebrow="02 / SIGN & VERIFY">
          {lesson && (
            <div className="signature-story" aria-label="서명 과정">
              <div>
                <b>① 승인할 내용</b>
                <p>
                  입력 {draft?.inputs.length}개 → 송금{" "}
                  {draft?.outputs[0]?.amount} · 거스름돈{" "}
                  {draft?.outputs[1]?.amount ?? 0}
                </p>
              </div>
              <span aria-hidden="true">↓</span>
              <div>
                <b>② 지갑의 개인키로 서명</b>
                <p>
                  {signed
                    ? "✓ 이 내용에 대한 서명 생성 완료"
                    : "전자서명 버튼을 눌러 승인하세요"}
                </p>
              </div>
              <span aria-hidden="true">↓</span>
              <div>
                <b>③ 노드가 공개키로 검증</b>
                <p>
                  {lesson.validation
                    ? lesson.validation.valid
                      ? "✓ 내용·서명·소유권 검사 통과"
                      : "× 검사 실패 — 결과를 확인하세요"
                    : "아직 검증하지 않았습니다"}
                </p>
              </div>
              <small>
                서명은 암호화가 아닙니다 이 실습에서는 교육용 키를 서버가
                보관하고 서명합니다
              </small>
            </div>
          )}
          {lesson && (
            <TransactionTrace lab={lab} nodeId={nodeId} lesson={lesson} />
          )}
          <h3>현재 거래의 검증 결과</h3>
          <p>
            현재 관찰 거래의 제출 노드:{" "}
            {lesson?.validation
              ? `Node ${lesson.origin}`
              : "아직 제출하지 않음"}
          </p>
          <Validation value={lesson?.validation ?? null} />
        </Panel>
      </div>
      <Panel title="실패도 직접 확인해 보세요" className="experiment-strip">
        <p>
          올바른 노드는 잘못된 거래를 Mempool에 넣지 않습니다 실패 실험은 원래
          거래를 변경하지 않습니다
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
        Genesis의 100 코인은 초기 학습용 배정입니다 채굴 보상이 아닙니다 확정
        전 거스름돈과 이미 예약된 입력은 이번 모델에서 다시 사용할 수 없습니다
      </Notice>
    </>
  );
}
