import { useState } from "react";
import {
  NODE_IDS,
  type LabSnapshot,
  type MiningMode,
  type RaceRequest,
} from "../api/types";
import { Panel, Code, Notice, short } from "./shared";
import { useInteraction } from "./useInteraction";
import { uiText } from "../copy";

export function RaceView({
  lab,
  guided,
  act,
  deliver,
  goNetwork,
  setAutomatic,
}: {
  lab: LabSnapshot;
  guided: boolean;
  act: (body: RaceRequest) => Promise<void>;
  deliver: () => Promise<unknown>;
  goNetwork: () => void;
  setAutomatic: (value: boolean) => void;
}) {
  const interaction = useInteraction();
  const [nextMode, setMode] = useState<MiningMode | null>(null);
  const [forkLesson, setForkLesson] = useState(false);
  const race = lab.race;
  const mode = nextMode ?? race?.mode ?? "learning";
  const active = race?.status === "running";
  const run = (body: RaceRequest) =>
    interaction
      .run("경쟁 제어", () => act({ raceId: race?.id, ...body }))
      .catch(() => undefined);
  const ready = NODE_IDS.every((n) => lab.nodes[n].mempool.length > 0);
  return (
    <>
      <Notice>
        노드는 거래와 블록을 검증하고, 채굴자는 후보 블록을 계산합니다 여기서는
        한 Python 서버가 세 채굴자의 계산을 교차 실행하는 모의 경쟁입니다
      </Notice>
      <Panel
        title="세 채굴자의 후보를 함께 관찰하세요"
        eyebrow="MINING RACE / REAL HASHES"
      >
        <p>
          각자 자기 노드의 멤풀에서 거래를 골라 후보를 만듭니다 같은 논스라도
          보상 수신자가 달라 해시가 달라집니다
        </p>
        <div className="actions">
          <label>
            계산 속도{" "}
            <select
              value={mode}
              disabled={active}
              onChange={(e) => setMode(e.target.value as MiningMode)}
            >
              <option value="learning">천천히 · 각자 한 번씩</option>
              <option value="fast">빠르게 · 실제 표본 보기</option>
            </select>
          </label>
          <button
            data-action="mine"
            disabled={interaction.busy || active || (guided && !ready && !race)}
            onClick={() => {
              setForkLesson(false);
              if (guided) setAutomatic(false);
              return run({ action: "prepare", guided, mode });
            }}
          >
            세 후보 준비
          </button>
          {race && race.status !== "stopped" && (
            <>
              <button
                disabled={interaction.busy}
                onClick={() => run({ action: active ? "pause" : "resume" })}
              >
                {active ? "경쟁 일시정지" : "경쟁 시작 / 계속"}
              </button>
              <button
                className="secondary"
                disabled={interaction.busy || active}
                onClick={() => run({ action: "step" })}
              >
                각자 한 번 계산
              </button>
              <button
                className="secondary"
                disabled={interaction.busy}
                onClick={() => run({ action: "stop" })}
              >
                경쟁 중지
              </button>
            </>
          )}
        </div>
        <p className="muted" role="status">
          {race ? `현재 계산 속도: ${race.mode === "fast" ? "빠르게" : "천천히"}. ` : ""}
          속도 선택은 다음 ‘세 후보 준비’에 적용됩니다 현재 후보와 시도 횟수는 유지합니다
          {race && mode !== race.mode && " 새로 준비하기 전까지는 현재 속도로 계속합니다"}
        </p>
        {guided && !ready && !race && (
          <p>
            첫 실습에서는 거래를 세 노드의 멤풀에 전달한 뒤 시작하세요.{" "}
            <button className="text-button" onClick={goNetwork}>
              전파 화면으로
            </button>
          </p>
        )}
        <p role="status">
          {uiText(race?.message ?? "세 후보를 준비하세요")}
        </p>
        {race?.guided && (
          <p className="muted">
            안내 실습은 첫 발견에서 설명을 위해 전체를 멈춥니다 다른 노드가
            이미 수신했다는 뜻은 아닙니다
          </p>
        )}
        {race?.advance && (
          <p role="status">
            성숙 빠른 진행 · 높이 {race.advance.startHeight} →{" "}
            {race.advance.height}. 다른 채굴자는 일시정지 중입니다 중지해도
            완료된 블록은 남습니다
          </p>
        )}
      </Panel>
      <div className="race-grid">
        {NODE_IDS.map((n) => {
          const job = race?.jobs[n];
          const node = lab.nodes[n];
          const candidate = job?.block;
          const reward = candidate?.transactions[0];
          const wallet = lab.minerWallets?.[n];
          const balance = wallet ? node.balances[wallet.address] : undefined;
          const matureTarget = Object.entries(node.utxos).find(
            ([, u]) =>
              u.coinbase &&
              u.address === wallet?.address &&
              node.chain.at(-1)!.height + 1 - (u.createdHeight ?? 0) < 100,
          );
          return (
            <Panel
              key={n}
              title={`채굴자 ${n}`}
              eyebrow={`NODE ${n} · OWN MEMPOOL`}
              className="race-card"
            >
              <p className="status-pill">
                {!job
                  ? "○ 준비 전"
                  : !race?.enabled[n]
                    ? "Ⅱ 개별 일시정지"
                    : job.status === "found"
                      ? "✓ 블록 발견"
                      : active
                        ? "● 계산 중"
                        : "Ⅱ 관찰 중"}
              </p>
              <p>
                활성 체인 #{node.chain.at(-1)?.height} · 대기 거래{" "}
                {node.mempool.length}개
              </p>
              {job && (
                <>
                  <p>
                    후보 #{candidate!.height} · 일반 거래{" "}
                    {
                      candidate!.transactions.filter(
                        (t) => t.kind !== "coinbase",
                      ).length
                    }
                    개
                  </p>
                  <div className="race-nonce">
                    <small>실제 시도 / 논스</small>
                    <strong>
                      {job.attempts.toLocaleString()} /{" "}
                      {job.samples.at(-1)?.nonce ?? "—"}
                    </strong>
                  </div>
                  <p className="hash-sample">
                    {job.samples.at(-1)?.hash ?? "첫 계산을 기다립니다"}
                  </p>
                  {job.message && <p>{uiText(job.message)}</p>}
                  <p>
                    보상 후보: 보조금 50 + 수수료{" "}
                    {(reward?.outputs[0]?.amount ?? 50) - 50} 코인
                  </p>
                  <details>
                    <summary>후보·목표값·최근 실제 결과</summary>
                    <p>이전 블록</p>
                    <Code>{candidate!.previousHash}</Code>
                    <p>목표값 이하이면 성공</p>
                    <Code>{lab.targetHex}</Code>
                    <p>코인베이스 TXID</p>
                    <Code>{reward?.id ?? ""}</Code>
                    <ul className="sample-list">
                      {job.samples.map((s) => (
                        <li key={s.nonce}>
                          Nonce {s.nonce} ·{" "}
                          {s.passed ? "✓ 성공" : "× 목표 초과"}
                          <Code>{s.hash}</Code>
                        </li>
                      ))}
                    </ul>
                  </details>
                  <button
                    className="secondary"
                    disabled={interaction.busy || !!race?.advance}
                    onClick={() => run({ action: "toggle", nodeId: n })}
                  >
                    {race?.enabled[n] ? `채굴자 ${n} 멈춤` : `채굴자 ${n} 참여`}
                  </button>
                </>
              )}
              <div className="reward-summary">
                <strong>내 보상 지갑</strong>
                <p>
                  체인에 포함 {balance?.confirmed ?? 0} · 미성숙{" "}
                  {balance?.immature ?? 0} · 다음 블록에 사용 가능{" "}
                  {balance?.spendable ?? 0}
                </p>
                {matureTarget && (
                  <>
                    <p>
                      선택 보상 #{matureTarget[1].createdHeight} · 다음 사용
                      가능 높이 {(matureTarget[1].createdHeight ?? 0) + 100}
                    </p>
                    <button
                      className="secondary"
                      disabled={
                        interaction.busy ||
                        active ||
                        !race ||
                        !!lab.queue.length ||
                        new Set(
                          NODE_IDS.map(
                            (id) => lab.nodes[id].chain.at(-1)?.hash,
                          ),
                        ).size !== 1
                      }
                      onClick={() => {
                        setAutomatic(false);
                        return run({
                          action: "advance",
                          nodeId: n,
                          reward: matureTarget[0],
                        });
                      }}
                    >
                      이 보상 성숙까지 빠른 진행
                    </button>
                  </>
                )}
              </div>
            </Panel>
          );
        })}
      </div>
      <Panel
        title="블록을 전달하면 무엇이 바뀔까요?"
        eyebrow="DISCOVER → PROPAGATE → VERIFY"
      >
        <p>
          발견한 노드가 먼저 검증합니다 다른 노드는 메시지를 받은 뒤 검증하고,
          더 큰 누적 작업량의 체인을 채택할 때 후보를 교체합니다
        </p>
        <div className="actions">
          <button
            disabled={interaction.busy || !lab.queue.length || !!race?.advance}
            onClick={() =>
              interaction.run("메시지 전달", deliver).catch(() => undefined)
            }
          >
            메시지 1개 전달 ({lab.queue.length})
          </button>
          <button className="secondary" onClick={goNetwork}>
            노드별 체인 자세히 보기
          </button>
        </div>
        {race?.completed.length ? (
          <ul>
            {race.completed
              .slice()
              .reverse()
              .map((item) => (
                <li key={item.hash}>
                  채굴자 {item.node} · #{item.height} · {item.attempts}회 ·{" "}
                  {short(item.hash)} — 발견 기록이며 최종 승리를 보장하지
                  않습니다
                </li>
              ))}
          </ul>
        ) : null}
      </Panel>
      <Panel title="심화: 같은 높이의 블록이 두 개라면?">
        <p>
          전달을 보류한 채 여러 후보를 계산하면 분기가 생길 수 있습니다 유효한
          블록도 동률이면 보관하고, 더 많은 작업이 누적된 쪽으로 바뀔 수
          있습니다
        </p>
        <button
          className="secondary"
          disabled={interaction.busy || active || !!race?.advance}
          onClick={() => {
            setAutomatic(false);
            setForkLesson(true);
            return run({ action: "prepare", guided: false, mode: "fast" });
          }}
        >
          전파 보류 분기 실험 준비
        </button>
        {forkLesson && (
          <p>
            ① 경쟁을 시작하세요 ② 두 채굴자가 같은 높이의 블록을 발견하면
            일시정지하세요 ③ 메시지를 전달해 분기를 비교하세요 ④ 한 채굴자만
            참여시켜 다음 블록을 찾고 다시 전달하세요 해답은 매번 실제로
            계산합니다
          </p>
        )}
      </Panel>
      <Notice>
        교육용 보조금은 50코인으로 고정하며 실제 Bitcoin의 현재 보조금이
        아닙니다 코인베이스는 각 일반 블록의 첫 거래입니다 보상은 생성
        높이보다 100 이상 높은 블록에서 소비할 수 있습니다
      </Notice>
    </>
  );
}
