import { useEffect, useState, type ReactNode } from "react";
import { version } from "../../package.json";
import { type ThemePreference } from "../theme";
import { useTheme } from "./useTheme";
import { NODE_IDS, type NodeId } from "../api/types";
import { Differences, Notice, Panel } from "./shared";
import { errorMessage, useLab } from "./useLab";
import { WalletView } from "./WalletView";
import { MempoolView, NetworkDiagram, NetworkView } from "./NetworkView";
import { RaceView } from "./RaceView";
import { Journey } from "./Journey";
import { MiningView } from "./MiningView";
import { TamperView } from "./TamperView";
import { learningFlow } from "./learning";
import { short } from "./shared";
import { LearningGuide, LessonRecap } from "./LearningGuide";
import { PracticeArchive } from "./PracticeArchive";
import { useInteraction } from "./useInteraction";
import { TeamNavigation } from "./TeamNavigation";

const pages = [
  { title: "실습 안내", sub: "지갑 → 거래 → 채굴 → 노드 검증", icon: "◈" },
  { title: "지갑·거래", sub: "UTXO 선택·서명·거래 검증", icon: "▣" },
  { title: "검증·Mempool", sub: "노드별 미확정 거래", icon: "≋" },
  { title: "채굴", sub: "실제 해시 계산·세 채굴자 경쟁", icon: "⛏" },
  { title: "노드·블록체인", sub: "메시지 전달·체인·분기", icon: "⬡" },
  { title: "변조 실험", sub: "복사본 변경·검증 실패 비교", icon: "⌁" },
];
const flows = [
  "Wallet",
  "서명",
  "검증",
  "Mempool",
  "Mining",
  "전파",
  "Blockchain",
];

