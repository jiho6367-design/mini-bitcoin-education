import { useEffect, useState } from "react";
import { NODE_IDS, type NodeId } from "../core/types";
import { Differences, Notice, Panel } from "./shared";
import { useLab } from "./useLab";
import { WalletView } from "./WalletView";
import { MempoolView, NetworkDiagram, NetworkView } from "./NetworkView";
import { MiningView } from "./MiningView";
import { TamperView } from "./TamperView";
import { learningFlow } from "./learning";
import { short } from "./shared";

const pages = [
  { title: "실습 안내", sub: "블록체인의 첫걸음", icon: "◈" },
  { title: "지갑·거래", sub: "Wallet & Transaction", icon: "▣" },
  { title: "검증·Mempool", sub: "Validation & Mempool", icon: "≋" },
  { title: "채굴", sub: "Proof of Work", icon: "⛏" },
  { title: "노드·블록체인", sub: "Nodes & Blockchain", icon: "⬡" },
  { title: "변조 실험", sub: "Tamper Lab", icon: "⌁" },
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

export function App() {
  const controller = useLab();
  const {
    lab,
    wallets,
    automatic,
    setAutomatic,
    mining,
    error,
    setError,
    touch,
    reset,
    start,
    addWallet,
    beginMining,
    miningCommand,
    lesson,
    setLesson,
    composer,
    setComposer,
  } = controller;
  const [page, setPage] = useState(0);
  const [nodeId, setNodeId] = useState<NodeId>("A");
  const [difficulty, setDifficulty] = useState(2);
  const [resetPrompt, setResetPrompt] = useState(false);
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [page]);
  const activeMining =
    mining.status === "running" || mining.status === "paused";
  const height = lab ? lab.nodes[nodeId].chain.length - 1 : 0;
  const flow = learningFlow(lab, lesson, mining);
  function observeMempool() {
    if (lesson?.validation?.valid) setLesson({ ...lesson, phase: "mempool" });
    setPage(2);
  }
  function startLab(example: boolean) {
    start(difficulty, example);
    setPage(1);
    setNodeId("A");
    setResetPrompt(false);
  }
  function doReset() {
    reset();
    setPage(0);
    setNodeId("A");
    setResetPrompt(false);
  }
  return (
    <div className="app-shell">
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
            mini bitcoin<small>EDUCATION LAB</small>
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
          <span className="mini-dot" /> 로컬 교육용 환경
          <p>
            실제 자산 없이,
            <br />
            원리는 직접 경험하며.
          </p>
          <span className="tag">3 NODES · IN MEMORY</span>
        </div>
        <div className="sidebar-footer">
          Mini Bitcoin v1.0<small>배우기 위한 작은 블록체인</small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            실습실 <span>/</span> <strong>{pages[page].title}</strong>
          </div>
          <div className="topbar-actions">
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
                  초기화 ↺
                </button>
              </>
            )}
          </div>
        </header>
        <main id="main">
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                LEARN BY DOING / {String(page + 1).padStart(2, "0")}
              </div>
              <h1>{pages[page].title}</h1>
              <p>{pages[page].sub}</p>
            </div>
            <span className="education-badge">EDUCATIONAL MODEL</span>
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
                {lesson.validation ? "제출" : "작성"} 노드 {lesson.origin}
                {lesson.experiment ? ` · ${lesson.experiment} 사본` : ""}
              </small>
            )}
          </div>
          {resetPrompt && (
            <div className="reset-banner" role="alert">
              <div>
                <strong>현재 실습을 초기화할까요?</strong>
                <p>
                  지갑·키·체인·대기 거래와 채굴 작업이 사라집니다. 새 실습에서
                  난이도를 선택할 수 있습니다.
                </p>
              </div>
              <button className="danger" onClick={doReset}>
                초기화 확인
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
              <section className="hero">
                <div className="hero-copy">
                  <span className="hero-kicker">
                    작은 실험으로 이해하는 큰 원리
                  </span>
                  <h2>
                    거래 하나가
                    <br />
                    <em>블록체인</em>이 되기까지.
                  </h2>
                  <p>
                    지갑을 만들고, 거래에 서명하고, 직접 블록을 채굴해 보세요.
                    <br className="desktop-only" />
                    보이지 않던 비트코인의 동작 과정을 눈앞에서 따라갑니다.
                  </p>
                  <div className="hero-tags">
                    <span>실제 전자서명</span>
                    <span>실시간 PoW</span>
                    <span>독립 노드 검증</span>
                  </div>
                </div>
                <div className="hero-art" aria-hidden="true">
                  <svg viewBox="0 0 360 290">
                    <defs>
                      <pattern
                        id="dot-grid"
                        width="18"
                        height="18"
                        patternUnits="userSpaceOnUse"
                      >
                        <circle cx="2" cy="2" r="1" fill="#38594c" />
                      </pattern>
                    </defs>
                    <rect width="360" height="290" fill="url(#dot-grid)" />
                    <path
                      d="M73 148 L160 98 L267 155 L180 205 Z"
                      fill="none"
                      stroke="#6e927d"
                      strokeDasharray="5 6"
                      strokeWidth="2"
                    />
                    <g transform="translate(33 106)">
                      <path d="M0 20 L40 0 L80 20 L40 41 Z" fill="#b9ccb6" />
                      <path d="M0 20 L40 41 L40 89 L0 68 Z" fill="#658d73" />
                      <path d="M40 41 L80 20 L80 68 L40 89 Z" fill="#8aaa8c" />
                    </g>
                    <g transform="translate(124 35)">
                      <path d="M0 25 L48 0 L96 25 L48 50 Z" fill="#f4c59e" />
                      <path d="M0 25 L48 50 L48 111 L0 86 Z" fill="#cc804e" />
                      <path d="M48 50 L96 25 L96 86 L48 111 Z" fill="#e8a36e" />
                      <text
                        x="47"
                        y="83"
                        textAnchor="middle"
                        fill="#fff5e5"
                        fontSize="25"
                      >
                        ₿
                      </text>
                    </g>
                    <g transform="translate(228 137)">
                      <path d="M0 20 L40 0 L80 20 L40 41 Z" fill="#b9ccb6" />
                      <path d="M0 20 L40 41 L40 89 L0 68 Z" fill="#658d73" />
                      <path d="M40 41 L80 20 L80 68 L40 89 Z" fill="#8aaa8c" />
                    </g>
                    <text
                      x="182"
                      y="267"
                      textAnchor="middle"
                      fill="#acbdb0"
                      fontSize="10"
                      letterSpacing="3"
                    >
                      SIGN · VERIFY · CONNECT
                    </text>
                  </svg>
                </div>
              </section>
              <div className="three-col overview">
                <Panel eyebrow="01 / CREATE" title="내 손으로 만드는 거래">
                  <p>
                    공개키와 개인키의 역할을 구분하고, 서명이 거래의 변경을
                    어떻게 감지하는지 확인합니다.
                  </p>
                </Panel>
                <Panel eyebrow="02 / DISCOVER" title="정답을 찾는 채굴">
                  <p>
                    Nonce에 따라 달라지는 해시를 관찰하며 작업증명과 난이도의
                    관계를 이해합니다.
                  </p>
                </Panel>
                <Panel eyebrow="03 / VERIFY" title="연결되고 검증되는 기록">
                  <p>
                    노드마다 거래와 블록을 다시 검증하고, 과거 기록이 바뀌면
                    연결이 어떻게 깨지는지 실험합니다.
                  </p>
                </Panel>
              </div>
              {!lab ? (
                <Panel
                  title="첫 번째 실습을 시작하세요"
                  eyebrow="READY WHEN YOU ARE"
                  className="start-panel"
                >
                  <div className="start-grid">
                    <div>
                      <p>
                        예제 시작은 Alice와 Bob 지갑을 만들고 Alice에게 100
                        교육용 코인을 배정합니다. 송금과 채굴은 직접 실행합니다.
                      </p>
                      <div className="actions">
                        <button onClick={() => startLab(true)}>
                          예제 실습 시작 <span>→</span>
                        </button>
                        <button
                          className="secondary"
                          disabled={wallets.length >= 2}
                          onClick={addWallet}
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
                            onClick={() => startLab(false)}
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
                        난이도는 실습 중 고정됩니다.
                        <br />
                        다른 난이도는 새 실습에서 비교하세요.
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
                실제 Bitcoin 네트워크나 금전 거래와 연결되지 않는 교육용 축소
                모델입니다. 모든 데이터는 이 브라우저의 메모리에만 있으며
                새로고침하면 초기화됩니다.
              </Notice>
            </>
          )}
          {lab && page === 1 && (
            <WalletView
              key={`${lab.sessionId}-${nodeId}`}
              lab={lab}
              nodeId={nodeId}
              touch={touch}
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
            <MiningView
              lab={lab}
              nodeId={nodeId}
              mining={mining}
              begin={beginMining}
              command={miningCommand}
              goNetwork={() => setPage(4)}
            />
          )}
          {lab && page === 4 && (
            <NetworkView
              lab={lab}
              nodeId={nodeId}
              automatic={automatic}
              setAutomatic={setAutomatic}
              touch={touch}
            />
          )}
          {lab && page === 5 && (
            <TamperView
              key={`${lab.sessionId}-${nodeId}`}
              lab={lab}
              nodeId={nodeId}
            />
          )}
          <Differences />
          <footer className="page-footer">
            <span>MINI BITCOIN EDUCATION LAB</span>
            <span>개념을 이해하고, 직접 확인하세요.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
