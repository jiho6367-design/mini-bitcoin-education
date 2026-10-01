import { useState } from "react";
import { useInteraction } from "./useInteraction";
import type {
  LabSnapshot,
  MiningMode,
  MiningSample,
  NodeId,
} from "../api/types";
import type { MiningState } from "./useLab";
import { Code, Notice, Panel } from "./shared";

const statuses = {
  idle: "준비",
  running: "계산 중",
  paused: "일시정지",
  found: "채굴 성공",
  stopped: "중지됨",
  error: "오류",
};
export function MiningView({
  lab,
  nodeId,
  mining,
  begin,
  command,
  goNetwork,
}: {
  lab: LabSnapshot;
  nodeId: NodeId;
  mining: MiningState;
  begin: (node: NodeId, step: boolean, mode?: MiningMode) => Promise<void>;
  command: (type: "pause" | "resume" | "step" | "stop") => Promise<void>;
  goNetwork: () => void;
}) {
  const interaction = useInteraction();
  const [selectedSample, setSelectedSample] = useState<MiningSample | null>(
    null,
  );
  const active = mining.status === "running" || mining.status === "paused";
  const difficulty = lab.difficulty;
  const noCandidate = !lab.nodes[nodeId].canStartMining;
  return (
    <>
      <Notice>
        채굴은 거래를 다시 서명하는 과정이 아닙니다. 후보 블록의 Nonce를 바꾸어
        목표값 이하의 해시를 찾습니다. 한 번의 성공 시간은 무작위입니다.
      </Notice>
      <div className="mining-layout">
        <Panel
          className="mining-console"
          title="조건을 만족하는 해시를 찾아보세요"
          eyebrow="PROOF OF WORK / SHA-256"
        >
          <div className="console-top">
            <span
              className={`status-pill ${mining.status === "running" ? "pulsing" : ""}`}
            >
              ● {statuses[mining.status]}
            </span>
            <span>채굴 노드 {active ? mining.node : nodeId}</span>
          </div>
          <p className="mining-mode-note">
            {mining.mode === "learning"
              ? `학습 실행 · 약 ${lab.limits.learningIntervalMs}ms마다 Nonce 하나를 실제로 계산합니다.`
              : "빠른 실행 · 실제 계산한 최근 결과를 표본으로 표시합니다."}
          </p>
          <div className="nonce-label">NONCE</div>
          <div className="nonce-number" data-testid="nonce">
            {mining.block?.nonce.toLocaleString() ?? "—"}
          </div>
          <div className="label">실제로 계산한 블록 해시</div>
          <div className="live-hash" data-testid="mining-hash">
            {mining.block?.hash ? (
              <>
                <mark>{mining.block.hash.slice(0, difficulty)}</mark>
                {mining.block.hash.slice(difficulty)}
              </>
            ) : (
              "한 번 계산하기를 눌러 첫 번째 해시를 확인하세요."
            )}
          </div>
          <div className="target-row">
            <span>목표값 이하이면 성공</span>
            <code>{lab.targetHex}</code>
          </div>
          {interaction.busy && (
            <p role="status">{interaction.pending.join(" · ")} 처리 중…</p>
          )}
          <div className="actions">
            <button
              data-action="mine"
              disabled={
                interaction.busy ||
                mining.status === "running" ||
                (!active && noCandidate)
              }
              onClick={() =>
                interaction.run("한 번 계산", () =>
                  active ? command("step") : begin(nodeId, true),
                )
              }
            >
              한 번 계산하기
            </button>
            <button
              className="secondary"
              disabled={interaction.busy || active || noCandidate}
              onClick={() =>
                interaction.run("연속 채굴 시작", () => begin(nodeId, false))
              }
            >
              연속 채굴 시작
            </button>
            <button
              className="secondary"
              disabled={interaction.busy || active || noCandidate}
              onClick={() =>
                interaction.run("학습 실행", () =>
                  begin(nodeId, false, "learning"),
                )
              }
            >
              천천히 학습 실행
            </button>
            {active && (
              <>
                <button
                  className="secondary"
                  data-action="pause"
                  disabled={interaction.pending.includes("채굴 제어")}
                  onClick={() =>
                    interaction.run("채굴 제어", () =>
                      command(mining.status === "running" ? "pause" : "resume"),
                    )
                  }
                >
                  {mining.status === "running" ? "일시정지" : "계속 채굴"}
                </button>
                <button
                  className="secondary"
                  disabled={interaction.pending.includes("채굴 중지")}
                  onClick={() =>
                    interaction.run("채굴 중지", () => command("stop"))
                  }
                >
                  채굴 중지
                </button>
              </>
            )}
          </div>
          {!active && noCandidate && (
            <p className="console-hint">
              대기 거래가 필요합니다. 이전 블록이 전달 중이라면 먼저 전달을
              완료하세요.
            </p>
          )}
          {mining.status === "found" && (
            <div className="mined-success" role="status">
              ✓ 채굴 성공 · Node {mining.node}가 재검증 후 체인에 추가했습니다.
              <button onClick={goNetwork}>노드 전파 확인 →</button>
            </div>
          )}
          {mining.message && <p role="alert">{mining.message}</p>}
        </Panel>
        <div className="stack">
          <Panel title="이번 실습의 난이도" eyebrow="DIFFICULTY">
            <div className="difficulty-number">
              {difficulty}
              <span>개의 선행 0</span>
            </div>
            <div className="zero-boxes">
              {Array.from({ length: 4 }, (_, i) => (
                <span key={i} className={i < difficulty ? "required" : ""}>
                  {i < difficulty ? "0" : "?"}
                </span>
              ))}
            </div>
            <p>
              해시 하나가 성공할 확률은 약{" "}
              <b>1 / {(16 ** difficulty).toLocaleString()}</b>입니다.
            </p>
            <p className="muted">
              평균 시도 횟수는 약 {(16 ** difficulty).toLocaleString()}회이며,
              실제 결과는 매번 달라집니다.
            </p>
            <small>난이도 변경은 시작 화면에서 새 실습에 적용합니다.</small>
          </Panel>
          <Panel title="후보 블록">
            <div className="node-stat">
              <span>높이</span>
              <b>{mining.block?.height ?? lab.nodes[nodeId].chain.length}</b>
            </div>
            <div className="node-stat">
              <span>포함 거래</span>
              <b>
                {mining.block?.transactions.length ??
                  lab.nodes[nodeId].mempool.length}
                개
              </b>
            </div>
            <details>
              <summary>후보의 이전 해시와 데이터 해시</summary>
              <Code>
                {mining.block?.previousHash ??
                  lab.nodes[nodeId].chain.at(-1)!.hash}
              </Code>
              <Code>
                {mining.block?.transactionsHash ?? "채굴 시작 시 고정됩니다."}
              </Code>
            </details>
            <p className="muted">
              채굴 도중 도착한 거래는 다음 후보에 포함됩니다. 이 모델에는 채굴
              보상이 없습니다.
            </p>
          </Panel>
        </div>
      </div>
      <div className="metrics mining-metrics">
        <div>
          <span>실제 시도 횟수</span>
          <strong data-testid="attempts">
            {mining.attempts.toLocaleString()}
          </strong>
          <small>계산한 모든 Nonce 집계</small>
        </div>
        <div>
          <span>실제 계산 시간</span>
          <strong>
            {(mining.elapsedMs / 1000).toFixed(3)}
            <em>s</em>
          </strong>
          <small>일시정지·표시 대기 시간 제외</small>
        </div>
        <div>
          <span>평균 계산 속도</span>
          <strong>
            {mining.elapsedMs
              ? Math.round(
                  mining.attempts / (mining.elapsedMs / 1000),
                ).toLocaleString()
              : "—"}
            <em>H/s</em>
          </strong>
          <small>PC와 실행 상황에 따라 달라짐</small>
        </div>
        <div>
          <span>학습 관찰 대기 시간</span>
          <strong data-testid="observation-time">
            {(mining.observationMs / 1000).toFixed(3)}
            <em>s</em>
          </strong>
          <small>저속 실행의 계산 사이 대기 · 일시정지 제외</small>
        </div>
      </div>
      <Panel title="최근 실제 Nonce·Hash 비교" className="mining-samples">
        <p className="muted">
          최근 {lab.limits.miningSampleLimit}개까지 표시합니다. 실패는
          목표값보다 큰 해시, 성공은 목표값 이하의 해시입니다. 첫 계산이 성공할
          수도 있습니다.
        </p>
        {selectedSample && (
          <div className="sample-inspector" data-testid="sample-inspector">
            <h3>선택한 표본 · Nonce {selectedSample.nonce}</h3>
            <Code>{selectedSample.hash}</Code>
            <p>
              {selectedSample.passed
                ? "✓ 목표값 이하 · 성공"
                : "✕ 목표값 초과 · 실패"}
            </p>
            <p>
              현재 최신 계산: Nonce {mining.block?.nonce ?? "—"}. 선택한 표본은
              새 계산이 진행돼도 유지됩니다.
            </p>
            <button
              className="secondary"
              onClick={() => setSelectedSample(null)}
            >
              표본 선택 해제
            </button>
          </div>
        )}
        {mining.samples.length ? (
          <ol className="hash-sample-list" data-testid="mining-samples">
            {[...mining.samples].reverse().map((sample) => (
              <li
                key={sample.nonce}
                className={`hash-sample ${sample.passed ? "passed" : ""}`}
                data-testid="mining-sample"
              >
                <button
                  className="secondary"
                  aria-label={`Nonce ${sample.nonce} 표본 비교`}
                  aria-pressed={selectedSample?.nonce === sample.nonce}
                  onClick={() => setSelectedSample({ ...sample })}
                >
                  Nonce {sample.nonce.toLocaleString()}
                </button>
                <code data-testid="sample-hash">
                  <mark>{sample.hash.slice(0, difficulty)}</mark>
                  {sample.hash.slice(difficulty)}
                </code>
                <span className="sample-result">
                  {sample.passed
                    ? "✓ 성공 · 목표값 이하"
                    : "× 실패 · 목표값 초과"}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p>계산을 시작하면 실제 결과를 이곳에서 비교할 수 있습니다.</p>
        )}
      </Panel>
      <p className="muted">
        해시는 Python 백엔드가 계산합니다. 연속 실행 화면은 실제 계산 결과의
        일부를 표본으로 보여줍니다. 정답 해시를 미리 정하거나 애니메이션으로
        만들어내지 않습니다.
      </p>
    </>
  );
}