export function App({ entry }: { entry?: ReactNode } = {}) {
  const { preference, selectTheme } = useTheme();
  const controller = useLab();
  const {
    lab,
    guided,
    setGuided,
    history,
    raceAction,
    wallets,
    automatic,
    setAutomatic,
    mining,
    error,
    setError,
    reset,
    start,
    addWallet,
    beginMining,
    miningCommand,
    actions,
    lesson,
    setLesson,
    composer,
    setComposer,
  } = controller;
  const interaction = useInteraction();
  const [page, setPage] = useState(0);
  const [nodeId, setNodeId] = useState<NodeId>("A");
  const [difficulty, setDifficulty] = useState(2);
  const [resetPrompt, setResetPrompt] = useState(false);
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [page]);
  const activeMining =
    mining.status === "running" ||
    mining.status === "paused" ||
    lab?.race?.status === "running";
  const height = lab ? lab.nodes[nodeId].chain.length - 1 : 0;
  const flow = learningFlow(lab, lesson, mining);
  function navigate(targetPage: number, action?: string) {
    if (targetPage === 2) observeMempool();
    else setPage(targetPage);
    requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(
        action ? `[data-action="${action}"]` : "#main h1",
      );
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "center", behavior: "instant" });
    });
  }
  function observeMempool() {
    if (lesson?.validation?.valid) setLesson({ ...lesson, phase: "mempool" });
    setPage(2);
  }
  async function startLab(example: boolean) {
    if (!(await start(difficulty, example))) return;
    setPage(1);
    setNodeId("A");
    setResetPrompt(false);
  }
  const reportError = (e: unknown) => setError(errorMessage(e));
  async function doReset() {
    if (!(await reset())) return;
    setPage(0);
    setNodeId("A");
    setResetPrompt(false);
  }
  return (
    <div className={`app-shell ${page === 4 ? "network-page" : ""}`}>
      <a className="skip-link" href="#main">
        본문으로 바로가기
      </a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage(0);
          }}
        >
          <span className="brand-mark">₿</span>
          <span>
            팀 블록체인<small>통합 실습실</small>
          </span>
        </a>
        <div className="sidebar-label">실습 워크스페이스</div>
        <nav aria-label="실습 화면">
          {pages.map((p, i) => (
            <button
              key={p.title}
              className={`nav-item ${page === i ? "current" : ""}`}
              aria-current={page === i ? "page" : undefined}
              disabled={!lab && i !== 0}
              onClick={() => {
                if (i === 2) observeMempool();
                else setPage(i);
                setError("");
              }}
            >
              <span className="nav-icon" aria-hidden="true">
                {p.icon}
              </span>
              <span>{p.title}</span>
              <small>{String(i + 1).padStart(2, "0")}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="mini-dot" /> 블록체인 실습 환경
          <p>모의 노드 3개 · 실제 자산 연결 없음</p>
        </div>
        <div className="sidebar-footer">
          <span data-testid="app-version">팀 블록체인 v{version}</span>
          <small>학습용 블록체인</small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            실습실 <span>/</span> <strong>{pages[page].title}</strong>
          </div>
          <TeamNavigation />
          <div className="topbar-actions">
            <label className="theme-select">
              화면 테마
              <select
                aria-label="화면 테마"
                value={preference}
                onChange={(event) =>
                  selectTheme(event.target.value as ThemePreference)
                }
              >
                <option value="system">시스템</option>
                <option value="light">라이트</option>
                <option value="dark">다크</option>
              </select>
            </label>
            <span className="local-status">
              <i /> {lab ? "실습 진행 중" : "실습 준비"}
            </span>
            {lab && (
              <>
                <label className="node-select">
                  관찰 노드
                  <select
                    aria-label="관찰 노드"
                    value={nodeId}
                    disabled={activeMining}
                    onChange={(e) => setNodeId(e.target.value as NodeId)}
                  >
                    {NODE_IDS.map((id) => (
                      <option key={id} value={id}>
                        Node {id}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="text-button"
                  onClick={() => setResetPrompt(true)}
                >
                  저장 후 나가기 ↺
                </button>
              </>
            )}
          </div>
        </header>
        <main id="main" className={page === 3 ? "race-page" : undefined}>
          <PracticeArchive
            storage={controller.storage}
            onResume={(result) => {
              if ("lab" in result) {
                controller.resumePractice(result.lab);
                setPage(result.lab.phase === "running" ? 1 : 0);
              }
            }}
          />
          <div className="page-heading">
            <div>
              <h1 tabIndex={-1}>{pages[page].title}</h1>
              <p>{pages[page].sub}</p>
            </div>
          </div>
          <div className="learning-flow" aria-label="전체 학습 흐름">
            {flows.map((f, i) => (
              <div
                key={f}
                data-testid={`flow-${i}`}
                data-state={
                  i === flow.current
                    ? flow.failed
                      ? "failed"
                      : "current"
                    : flow.completed.includes(i)
                      ? "complete"
                      : "pending"
                }
                aria-current={i === flow.current ? "step" : undefined}
                aria-label={`${f}: ${i === flow.current ? (flow.failed ? "검증 실패" : "현재 단계") : flow.completed.includes(i) ? "완료" : "대기"}`}
                className={
                  i === flow.current
                    ? `flow-current ${flow.failed ? "flow-failed" : ""}`
                    : flow.completed.includes(i)
                      ? "flow-complete"
                      : ""
                }
              >
                <span>
                  {flow.completed.includes(i)
                    ? "✓"
                    : flow.failed && i === flow.current
                      ? "✕"
                      : String(i + 1).padStart(2, "0")}
                </span>
                {f}
                {i < flows.length - 1 && <b aria-hidden="true">→</b>}
              </div>
            ))}
          </div>
          <div
            className="learning-status"
            data-testid="learning-status"
            role="status"
          >
            <strong>
              {flow.failed ? "✕ " : ""}
              {flow.summary}
            </strong>
            {lesson?.signed && (
              <small>
                관찰 거래 {short(lesson.checked?.id ?? lesson.signed.id, 8)} ·{" "}
                {lesson.restored ? "복원 관찰 기준" : lesson.validation ? "제출" : "작성"} 노드 {lesson.origin}
                {lesson.experiment ? ` · ${lesson.experiment} 사본` : ""}
              </small>
            )}
          </div>
          {page === 0 && (
            <Panel title="시연 영상" className="team-demo">
              <video controls playsInline preload="metadata" data-testid="team-demo-video"
                poster="/static/media/blockchain-demo-poster.jpg">
                <source src="/static/media/blockchain-demo-ko.mp4" type="video/mp4" />
                <a href="/static/media/blockchain-demo-ko.mp4" download>영상 다운로드</a>
              </video>
              <div className="team-demo-caption">
                <span>이전 기초 화면 시연 · 한국어 자막</span>
                <a href="/basic">기초 실습 열기</a>
              </div>
            </Panel>
          )}
          <LearningGuide
            page={page}
            lab={lab}
            lesson={lesson}
            mining={mining}
            nodeId={nodeId}
            navigate={navigate}
          />
          {lab && (
            <>
              <div className="lesson-mode">
                <label>
                  실습 방식{" "}
                  <select
                    value={guided ? "guided" : "free"}
                    onChange={(e) => {
                      setGuided(e.target.value === "guided");
                      if (lab.race)
                        void raceAction({
                          action: "configure",
                          guided: e.target.value === "guided",
                          raceId: lab.race.id,
                        }).catch(() => undefined);
                    }}
                  >
                    <option value="guided">안내 실습</option>
                    <option value="free">자유 탐색</option>
                  </select>
                </label>
                <p>지갑은 서명 · 노드는 검증과 전달 · 채굴자는 후보 계산</p>
              </div>
              <Journey lab={lab} history={history} current={lesson} onSelect={controller.selectLesson} />
            </>
          )}
          {lab && lesson && page === 1 && (
            <LessonRecap
              lab={lab}
              lesson={lesson}
              newTransaction={() => {
                setLesson(null);
                navigate(1, "draft");
              }}
              tamper={() => navigate(5)}
            />
          )}
          {interaction.busy && (
            <p role="status">{interaction.pending.join(" · ")} 처리 중…</p>
          )}
          {resetPrompt && (
            <div className="reset-banner" role="alert">
              <div>
                <strong>현재 실습에서 나갈까요?</strong>
                <p>
                  지갑·체인·대기 거래는 저장된 실습에서 다시 열 수 있습니다 새
                  실습에서 난이도를 선택할 수 있습니다
                </p>
              </div>
              <button className="danger" onClick={doReset}>
                나가기 확인
              </button>
              <button
                className="secondary"
                onClick={() => setResetPrompt(false)}
              >
                취소
              </button>
            </div>
          )}
          {error && (
            <div className="result bad" role="alert">
              {error}
              <button className="text-button" onClick={() => setError("")}>
                닫기
              </button>
            </div>
          )}
          {page === 0 && (
            <>
              {!lab && entry}
              {!lab ? (
                <Panel
                  title="실습 시작"

                  className="start-panel"
                >
                  <div className="start-grid">
                    <div>
                      <p>
                        Alice 100코인 · Bob 0코인으로 시작
                      </p>
                      <div className="actions">
                        <button
                          data-action="start"
                          disabled={interaction.busy}
                          onClick={() =>
                            interaction.run("실습 시작", () => startLab(true))
                          }
                        >
                          예제 실습 시작 <span>→</span>
                        </button>
                        <button
                          className="secondary"
                          disabled={interaction.busy || wallets.length >= 2}
                          onClick={() =>
                            interaction.run("지갑 생성", addWallet)
                          }
                        >
                          {wallets.length === 0
                            ? "직접 시작: Alice 지갑 생성"
                            : wallets.length === 1
                              ? "Bob 지갑 생성"
                              : "지갑 준비 완료"}
                        </button>
                        {wallets.length >= 2 && (
                          <button
                            className="secondary"
                            disabled={interaction.busy}
                            onClick={() =>
                              interaction.run("실습 시작", () =>
                                startLab(false),
                              )
                            }
                          >
                            생성한 지갑으로 시작
                          </button>
                        )}
                      </div>
                      {wallets.length > 0 && (
                        <p className="good-text" role="status">
                          ✓ {wallets.map((w) => w.name).join(", ")} 지갑 생성
                          완료
                        </p>
                      )}
                    </div>
                    <label className="difficulty-select">
                      실습 난이도
                      <select
                        aria-label="실습 난이도"
                        value={difficulty}
                        onChange={(e) => setDifficulty(Number(e.target.value))}
                      >
                        {[1, 2, 3, 4].map((d) => (
                          <option key={d} value={d}>
                            {d} · 선행 0 {d}개{d === 2 ? " (기본)" : ""}
                          </option>
                        ))}
                      </select>
                      <small>
                        난이도는 실습 중 고정됩니다
                        <br />
                        다른 난이도는 새 실습에서 비교하세요
                      </small>
                    </label>
                  </div>
                </Panel>
              ) : (
                <Panel title="진행 중인 실습" eyebrow="YOUR WORKSPACE">
                  <div className="metrics mini">
                    <div>
                      <span>지갑</span>
                      <strong>{wallets.length}</strong>
                    </div>
                    <div>
                      <span>Node {nodeId} 체인 높이</span>
                      <strong>{height}</strong>
                    </div>
                    <div>
                      <span>난이도</span>
                      <strong>{lab.genesis.difficulty}</strong>
                    </div>
                  </div>
                  <div className="actions">
                    <button onClick={() => setPage(1)}>
                      지갑·거래로 이동 →
                    </button>
                    <button
                      className="secondary"
                      onClick={() => setResetPrompt(true)}
                    >
                      난이도를 바꿔 새 실습
                    </button>
                  </div>
                  <NetworkDiagram lab={lab} />
                </Panel>
              )}
              <Notice>
                교육용 모의 환경 · 서버에 자동 저장 · 저장 기록에서 결과 확인·이어 하기
              </Notice>
            </>
          )}
          {lab && page === 1 && (
            <WalletView
              key={`${lab.sessionId}-${nodeId}-${controller.observationVersion}`}
              lab={lab}
              nodeId={nodeId}
              actions={actions}
              addWallet={addWallet}
              lesson={lesson}
              setLesson={setLesson}
              composer={composer}
              setComposer={setComposer}
              goMempool={observeMempool}
            />
          )}
          {lab && page === 2 && (
            <MempoolView lab={lab} goMining={() => setPage(3)} />
          )}
          {lab && page === 3 && (
            <>
              <RaceView
                lab={lab}
                guided={guided}
                act={raceAction}
                deliver={actions.deliverNext}
                goNetwork={() => setPage(4)}
                setAutomatic={setAutomatic}
              />
              <details className="single-miner">
                <summary>한 채굴자의 계산 자세히 실험하기</summary>
                <MiningView
                  lab={lab}
                  nodeId={nodeId}
                  key={`${lab.labId}-${mining.jobId ?? "ready"}`}
                  mining={mining}
                  begin={beginMining}
                  command={miningCommand}
                  goNetwork={() => setPage(4)}
                />
              </details>
            </>
          )}
          {lab && page === 4 && (
            <>
            <NetworkView
              key={lab.labId}
              lab={lab}
              nodeId={nodeId}
              automatic={automatic}
              setAutomatic={setAutomatic}
              actions={actions}
              reportError={reportError}
            />
            {lesson && <LessonRecap lab={lab} lesson={lesson} newTransaction={() => { setLesson(null); navigate(1, "draft"); }} tamper={() => navigate(5)} />}
            </>
          )}
          {lab && page === 5 && (
            <TamperView
              key={`${lab.sessionId}-${nodeId}`}
              lab={lab}
              nodeId={nodeId}
              actions={actions}
              reportError={reportError}
            />
          )}
          <Differences />
          <footer className="page-footer">
            <span>팀 블록체인 실습실</span>
            <span>교육용 모의 환경</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
