import { useState } from "react";
import { NODE_IDS, type DraftResult, type Transaction } from "../api/types";
import { type RoomState, useClassroom } from "./useClassroom";
import { collectGuideTransactions, deriveGuideProgress, pickPendingGuideTransaction } from "./teamGuideProgress";
import { walletAvailability } from "./walletAvailability";
import type { GuidePage } from "./TeamPracticeGuide";

export function TeamEasyStart({ room, participantId, selected, observe, command, navigate, roles, disabled, blocked }: {
  room: RoomState; participantId: string; selected: string | null;
  observe: (id: string | null) => void;
  command: ReturnType<typeof useClassroom>["command"];
  navigate: (page: GuidePage) => void; roles: () => void;
  disabled: boolean; blocked: string;
}) {
  const me = room.participants.find((person) => person.id === participantId);
  const team = me?.team;
  const wallet = team ? room.lab.wallets[NODE_IDS.indexOf(team)] : null;
  const balance = team && wallet ? walletAvailability(room.lab.nodes[team], wallet.address) : null;
  const available = balance?.spendable || 0;
  const [amount, setAmount] = useState(String(Math.min(10, Math.max(1, available - 1))));
  const [fee, setFee] = useState(available <= 1 ? "0" : "1");
  const [recipient, setRecipient] = useState(room.lab.wallets[team === "A" ? 1 : 0].address);
  const [draft, setDraft] = useState<DraftResult | null>(null);
  const [signed, setSigned] = useState<Transaction | null>(null);
  const [validation, setValidation] = useState("");
  const progress = deriveGuideProgress(room, selected);
  const target = collectGuideTransactions(room).find((transaction) => transaction.id === selected);
  const pending = pickPendingGuideTransaction(room);
  const race = room.lab.race;
  let title: string, explanation: string;
  let label = "";
  let action: (() => void | Promise<unknown>) | null = null;
  let showForm = false;
  const clear = () => { setDraft(null); setSigned(null); setValidation(""); };
  if (!team) {
    const free = NODE_IDS.find((node) => !room.participants.some((person) => person.team === node));
    title = "먼저 내 팀 하나를 선택하세요";
    explanation = "혼자라면 A만 맡으면 됩니다 B·C 노드는 서버에서 자동으로 검증하므로 다른 사람이 없어도 됩니다";
    label = free ? `팀 ${free}로 시작` : "팀 선택 확인";
    action = free ? () => command("claim", { team: free }) : roles;
  } else if (!room.automatic) {
    title = "노드 사이의 자동 전달을 켜세요";
    explanation = "한 번 켜 두면 거래와 블록을 A·B·C에 자동으로 전달합니다";
    label = "자동 전달 켜기"; action = () => command("automatic", { enabled: true });
  } else if (progress.completeCount === 6) {
    title = "송금과 채굴을 완료했습니다";
    explanation = "A·B·C가 같은 거래와 블록을 검증했습니다 결과 화면에서 바뀐 잔액을 확인하세요";
    label = "결과 보기"; action = () => navigate("거래");
  } else if (!target && pending) {
    title = "이미 보내 둔 거래를 이어가세요";
    explanation = "대기 거래가 코인을 예약하고 있습니다 새 송금을 만들지 않고 이 거래를 채굴하면 됩니다";
    label = "대기 거래 이어서 보기"; action = () => { clear(); observe(pending); };
  } else if (!target && available <= 0) {
    title = "현재 팀의 사용 가능한 코인을 확인하세요";
    explanation = balance?.reserved ? "대기 거래가 코인을 예약하고 있습니다 거래 화면에서 대기 거래를 선택해 이어가세요" : "다른 비어 있는 팀을 맡거나 새 실습을 만들어 시작할 수 있습니다 기존 기록은 유지됩니다";
    label = balance?.reserved ? "거래 화면으로 이동" : "다른 팀 선택";
    action = balance?.reserved ? () => navigate("거래") : roles;
  } else if (!target) {
    showForm = true;
    title = signed ? "서명한 거래를 보내세요" : draft ? "송금 내용을 확인하고 서명하세요" : "다른 팀으로 코인을 보내 보세요";
    explanation = signed ? "보내기를 눌러야 노드가 거래를 검증합니다 아직 코인은 예약되지 않았습니다"
      : draft ? `입력 ${draft.inputs.reduce((sum, input) => sum + input.amount, 0)}코인에서 송금 ${amount} · 수수료 ${fee}를 빼고 나머지는 거스름돈으로 돌아옵니다`
        : `내 팀 ${team}의 사용 가능액은 ${available}코인입니다 받는 팀이 접속하지 않아도 송금할 수 있습니다`;
    label = signed ? "거래 보내기" : draft ? "전자서명하기" : "송금 내용 확인";
    action = async () => {
      if (signed) {
        const result = await command("submit", { transactionId: signed.id });
        if (result?.validation?.valid) { observe(signed.id); clear(); }
        else if (result?.validation) setValidation(result.validation.message);
      } else if (draft) {
        const result = await command("sign", { draftId: draft.signingHash });
        if (result?.signed) setSigned(result.signed);
      } else {
        const result = await command("draft", { senderAddress: wallet!.address, recipientAddress: recipient, amount: Number(amount), fee: Number(fee) });
        if (result?.draft) setDraft(result as DraftResult);
      }
    };
  } else if (progress.nodes.some((node) => node.status === "invalid" || node.status === "conflict")) {
    title = "거래와 노드의 검증 상태를 확인하세요";
    explanation = progress.nextAction;
    label = "노드 상태 확인"; action = () => navigate("노드·블록체인");
  } else if (!progress.steps[3].complete) {
    title = "다른 노드에 거래를 전달 중입니다";
    explanation = room.lab.queue.length ? "자동 전달이 켜져 있습니다 세 노드가 거래를 받으면 다음 버튼이 나타납니다"
      : "전달 대기 메시지가 없습니다 노드별 수신·검증 상태를 확인하고 이어갈 거래를 다시 선택하세요";
    label = "전파 상태 확인"; action = () => navigate("노드·블록체인");
  } else if (!progress.steps[4].complete) {
    const job = race?.jobs[team];
    const ready = race?.singleNode === team && race.status !== "stopped" && job
      && ["paused", "running"].includes(job.status) && job.block.transactions.some((tx) => tx.id === selected);
    if (!ready) {
      title = "이 거래를 담은 채굴 후보를 준비하세요";
      explanation = `노드 ${team} 하나로 빠르게 계산합니다 다른 팀의 참여 버튼은 누를 필요가 없습니다`;
      label = "채굴 준비";
      action = () => command("prepare", { raceId: race?.id, singleNode: team, mode: "fast", guided: true });
    } else if (race.status === "running") {
      title = "블록을 찾는 중입니다";
      explanation = `노드 ${team}가 실제 해시를 계산하고 있습니다 현재 ${job.attempts}회 시도했습니다 첫 발견 후 자동으로 멈춥니다`;
    } else {
      title = "준비됐습니다 채굴을 시작하세요";
      explanation = `노드 ${team}의 후보에 선택한 거래가 포함돼 있습니다`;
      label = "채굴 시작"; action = () => command("resume", { raceId: race.id });
    }
  } else {
    title = "찾은 블록을 다른 노드가 검증 중입니다";
    explanation = progress.nextAction;
    label = progress.nextDestination === "채굴" ? "채굴 상태 확인" : "검증 상태 확인";
    action = () => navigate(progress.nextDestination === "채굴" ? "채굴" : "노드·블록체인");
  }
  return <section className="panel team-easy-start" data-testid="team-easy-start" aria-labelledby="team-easy-title">
    <div className="team-easy-heading"><h2 id="team-easy-title">쉬운 실습</h2><span>실제 완료 {progress.completeCount} / 6</span></div>
    <h3>{title}</h3><p>{explanation}</p>
    {showForm ? <form className="team-easy-form" onSubmit={(event) => { event.preventDefault(); if (!disabled) void action?.(); }}>
      <fieldset disabled={disabled}>
        <label>연습 받는 팀<select value={recipient} onChange={(event) => { setRecipient(event.target.value); clear(); }}>
          {room.lab.wallets.filter((other) => other.address !== wallet?.address).map((other) => <option key={other.address} value={other.address}>{other.name}</option>)}
        </select></label>
        <label>연습 송금액<input type="number" required min={1} step={1} value={amount} onChange={(event) => { setAmount(event.target.value); clear(); }} /></label>
        <label>연습 수수료<input type="number" required min={0} step={1} value={fee} onChange={(event) => { setFee(event.target.value); clear(); }} /></label>
        <button>{label}</button>
      </fieldset>
    </form> : action ? <button disabled={disabled} onClick={() => void action?.()}>{label}</button>
      : <p role="status" className="team-easy-wait">진행 중 · 상태가 갱신되면 다음 할 일을 자동으로 표시합니다</p>}
    {disabled && <p role="status" className="team-easy-blocked">{blocked}</p>}
    {progress.completeCount === 6 && <button className="secondary" disabled={disabled} onClick={() => { clear(); observe(pending); }}>
      {pending ? "다른 대기 거래 이어가기" : "다음 송금 연습"}
    </button>}
    {validation && <p role="alert">{validation}</p>}
    <p className="team-easy-footnote">혼자 연습해도 됩니다 버튼은 한 번씩 순서대로 누르세요 팀원과 할 때는 한 사람이 공통 버튼을 진행하세요</p>
  </section>;
}
