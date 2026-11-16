import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { PracticeArchive } from "./PracticeArchive";
import qrcode from "qrcode-generator";
import {
  NODE_IDS,
  type NodeId,
  type DraftResult,
  type Transaction,
  type LabSnapshot,
} from "../api/types";
import { Panel, Notice, short } from "./shared";
import { useTheme } from "./useTheme";
import type { ThemePreference } from "../theme";
import { TeamNavigation } from "./TeamNavigation";
import { uiText } from "../copy";
import { useConnectionMessage } from "./ServerConnection";
import { TeamPracticeGuide, useGuideTransaction, type GuidePage } from "./TeamPracticeGuide";
import { serverConnection } from "../api/serverConnection";
import { TeamWalletBalances } from "./TeamWalletBalances";
import { SingleNodeMining } from "./SingleNodeMining";
import {
  ROOM_KEY,
  roomRequest,
  teamRequest,
  savedCredential,
  useClassroom,
  type Credential,
  type RoomState,
} from "./useClassroom";

export function ClassroomApp({
  mode,
  publicUrl,
  defaultTeamCode,
  personal,
  back,
}: {
  mode: string;
  publicUrl: string;
  defaultTeamCode?: string;
  personal: boolean;
  back: () => void;
}) {
  const [credential, setCredential] = useState<Credential | null>(
    savedCredential,
  );
  const credentialRef = useRef(credential);
  credentialRef.current = credential;
  const [stableTeamCode, setStableTeamCode] = useState(defaultTeamCode || "");
  const [teacherForm, setTeacherForm] = useState(mode === "teacher" && !defaultTeamCode);
  const [teamForm, setTeamForm] = useState(mode === "team" || (mode === "teacher" && !!defaultTeamCode));
  const [name, setName] = useState("");
  const [code, setCode] = useState(
    new URLSearchParams(location.search).get("room") || defaultTeamCode || "",
  );
  const [secret, setSecret] = useState("");
  const [difficulty, setDifficulty] = useState(1);
  const [newPractice, setNewPractice] = useState(false);
  const [initialBalance, setInitialBalance] = useState("100");
  const [joining, setJoining] = useState(false);
  const [joinError, setJoinError] = useState("");
  const [page, setPage] = useState("거래");
  const [removing, setRemoving] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const { preference, selectTheme } = useTheme();
  const updateCredential = useCallback((next: Credential | null, message = "") => {
    try {
      if (next) sessionStorage.setItem(ROOM_KEY, JSON.stringify(next));
      else sessionStorage.removeItem(ROOM_KEY);
    } catch { /* reconnect still works while this page is open */ }
    if (!next && credentialRef.current) setCode(credentialRef.current.code);
    if (next?.teamMode) setStableTeamCode(next.code);
    setCredential(next);
    setJoinError(message);
    setPage("거래");
    setRemoving(null);
    if (!next) {
      setTeacherForm(false);
      setTeamForm(false);
    }
  }, []);
  const room = useClassroom(credential, updateCredential);
  const joinErrorMessage = useConnectionMessage(joinError);
  const saveErrorMessage = useConnectionMessage(saveError);
  const roomErrorMessage = useConnectionMessage(room.error);
  const me = room.state?.participants.find(
    (p) => p.id === credential?.participantId,
  );
  const teamMode = !!(credential?.teamMode || room.state?.teamMode);
  const serverPhase = useSyncExternalStore(
    serverConnection.subscribe,
    () => serverConnection.getSnapshot().phase,
    () => serverConnection.getSnapshot().phase,
  );
  const guideConnected = room.connection === "연결됨" && serverPhase === "ready"
    && !room.state?.storage?.error && !room.state?.closed;
  const { transactionId: guideTransactionId, selectTransaction: selectGuideTransaction } =
    useGuideTransaction(teamMode && guideConnected ? room.state : null);
  const teacher = !teamMode && me?.role === "teacher";
  const controls = teamMode || teacher;
  const disabled = room.busy || !!room.pending || room.connection !== "연결됨";
  const learningDisabled =
    disabled || !!room.state?.storage?.error || !!room.state?.limited;
  function focusGuideTarget(id: string) {
    requestAnimationFrame(() => {
      const target = document.getElementById(id);
      target?.scrollIntoView({ block: "start", behavior: "instant" });
      target?.focus({ preventScroll: true });
    });
  }
  function navigateFromGuide(next: GuidePage) {
    setPage(next);
    focusGuideTarget("team-practice-content");
  }
  async function enter() {
    if (joining) return;
    if (
      !teacherForm && !teamForm &&
      !/^[A-HJ-NP-Z2-9]{8}$/.test(code.trim().toUpperCase())
    ) {
      setJoinError(
        "방 코드 8자리를 입력하거나 공유 링크로 접속하세요",
      );
      return;
    }
    setJoining(true);
    setJoinError("");
    try {
      const c = teamForm
        ? await teamRequest<Credential>("/rooms", { name, difficulty: 1,
          ...(newPractice ? { newPractice: true, initialBalance: Number(initialBalance) } : {}) })
        : await roomRequest<Credential>(
        teacherForm ? "/rooms" : `/rooms/${code.trim().toUpperCase()}/join`,
        teacherForm ? { secret, name, difficulty } : { name },
      );
      try {
        sessionStorage.setItem(ROOM_KEY, JSON.stringify(c));
      } catch {
        setJoinError(
          "브라우저 저장소를 사용할 수 없어 새로고침 시 다시 입장해야 합니다",
        );
      }
      setCredential(c);
      if (c.teamMode) setStableTeamCode(c.code);
      setCode(c.code);
      history.replaceState(null, "", `${location.pathname}?classroom=join&room=${c.code}`);
      setSecret("");
    } catch (e) {
      setJoinError(e instanceof Error ? e.message : "접속 실패");
    } finally {
      setJoining(false);
    }
  }
  function forget(nextTeacher = false) {
    const previousCode = teamMode ? credential?.code || code : "";
    try {
      sessionStorage.removeItem(ROOM_KEY);
    } catch {
      /* storage can be disabled */
    }
    setCredential(null);
    setTeacherForm(nextTeacher);
    setTeamForm(false);
    setPage("거래");
    setRemoving(null);
    setCode(previousCode);
    setSecret("");
    setJoinError("");
    history.replaceState(
      null,
      "",
      `${location.pathname}?classroom=${nextTeacher ? "teacher" : "join"}${previousCode ? `&room=${previousCode}` : ""}`,
    );
  }
  return (
    <div className="classroom-shell">
      <a className="skip-link" href="#classroom-main">
        공동 실습 본문으로 바로가기
      </a>
      <header className="classroom-header">
        <div>
          <strong>팀 블록체인 · 공동 실습</strong>
          <p>{teacherForm || teacher ? "교사·대표·관찰자 실습" : "같은 링크에서 함께 이어가는 실습"}</p>
        </div>
        <TeamNavigation classroom />
        <label>
          화면 테마
          <select
            value={preference}
            onChange={(e) => selectTheme(e.target.value as ThemePreference)}
          >
            <option value="system">시스템</option>
            <option value="light">라이트</option>
            <option value="dark">다크</option>
          </select>
        </label>
      </header>
      <main id="classroom-main" tabIndex={-1}>
        {!credential ? (
          <Panel title={teacherForm ? "교사 방 만들기" : teamForm ? newPractice ? "새 실습 만들기" : "팀 실습 시작" : "공동 실습 참여"}>
            <div className="button-row">
              <button
                className="secondary"
                onClick={() => { setTeacherForm(false); setTeamForm(false); setNewPractice(false); }}
              >
                공동 실습 참여
              </button>
              <button
                className="secondary"
                onClick={() => { setTeacherForm(false); setTeamForm(true); setNewPractice(false); }}
              >
                팀 실습 시작
              </button>
              <button
                className="secondary"
                onClick={() => { setTeacherForm(false); setTeamForm(true); setNewPractice(true); }}
              >
                새 실습 만들기
              </button>
              {!stableTeamCode && <button
                className="text-button"
                onClick={() => { setTeacherForm(true); setTeamForm(false); }}
              >
                교사 방 만들기
              </button>}
              {personal && (
                <button className="secondary" onClick={back}>
                  통합 실습
                </button>
              )}
            </div>
            <form
              className="classroom-form"
              onSubmit={(e) => {
                e.preventDefault();
                void enter();
              }}
            >
              <label>
                별명
                <input
                  required
                  maxLength={30}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              {teacherForm ? (
                <>
                  <label>
                    교사용 비밀코드
                    <input
                      type="password"
                      required
                      value={secret}
                      onChange={(e) => setSecret(e.target.value)}
                    />
                  </label>
                  <label>
                    방 난이도
                    <select
                      value={difficulty}
                      onChange={(e) => setDifficulty(Number(e.target.value))}
                    >
                      {[1, 2, 3, 4].map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p>
                    교사용 비밀코드를 입력하세요 난이도는 방을 만들 때 정합니다
                  </p>
                </>
              ) : !teamForm ? (
                <label>
                  방 코드
                  <input
                    required
                    aria-label="방 코드"
                    aria-describedby="room-code-help"
                    maxLength={64}
                    autoCapitalize="characters"
                    autoCorrect="off"
                    spellCheck={false}
                    value={code}
                    onChange={(e) => setCode(e.target.value.toUpperCase())}
                  />
                  <small id="room-code-help">
                    공유 링크나 QR로 접속하면 자동 입력됩니다
                  </small>
                </label>
              ) : newPractice ? (
                <>
                  <label>
                    팀별 초기 코인
                    <input type="number" required min={1} max={10000} step={1}
                      value={initialBalance} onChange={(e) => setInitialBalance(e.target.value)} />
                  </label>
                  <p>A·B·C에 같은 금액을 한 번 배정합니다 새 실습에는 별도 링크가 생기고 기존 링크와 기록은 유지됩니다</p>
                </>
              ) : <p>교사 없이 팀원끼리 실습합니다 기존 팀 기록을 이어서 엽니다 초기 배정액을 정하려면 새 실습 만들기를 선택하세요</p>}
              <button disabled={joining}>
                {joining ? "접속 중…" : teacherForm ? "방 생성" : teamForm ? newPractice ? "새 실습 시작" : "팀 실습 시작" : "입장"}
              </button>
            </form>
            <p>
              {teacherForm ? "교사 1명 · 팀 대표 3명 · 관찰자 최대 26명" : "팀 A·B·C를 직접 맡고 함께 조작할 수 있습니다"}
              {" "}기록은 자동 저장되며 같은 링크로 이어갑니다
            </p>
            {teacherForm && (
              <PracticeArchive
                classroom
                token={secret}
                name={name || "교사"}
                onResume={(c) => {
                  if ("token" in c) {
                    try {
                      sessionStorage.setItem(ROOM_KEY, JSON.stringify(c));
                    } catch {
                      setJoinError("브라우저 저장소를 사용할 수 없어 새로고침 시 다시 입장해야 합니다");
                    }
                    setCredential(c);
                    setSecret("");
                  }
                }}
              />
            )}
            {joinError && <p role="alert">{joinErrorMessage}</p>}
          </Panel>
        ) : (
          <>
            {teacher && (
              <PracticeArchive
                classroom
                token={credential.token}
                storage={room.state?.storage}
                onResume={() => undefined}
              />
            )}
            <Panel title={`방 ${credential.code}`}>
              <div className="button-row">
                <strong role="status">{room.connection}</strong>
                <span>
                  {me?.name} ·{" "}
                  {teacher
                    ? "교사"
                    : me?.team
                      ? `팀 ${me.team} 대표`
                      : teamMode ? "팀원" : "관찰자"}
                </span>
                {teamMode && (
                  <button className="secondary" disabled={disabled}
                    onClick={async () => {
                      if (await room.command("leave")) {
                        forget();
                        setTeacherForm(false);
                        setTeamForm(true);
                        setNewPractice(true);
                      }
                    }}>
                    새 실습 만들기
                  </button>
                )}
                {teamMode && (
                  <button
                    className="secondary"
                    disabled={disabled || saving}
                    onClick={async () => {
                      setSaving(true);
                      setSaveError("");
                      try {
                        const result = await teamRequest<{ savedAt: string }>(`/rooms/${credential.code}/save`, {}, credential.token);
                        setSavedAt(result.savedAt);
                      } catch (error) {
                        setSaveError(error instanceof Error ? error.message : "저장에 실패했습니다");
                      } finally { setSaving(false); }
                    }}
                  >
                    {saving ? "저장 중…" : "지금 저장"}
                  </button>
                )}
                <button
                  className="secondary"
                  disabled={disabled}
                  onClick={async () => {
                    if (teacher && !confirm("기록을 보관하고 모든 참가자의 수업을 종료할까요?"))
                      return;
                    const r = await room.command("leave");
                    if (r) forget(teacher);
                  }}
                >
                  {teacher ? "방 종료" : "퇴장"}
                </button>
                {room.connection === "수업 종료" && (
                  <button onClick={() => forget(teacher)}>
                    {teacher ? "새 수업 준비" : "새 방 입장 화면"}
                  </button>
                )}
              </div>
              <p>
                방 코드 <strong>{credential.code}</strong>
                {teamMode ? " · 서버가 재시작돼도 이 링크에서 기록을 이어갑니다" : " · 아래 QR로 참가할 수 있습니다"}
              </p>
              {teamMode && <p>{savedAt || room.state?.storage?.savedAt ? "저장됨 · 같은 링크로 이어가기" : "변경 내용 자동 저장"}</p>}
              {saveError && <p role="alert">{saveErrorMessage}</p>}
            </Panel>
            {controls && (
              <ShareRoom code={credential.code} publicUrl={publicUrl} />
            )}
            {room.error && (
              <p role="alert" className="notice">
                {roomErrorMessage}
              </p>
            )}
            {room.pending && (
              <div role="alert" className="notice">
                <p>
                  앞선 조작이 서버에서 처리됐을 수 있어 새 조작을 잠시 막았습니다
                  새로고침하거나 다시 거래를 만들지 말고 같은 요청의 결과를 확인하세요
                </p>
                <button
                  disabled={room.busy || room.connection !== "연결됨"}
                  onClick={room.retryPending}
                >
                  같은 요청 결과 확인
                </button>
              </div>
            )}
            {credential && room.connection !== "연결됨" && room.connection !== "수업 종료" && (
              <Notice>
                서버 연결을 복구하고 있습니다 연결되면 저장된 기록과 현재 역할을 다시 받습니다
              </Notice>
            )}
            {room.state?.storage?.error && (
              <Notice>
                저장 오류로 조작을 멈췄습니다 연결과 저장이 복구되면 실습을 다시 시작하세요
              </Notice>
            )}
            {room.state?.limited && (
              <Notice>
                이 수업의 실행 한도에 도달했습니다 현재 결과는 보관할 수 있지만 같은 기록을
                이어 열어도 한도는 초기화되지 않습니다
                {teamMode
                  ? " 이미 저장된 결과는 같은 링크에서 계속 확인할 수 있습니다"
                  : teacher
                  ? " 저장된 수업에서 결과를 확인한 뒤 방 종료를 누르고 새 수업을 준비하세요 이전 기록은 삭제되지 않습니다"
                  : " 교사가 다음 수업을 열면 새 참가 링크로 입장하세요"}
              </Notice>
            )}
            {room.state && (
              <>
                {teamMode ? (
                  <TeamPracticeGuide
                    key={`${room.state.code}:${room.state.lab.sessionId}`}
                    page={page}
                    team={me?.team || null}
                    room={room.state}
                    connected={guideConnected}
                    selectedTransactionId={guideTransactionId}
                    onSelectTransaction={selectGuideTransaction}
                    onNavigate={navigateFromGuide}
                    onRoles={() => focusGuideTarget("team-role-selection")}
                  />
                ) : (
                <Notice>
                  {teacher
                    ? "교사: 참가자 목록에서 세 팀 대표를 지정하세요 거래를 전달한 뒤 후보 준비 → 대표의 채굴 참여 → 전체 시작 순으로 진행합니다"
                    : me?.team
                      ? `팀 ${me.team} 대표입니다 거래 작성 → 전자서명 → 검증·전파를 누르세요 채굴 화면에서는 교사가 후보를 준비한 뒤 내 채굴 참여를 켭니다`
                      : "관찰자입니다 거래와 채굴 결과를 볼 수 있습니다 직접 실습하려면 교사에게 팀 대표 지정을 요청하세요"}
                </Notice>
                )}
                {teamMode && (
                  <section id="team-role-selection" tabIndex={-1} aria-label="팀 역할 선택">
                  <Panel title="내 팀 선택">
                    <div className="button-row">
                      {NODE_IDS.map((team) => {
                        const owner = room.state!.participants.find((p) => p.team === team);
                        return (
                          <button
                            key={team}
                            disabled={disabled || !!owner}
                            className={me?.team === team ? "" : "secondary"}
                            onClick={() => void room.command("claim", { team })}
                          >
                            {me?.team === team ? `팀 ${team} 맡는 중` : owner ? `팀 ${team} · ${owner.name}` : `팀 ${team} 맡기`}
                          </button>
                        );
                      })}
                      {me?.team && <button className="text-button" disabled={disabled} onClick={() => void room.command("release")}>대표 내려놓기</button>}
                    </div>
                    <p>공통 채굴·전파는 누구나 조작할 수 있습니다 팀 대표는 한 명씩 맡습니다</p>
                  </Panel>
                  </section>
                )}
                <Notice>
                  각 팀의 지갑은 서명하고 노드는 검증합니다 채굴 계산은 공유
                  서버에서 수행합니다 학생 기기가 독립 노드로 실행되는 방식은
                  아닙니다
                </Notice>
                <details className="classroom-participants">
                  <summary>
                    참가자 {room.state.participants.length}/30{teamMode ? "" : " · 역할 배정"}
                    {teacher ? " (교사)" : ""}
                  </summary>
                  <div className="classroom-people">
                    {room.state.participants.map((p) => (
                      <div key={p.id}>
                        <strong>{p.name}</strong> ·{" "}
                        {p.connected ? "● 접속" : "○ 연결 대기"} ·{" "}
                        {p.role === "teacher"
                          ? "교사"
                          : p.team
                            ? `팀 ${p.team}`
                            : teamMode ? "팀원" : "관찰자"}
                        {teacher && p.role !== "teacher" && (
                          <div className="button-row">
                            {NODE_IDS.map((n) => (
                              <button
                                key={n}
                                disabled={disabled}
                                aria-label={`${p.name} 팀 ${n} 대표 지정`}
                                onClick={() =>
                                  void room.command("assign", {
                                    team: n,
                                    participantId: p.id,
                                  })
                                }
                              >
                                {n} 대표 지정
                              </button>
                            ))}
                            <button
                              disabled={disabled}
                              className="text-button"
                              aria-label={`${p.name} 참가자 내보내기`}
                              onClick={() => setRemoving(p.id)}
                            >
                              참가자 내보내기
                            </button>
                            {removing === p.id && (
                              <div role="group" aria-label="참가자 내보내기 확인">
                                <p>
                                  {p.name}의 접속과 대표 권한을 종료합니다 담당 채굴은
                                  멈추며 이전 거래·수업 기록은 남습니다
                                </p>
                                <button
                                  disabled={disabled}
                                  onClick={async () => {
                                    const result = await room.command("remove", { participantId: p.id });
                                    if (result) setRemoving(null);
                                  }}
                                >
                                  내보내기 확인
                                </button>
                                <button onClick={() => setRemoving(null)}>취소</button>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
                <nav className="button-row" aria-label="공동 실습 화면">
                  {["거래", "채굴", "노드·블록체인", "활동 기록"].map((p) => (
                    <button
                      className={page === p ? "" : "secondary"}
                      key={p}
                      aria-current={page === p ? "page" : undefined}
                      onClick={() => setPage(p)}
                    >
                      {p}
                    </button>
                  ))}
                  {teamMode && (
                    <button className="text-button" onClick={() => focusGuideTarget("team-practice-guide")}>
                      가이드로 돌아가기
                    </button>
                  )}
                </nav>
                <section id="team-practice-content" className="team-practice-content" tabIndex={-1} aria-label={`${page} 실습 화면`}>
                <div hidden={page !== "거래"}>
                  <TeamWallet
                    key={`${credential.runId}:${credential.participantId}:${me?.assignmentId ?? `${me?.role}:${me?.team}`}`}
                    lab={room.state.lab}
                    teamMode={teamMode}
                    team={me?.team || null}
                    observedTransactionId={teamMode ? guideTransactionId : undefined}
                    onObserveTransaction={teamMode ? selectGuideTransaction : undefined}
                    disabled={learningDisabled}
                    command={room.command}
                  />
                </div>
                {page === "채굴" && (
                  <ClassRace
                    state={room.state}
                    teacher={controls}
                    team={me?.team || null}
                    disabled={learningDisabled}
                    command={room.command}
                  />
                )}
                {page === "노드·블록체인" && (
                  <>
                    <Panel title="전달과 독립 검증">
                      <p>
                        메시지 {room.state.lab.queue.length}개 대기 · 각 수신
                        노드가 검증한 뒤 자기 체인과 멤풀을 변경합니다
                      </p>
                      {controls && (
                        <div className="button-row">
                          <button
                            disabled={disabled || !room.state.lab.queue.length}
                            onClick={() => void room.command("deliver")}
                          >
                            메시지 한 개 전달
                          </button>
                          <label>
                            <input
                              type="checkbox"
                              checked={room.state.automatic}
                              disabled={disabled}
                              onChange={(e) =>
                                void room.command("automatic", {
                                  enabled: e.target.checked,
                                })
                              }
                            />{" "}
                            메시지 자동 전달
                          </label>
                        </div>
                      )}
                      <ul>
                        {room.state.lab.queue.slice(0, 20).map((m) => (
                          <li key={m.id}>
                            {m.from} → {m.to} · {m.kind} ·{" "}
                            {short(m.payloadId || "")}
                          </li>
                        ))}
                      </ul>
                    </Panel>
                    <div className="classroom-grid">
                      {NODE_IDS.map((n) => (
                        <Panel key={n} title={`노드 ${n}`}>
                          <p>
                            활성 높이{" "}
                            {room.state!.lab.nodes[n].chain.at(-1)?.height} ·
                            누적 작업량 {room.state!.lab.nodes[n].chainWork}
                          </p>
                          <p>
                            멤풀 {room.state!.lab.nodes[n].mempool.length}건 ·
                            부모 블록 대기{" "}
                            {room.state!.lab.nodes[n].pendingBlocks}
                          </p>
                          <details>
                            <summary>분기와 체인 상세</summary>
                            {room
                              .state!.lab.nodes[n].branches?.slice(-12)
                              .map((b) => (
                                <p key={b.hash}>
                                  #{b.height}{" "}
                                  {b.active ? "✓ 활성" : "분기 보관"} ·{" "}
                                  {short(b.hash)} ← {short(b.previousHash)}
                                </p>
                              ))}
                            <pre>
                              {JSON.stringify(
                                room.state!.lab.nodes[n].chain.slice(-3),
                                null,
                                2,
                              )}
                            </pre>
                          </details>
                        </Panel>
                      ))}
                    </div>
                  </>
                )}
                {page === "활동 기록" && (
                  <Panel title="누가 무엇을 바꿨나요?">
                    <ol>
                      {room.state.activity
                        .slice()
                        .reverse()
                        .map((a) => (
                          <li key={a.seq}>
                            <strong>{a.actor}</strong> · {uiText(a.message)}
                          </li>
                        ))}
                    </ol>
                  </Panel>
                )}
                </section>
              </>
            )}
          </>
        )}
      </main>
      <footer>
        학습용 모의 환경 · 실습 기록은 서버에 자동 저장됩니다
      </footer>
    </div>
  );
}

type Command = ReturnType<typeof useClassroom>["command"];
function TeamWallet({
  lab,
  teamMode,
  team,
  disabled,
  command,
  observedTransactionId,
  onObserveTransaction,
}: {
  lab: LabSnapshot;
  teamMode: boolean;
  team: NodeId | null;
  disabled: boolean;
  command: Command;
  observedTransactionId?: string | null;
  onObserveTransaction?: (id: string | null) => void;
}) {
  const [recipient, setRecipient] = useState(
    lab.wallets[team === "A" ? 1 : 0].address,
  );
  const [amount, setAmount] = useState(30);
  const [fee, setFee] = useState(1);
  const [reward, setReward] = useState(false);
  const [manual, setManual] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [draft, setDraft] = useState<DraftResult | null>(null);
  const [signed, setSigned] = useState<Transaction | null>(null);
  const [result, setResult] = useState("");
  const node = lab.nodes[team || "A"];
  const own = team
    ? reward
      ? lab.minerWallets![team]
      : lab.wallets[NODE_IDS.indexOf(team)]
    : null;
  const [observed, setObserved] = useState("");
  const allTransactions = [
    ...new Map(
      NODE_IDS.flatMap((n) => [
        ...lab.nodes[n].mempool,
        ...lab.nodes[n].chain.flatMap((b) =>
          b.transactions.filter((t) => !t.kind || t.kind === "regular"),
        ),
      ]).map((t) => [t.id, t]),
    ).values(),
  ];
  const recentTransactions = allTransactions.slice(-20);
  const selectedId = observedTransactionId === undefined ? observed : observedTransactionId;
  const selectedTransaction = allTransactions.find((transaction) => transaction.id === selectedId);
  const txs = selectedTransaction && !recentTransactions.some((transaction) => transaction.id === selectedId)
    ? [selectedTransaction, ...recentTransactions] : recentTransactions;
  const chosen = observedTransactionId === undefined
    ? observed || txs.at(-1)?.id : observedTransactionId;
  const observe = (id: string) => {
    if (onObserveTransaction) onObserveTransaction(id || null);
    else setObserved(id);
  };
  function clear() {
    setDraft(null);
    setSigned(null);
    setResult("");
  }
  return (
    <>
      <Panel title="한 거래가 지금 어디에 있나요?">
        <label>
          관찰 거래
          <select
            value={chosen || ""}
            onChange={(e) => observe(e.target.value)}
          >
            <option value="">거래 선택</option>
            {txs.map((t) => (
              <option value={t.id} key={t.id}>
                {short(t.id)}
              </option>
            ))}
          </select>
        </label>
        <div className="classroom-grid">
          {NODE_IDS.map((n) => {
            const b = lab.nodes[n].chain.find((b) =>
              b.transactions.some((t) => t.id === chosen),
            );
            return (
              <p key={n}>
                <strong>노드 {n}</strong> ·{" "}
                {b
                  ? `✓ 블록 #${b.height} 포함`
                  : lab.nodes[n].mempool.some((t) => t.id === chosen)
                    ? "◷ Mempool 대기"
                    : "미수신·미등록"}
              </p>
            );
          })}
        </div>
      </Panel>
      <TeamWalletBalances lab={lab} />
      {!team ? (
        <Notice>
          {teamMode
            ? "위에서 내 팀을 선택하면 송금과 채굴에 참여할 수 있습니다"
            : "팀 대표의 송금과 각 노드의 상태를 관찰하세요 교사가 대표를 지정하면 거래를 작성할 수 있습니다"}
        </Notice>
      ) : (
        <Panel title={`팀 ${team} · UTXO 선택 → 서명 → 검증·전파`}>
          <fieldset disabled={disabled} className="classroom-form">
            <label>
              보내는 지갑
              <select
                value={reward ? "reward" : "team"}
                onChange={(e) => {
                  setReward(e.target.value === "reward");
                  setSelected([]);
                  clear();
                }}
              >
                <option value="team">팀 {team} 초기 지갑</option>
                <option value="reward">팀 {team} 채굴 보상 지갑</option>
              </select>
            </label>
            <label>
              받는 팀
              <select
                value={recipient}
                onChange={(e) => {
                  setRecipient(e.target.value);
                  clear();
                }}
              >
                {lab.wallets.map((w) => (
                  <option key={w.address} value={w.address}>
                    {w.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              송금액
              <input
                type="number"
                min={1}
                required
                value={amount}
                onChange={(e) => {
                  setAmount(Number(e.target.value));
                  clear();
                }}
              />
            </label>
            <label>
              수수료
              <input
                type="number"
                min={0}
                value={fee}
                onChange={(e) => {
                  setFee(Number(e.target.value));
                  clear();
                }}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={manual}
                onChange={(e) => {
                  setManual(e.target.checked);
                  clear();
                }}
              />{" "}
              UTXO 직접 선택
            </label>
            {manual &&
              Object.entries(node.utxos)
                .filter(([, u]) => u.address === own?.address)
                .map(([key, u]) => {
                  const blocked =
                    node.reserved.includes(key) ||
                    (u.coinbase &&
                      node.chain.at(-1)!.height + 1 - (u.createdHeight || 0) <
                        100);
                  return (
                    <label key={key}>
                      <input
                        type="checkbox"
                        disabled={blocked}
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
                      {short(u.txId)}:{u.outputIndex} · {u.amount} 코인{" "}
                      {blocked ? "예약·미성숙" : "사용 가능"}
                    </label>
                  );
                })}
            <p>
              TXID와 출력 번호는 식별자입니다 금액과 사용 조건은 해당 출력의
              정보입니다
            </p>
            <div className="button-row">
              <button
                onClick={async () => {
                  const r = await command("draft", {
                    senderAddress: own!.address,
                    recipientAddress: recipient,
                    amount,
                    fee,
                    ...(manual ? { selectedInputs: selected } : {}),
                  });
                  if (r?.draft) {
                    setDraft(r as DraftResult);
                    setSigned(null);
                    setResult("");
                  }
                }}
              >
                1. 거래 작성
              </button>
              <button
                disabled={disabled || !draft || !!signed}
                onClick={async () => {
                  const r = await command("sign", {
                    draftId: draft!.signingHash,
                  });
                  if (r?.signed) setSigned(r.signed);
                }}
              >
                2. 전자서명
              </button>
              <button
                disabled={disabled || !signed}
                onClick={async () => {
                  const r = await command("submit", {
                    transactionId: signed!.id,
                  });
                  if (r?.validation) {
                    setResult(uiText(r.validation.message));
                    if (r.validation.valid) {
                      observe(signed!.id);
                      setSigned(null);
                      setDraft(null);
                      setSelected([]);
                    }
                  }
                }}
              >
                3. 검증·전파
              </button>
            </div>
          </fieldset>
          {draft && (
            <div className="notice">
              <p>
                입력 {draft.inputs.reduce((s, u) => s + u.amount, 0)} →{" "}
                {draft.draft.outputs
                  .map(
                    (o) =>
                      `${lab.wallets.find((w) => w.address === o.address)?.name || "내 지갑"} ${o.amount}`,
                  )
                  .join(" + ")}{" "}
                + 수수료 {fee}
              </p>
              <p>
                {signed
                  ? "✓ 개인키로 서명했습니다 노드는 공개키로 서명을 검증합니다"
                  : "① 보낼 내용을 확인하세요 아직 서명하지 않았습니다"}{" "}
                서명은 암호화·복호화가 아닙니다
              </p>
              <details>
                <summary>TXID·서명 원문 상세</summary>
                <pre>{JSON.stringify(signed || draft, null, 2)}</pre>
              </details>
            </div>
          )}
          {result && <p role="status">{result}</p>}
        </Panel>
      )}
    </>
  );
}

function ClassRace({
  state,
  teacher,
  team,
  disabled,
  command,
}: {
  state: RoomState;
  teacher: boolean;
  team: NodeId | null;
  disabled: boolean;
  command: Command;
}) {
  const { lab } = state,
    race = lab.race;
  const [guided, setGuided] = useState(race?.guided ?? true);
  const [mode, setMode] = useState(race?.mode ?? "learning");
  const [miningView, setMiningView] = useState<"single" | "competition">(race?.singleNode ? "single" : "competition");
  const [selectedNode, setSelectedNode] = useState<NodeId>(race?.singleNode || team || "A");
  const act = (action: string, payload: object = {}) =>
    void command(action, { raceId: race?.id, ...payload });
  return (
    <>
      {state.teamMode && <Panel title="채굴 방식">
        <div className="button-row" role="group" aria-label="채굴 방식 선택">
          <button className={miningView === "single" ? "" : "secondary"} aria-pressed={miningView === "single"}
            onClick={() => setMiningView("single")}>한 노드 채굴</button>
          <button className={miningView === "competition" ? "" : "secondary"} aria-pressed={miningView === "competition"}
            onClick={() => setMiningView("competition")}>채굴 경쟁</button>
        </div>
        <p>한 노드로 논스·해시 변화를 관찰하거나 세 팀이 함께 경쟁할 수 있습니다 화면을 바꿔도 실행 상태는 바뀌지 않습니다</p>
        {race && <p role="status">현재 후보: {race.singleNode ? `노드 ${race.singleNode}만 채굴` : "세 팀 경쟁"} · {race.status === "running" ? "계산 중" : race.status === "paused" ? "일시정지" : "중지"}</p>}
      </Panel>}
      {state.teamMode && miningView === "single" ? <SingleNodeMining
        race={race} selected={selectedNode} select={setSelectedNode} disabled={disabled} command={command}
      /> : <>
      <Panel title="세 팀의 채굴 경쟁">
        <p>
          {race?.message ||
            (state.teamMode
              ? "세 후보 준비 → 내 채굴 참여 → 전체 시작 순으로 진행하세요"
              : "교사가 후보를 준비하면 팀 대표가 채굴 참여를 켭니다")}
        </p>
        <p>
          전체 상태: {race?.status || "준비 전"} ·{" "}
          {race?.guided ? "안내 실습: 첫 발견에서 전체 일시정지" : "자유 탐색"}
        </p>
        {teacher && (
          <fieldset disabled={disabled}>
            <div className="button-row">
              <label>
                실습 방식
                <select
                  value={guided ? "guided" : "free"}
                  onChange={(e) => {
                    const v = e.target.value === "guided";
                    setGuided(v);
                    if (race) act("configure", { guided: v });
                  }}
                >
                  <option value="guided">안내 실습</option>
                  <option value="free">자유 탐색</option>
                </select>
              </label>
              <label>
                계산 속도
                <select
                  value={mode}
                  onChange={(e) =>
                    setMode(e.target.value as "learning" | "fast")
                  }
                >
                  <option value="learning">천천히 · 실제 한 번씩</option>
                  <option value="fast">빠르게 · 묶음 계산</option>
                </select>
              </label>
              <button onClick={() => act("prepare", { guided, mode })}>
                세 후보 준비
              </button>
              <button
                disabled={disabled || !race || !!race.singleNode}
                onClick={() => act("resume")}
              >
                전체 시작 / 계속
              </button>
              <button disabled={disabled || !race || !!race.singleNode} onClick={() => act("pause")}>
                전체 일시정지
              </button>
              <button
                disabled={disabled || !race || !!race.singleNode || race.status !== "paused"}
                onClick={() => act("step")}
              >
                각자 한 번 계산
              </button>
              <button disabled={disabled || !race || !!race.singleNode} onClick={() => act("stop")}>
                경쟁 중지
              </button>
            </div>
            <p>
              속도 변경은 다음 후보 준비에 적용합니다 새 후보를 준비하면 대표가
              다시 참여해야 합니다
            </p>
          </fieldset>
        )}
        {race?.singleNode && <p>경쟁하려면 세 후보 준비를 누르고 각 팀이 다시 참여하세요</p>}
      </Panel>
      </>}
      <div className={`classroom-grid${state.teamMode && miningView === "single" ? " single-mining-grid" : ""}`}>
        {(state.teamMode && miningView === "single" ? [selectedNode] : NODE_IDS).map((n) => {
          const job = race?.jobs[n],
            node = lab.nodes[n];
          const reward = Object.entries(node.utxos).find(
            ([, u]) =>
              u.address === lab.minerWallets?.[n].address &&
              u.coinbase &&
              node.chain.at(-1)!.height + 1 - (u.createdHeight || 0) < 100,
          );
          const bal = node.balances[lab.minerWallets![n].address];
          const representativeOnline = state.participants.some((person) => person.team === n && person.connected);
          const canAdvance = !!race && !race.advance && (race.singleNode ? race.singleNode === n : representativeOnline);
          return (
            <Panel key={n} title={`채굴자 ${n}`} className="race-card">
              <p>
                {race?.enabled[n] ? "▶ 참여" : "Ⅱ 개별 일시정지"} · 활성 높이{" "}
                {node.chain.at(-1)?.height}
              </p>
              <p>
                후보 #{job?.block.height || "—"} · 시도 {job?.attempts || 0}회 ·
                논스 {job?.samples.at(-1)?.nonce ?? "—"}
              </p>
              <code className="hash-sample">
                {job?.samples.at(-1)?.hash || "첫 계산 대기"}
              </code>
              <p>
                멤풀 {node.mempool.length}건 · 보상은 자기 블록의 코인베이스로
                청구합니다
              </p>
              {team === n && !race?.singleNode && !(state.teamMode && miningView === "single") && (
                <button
                  disabled={disabled || !race || !!race.advance}
                  onClick={() =>
                    act("participate", { enabled: !race?.enabled[n] })
                  }
                >
                  {race?.enabled[n] ? "내 채굴 멈춤" : "내 채굴 참여"}
                </button>
              )}
              <p>
                보상 지갑: {bal?.confirmed || 0} · 미성숙 {bal?.immature || 0} ·
                사용 가능 {bal?.spendable || 0}
              </p>
              {teacher && reward && (
                <button
                  disabled={disabled || !canAdvance}
                  onClick={() =>
                    act("advance", { nodeId: n, reward: reward[0] })
                  }
                >
                  팀 {n} 보상 성숙까지 진행
                </button>
              )}
              {teacher && reward && !race?.singleNode && !representativeOnline && <small>이 팀 대표가 접속하면 보상 성숙을 진행할 수 있습니다</small>}
              {teacher && reward && race?.singleNode && race.singleNode !== n && <small>현재 채굴 노드 {race.singleNode}의 보상만 진행할 수 있습니다</small>}
              <details>
                <summary>후보·실제 계산 표본</summary>
                <pre>
                  {JSON.stringify(
                    { candidate: job?.block, samples: job?.samples },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </Panel>
          );
        })}
      </div>
      <Panel title="발견 → 전파 → 독립 검증">
        <ul>
          {race?.completed.map((b) => (
            <li key={b.hash}>
              채굴자 {b.node} · #{b.height} · {b.attempts}회 · {short(b.hash)}{" "}
              (발견 기록)
            </li>
          ))}
        </ul>
        <p>
          전달 대기 {lab.queue.length}개 수신 전의 다른 노드는 기존 후보를 계속
          계산할 수 있습니다
        </p>
        {teacher && (
          <div className="button-row">
            <button
              disabled={disabled || !lab.queue.length}
              onClick={() => act("deliver")}
            >
              메시지 한 개 전달
            </button>
            <label>
              <input
                type="checkbox"
                checked={state.automatic}
                disabled={disabled}
                onChange={(e) =>
                  act("automatic", { enabled: e.target.checked })
                }
              />{" "}
              메시지 자동 전달
            </label>
          </div>
        )}
        <p>
          보조금 50은 고정 학습용 값입니다 코인베이스는 일반 블록의 첫 거래이며
          보조금과 수수료를 청구합니다 보상은 생성 높이보다 100 이상 높은
          블록에서 사용할 수 있습니다
        </p>
      </Panel>
      {teacher && !(state.teamMode && miningView === "single") && (
        <Panel title="심화: 같은 높이의 두 블록">
          <p>
            전파를 보류하고 각 대표가 참여합니다 같은 높이의 블록을 둘 이상
            발견하면 멈춘 뒤 전달하세요 동률 분기는 보관되고, 더 큰 누적
            작업량의 체인을 받으면 재구성됩니다
          </p>
          <button
            disabled={disabled}
            onClick={() => {
              setGuided(false);
              act("fork", { guided: false, mode });
            }}
          >
            전파 보류 분기 실험 준비
          </button>
        </Panel>
      )}
    </>
  );
}

function ShareRoom({ code, publicUrl }: { code: string; publicUrl: string }) {
  const [copied, setCopied] = useState(false);
  const origin =
    publicUrl ||
    (!["localhost", "127.0.0.1", "::1"].includes(location.hostname)
      ? location.origin
      : "");
  const url = origin
    ? `${origin.replace(/\/$/, "")}/?classroom=join&room=${code}`
    : "";
  const qr = useMemo(() => {
    if (!url) return "";
    const code = qrcode(0, "M");
    code.addData(url);
    code.make();
    return code.createDataURL(5, 20);
  }, [url]);
  return (
    <details className="classroom-participants">
      <summary>휴대폰 참여 · 링크 복사 / QR 코드</summary>
      {url ? (
        <div className="classroom-share">
          <img src={qr} width={220} height={220} alt="참가자 접속 QR 코드" />
          <div>
            <p>
              휴대폰 카메라로 QR 코드를 읽으세요 앱 설치는 필요 없습니다 방
              코드는 자동 입력됩니다
            </p>
            <a href={url}>{url}</a>
            <p>
              <button
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(url);
                    setCopied(true);
                  } catch {
                    setCopied(false);
                  }
                }}
              >
                참가 링크 복사
              </button>{" "}
              {copied
                ? "복사됨"
                : "복사가 안 되면 위 주소를 길게 눌러 복사하세요"}
            </p>
          </div>
        </div>
      ) : (
        <p>
          현재는 로컬 접속입니다 Start-Classroom.ps1 -Share로 실행하면 참가자
          주소와 QR 코드가 표시됩니다
        </p>
      )}
    </details>
  );
}
