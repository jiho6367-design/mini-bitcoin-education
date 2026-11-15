import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { createSequencer } from "../api/sequencer";
import type {
  RaceRequest,
  AnySnapshot,
  Block,
  LabSnapshot,
  MiningMode,
  MiningResult,
  MiningSample,
  MiningSnapshot,
  NodeId,
  SetupSnapshot,
  TamperRequest,
  Transaction,
  UnsignedTransaction,
} from "../api/types";
import { restoreLessons, type TransactionLesson } from "./learning";
import { uiText } from "../copy";

export interface MiningState {
  jobId?: string;
  status: "idle" | "running" | "paused" | "found" | "stopped" | "error";
  attempts: number;
  elapsedMs: number;
  block?: Block;
  node?: NodeId;
  message?: string;
  mode: MiningMode;
  observationMs: number;
  samples: MiningSample[];
}
const initialMining: MiningState = {
  status: "idle",
  attempts: 0,
  elapsedMs: 0,
  mode: "fast",
  observationMs: 0,
  samples: [],
};
const initialComposer = { sender: "", receiver: "", amount: "30", fee: "1" };
const miningState = (m: MiningSnapshot): MiningState => ({
  jobId: m.jobId,
  status: m.status,
  attempts: m.attempts,
  elapsedMs: m.elapsedMs,
  block: m.block,
  node: m.node,
  message: m.message ?? undefined,
  mode: m.mode,
  observationMs: m.observationMs,
  samples: m.samples,
});
export const errorMessage = (e: unknown) =>
  e instanceof Error ? uiText(e.message) : "요청을 처리하지 못했습니다";

// 응답이 도착한 순서와 무관하게 가장 최근 상태만 반영하기 위한 기준값
const disconnected = () => ({
  labId: null as string | null,
  revision: -1,
  lab: null as LabSnapshot | null,
  jobId: "",
  seq: -1,
  miningActive: false,
});

export function useLab() {
  const [lab, setLab] = useState<LabSnapshot | null>(null);
  const [setup, setSetup] = useState<SetupSnapshot | null>(null);
  const [automatic, setAutomatic] = useState(false);
  const [lesson, setLesson] = useState<TransactionLesson | null>(null);
  const [guided, setGuided] = useState(true);
  const [observationVersion, setObservationVersion] = useState(0);
  const [history, setHistory] = useState<TransactionLesson[]>([]);
  const [composer, setComposer] = useState(initialComposer);
  const [mining, setMining] = useState<MiningState>(initialMining);
  const [error, setError] = useState("");
  const [sequencer] = useState(createSequencer);
  const live = useRef(disconnected());
  const tickTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  const stopTicking = useCallback(() => {
    clearTimeout(tickTimer.current);
    tickTimer.current = undefined;
  }, []);
  // 현재 실습의 더 새로운 상태만 화면에 반영한다
  const accept = useCallback((snapshot: AnySnapshot) => {
    const state = live.current;
    if (snapshot.labId !== state.labId || snapshot.revision < state.revision)
      return;
    state.revision = snapshot.revision;
    if (snapshot.phase === "running") {
      state.lab = snapshot;
      setLab(snapshot);
      setSetup(null);
      if (snapshot.race) setGuided(snapshot.race.guided);
    } else setSetup(snapshot);
  }, []);
  // 진행 중인 요청을 끊고 실행 세션을 닫는다 DB 기록은 유지한다
  const disconnect = useCallback(() => {
    sequencer.reset();
    stopTicking();
    const previous = live.current.labId;
    live.current = disconnected();
    if (previous) void api.deleteLab(previous).catch(() => undefined);
  }, [sequencer, stopTicking]);
  useEffect(
    () => () => {
      sequencer.reset();
      stopTicking();
    },
    [sequencer, stopTicking],
  );

  /** 요청을 누른 순서대로 보내고, 응답에 실습 상태가 있으면 화면에 반영한다 */
  const call = useCallback(
    <T>(task: (labId: string) => Promise<T | null>) =>
      sequencer.run(async (alive) => {
        const labId = live.current.labId;
        if (!labId) return null;
        const result = await task(labId);
        if (!alive()) return null;
        const snapshot = (result as { lab?: AnySnapshot | null } | null)?.lab;
        if (snapshot) accept(snapshot);
        return result;
      }),
    [sequencer, accept],
  );

  useEffect(() => {
    if (!automatic || !lab?.labId) return;
    let delivering = false;
    const timer = setInterval(() => {
      if (delivering || !live.current.lab?.queue.length) return;
      delivering = true;
      call((id) => api.deliverNext(id))
        .catch((e: unknown) => {
          setAutomatic(false);
          setError(errorMessage(e));
        })
        .finally(() => {
          delivering = false;
        });
    }, 700);
    return () => clearInterval(timer);
  }, [automatic, lab?.labId, call]);

  function clearScreen() {
    setHistory([]);
    setMining(initialMining);
    setLesson(null);
    setComposer(initialComposer);
    setAutomatic(false);
    setError("");
  }
  function resumePractice(snapshot: AnySnapshot) {
    disconnect();
    clearScreen();
    live.current.labId = snapshot.labId;
    accept(snapshot);
    if (snapshot.phase === "running") {
      const lessons = restoreLessons(snapshot);
      setHistory(lessons);
      const latest = lessons.at(-1) ?? null;
      selectLesson(latest);
      setGuided(snapshot.race?.guided ?? true);
    }
    if (snapshot.phase === "running" && snapshot.mining) {
      receiveMining(
        snapshot.labId,
        { mining: snapshot.mining, lab: snapshot },
        true,
      );
    }
  }
  function selectLesson(value: TransactionLesson | null) {
    setObservationVersion(version => version + 1);
    setLesson(value);
    if (!value) return;
    const tx = value.checked ?? value.signed ?? value.draft;
    setComposer({ sender: value.inputs[0]?.address ?? "", receiver: tx.outputs[0]?.address ?? "", amount: String(tx.outputs[0]?.amount ?? 30), fee: String(value.inputs.reduce((sum, input) => sum + input.amount, 0) - tx.outputs.reduce((sum, output) => sum + output.amount, 0)) });
  }
  async function reset() {
    try {
      const id = live.current.labId;
      if (id) {
        try {
          await api.deleteLab(id);
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404)) throw e;
        }
      }
      disconnect();
      setLab(null);
      setSetup(null);
      clearScreen();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    }
  }
  async function start(difficulty: number, example = false): Promise<boolean> {
    try {
      const manual = !example && live.current.labId && !live.current.lab;
      if (!manual) disconnect();
      const result = await sequencer.run(async (alive) => {
        const labId = live.current.labId;
        const started = labId
          ? await api.startLab(labId, difficulty)
          : await api.createLab({ mode: "example", difficulty });
        if (!alive()) return null;
        live.current.labId = started.lab.labId;
        accept(started.lab);
        return started;
      });
      if (!result) return false;
      clearScreen();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    }
  }
  async function addWallet() {
    try {
      await sequencer.run(async (alive) => {
        if (!live.current.labId) {
          // 실습 시작 전의 지갑도 백엔드가 만들고 보관한다
          const created = await api.createLab({ mode: "manual" });
          if (!alive()) return null;
          live.current.labId = created.lab.labId;
          accept(created.lab);
        }
        const added = await api.addWallet(live.current.labId!);
        if (alive()) accept(added.lab);
        return added;
      });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function receiveMining(labId: string, result: MiningResult, started = false) {
    const state = live.current;
    if (state.labId !== labId) return;
    const m = result.mining;
    if (started) {
      state.jobId = m.jobId;
      state.seq = -1;
    }
    // 초기화·새 작업 이후에 도착한 이전 작업의 결과와 순서가 뒤바뀐 응답은 무시한다
    if (m.jobId !== state.jobId || m.seq < state.seq) return;
    state.seq = m.seq;
    state.miningActive = m.status === "running" || m.status === "paused";
    if (result.lab) accept(result.lab);
    setMining(miningState(m));
    stopTicking();
    // 백엔드는 요청을 받을 때만 계산한다 다음 계산 시점은 응답이 알려 준다
    if (m.nextTickInMs !== null)
      tickTimer.current = setTimeout(
        () => void command(labId, m.jobId, "tick"),
        m.nextTickInMs,
      );
  }
  async function command(
    labId: string,
    jobId: string,
    type: "tick" | "pause" | "resume" | "step" | "stop",
  ) {
    try {
      receiveMining(labId, await api.miningCommand(labId, jobId, type));
    } catch (e) {
      const state = live.current;
      if (state.labId !== labId || state.jobId !== jobId) return;
      if (e instanceof ApiError && e.code === "STALE_JOB") return;
      stopTicking();
      state.miningActive = false;
      setMining((m) => ({ ...m, status: "error", message: errorMessage(e) }));
    }
  }
  async function beginMining(
    node: NodeId,
    step: boolean,
    mode: MiningMode = "fast",
  ) {
    const labId = live.current.labId;
    if (!labId) return;
    try {
      const result = await sequencer.run(async () =>
        live.current.miningActive
          ? null
          : api.startMining(labId, { nodeId: node, mode, step }),
      );
      if (!result) return;
      receiveMining(labId, result, true);
      setError("");
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function miningCommand(type: "pause" | "resume" | "step" | "stop") {
    const { labId, jobId } = live.current;
    if (!labId || !jobId) return;
    if (type === "pause" || type === "stop") stopTicking();
    await command(labId, jobId, type);
  }

  const raceAction = useCallback(
    async (body: RaceRequest) => {
      if (["prepare", "resume", "step", "advance"].includes(body.action)) {
        stopTicking();
        live.current.jobId = "";
        live.current.miningActive = false;
        live.current.seq = -1;
        setMining(initialMining);
      }
      try {
        const result = await call((id) => api.race(id, body));
        if (
          result &&
          !result.lab &&
          result.race &&
          live.current.lab &&
          result.labId === live.current.labId &&
          result.revision !== undefined
        )
          accept({
            ...live.current.lab,
            revision: result.revision,
            race: result.race,
          });
      } catch (e) {
        setError(errorMessage(e));
        if (e instanceof ApiError && e.code === "SAVE_FAILED") {
          setAutomatic(false);
          setLab((current) =>
            current?.race
              ? { ...current, race: { ...current.race, status: "paused" } }
              : current,
          );
        }
        throw e;
      }
    },
    [call, stopTicking, accept],
  );

  useEffect(() => {
    const race = lab?.race;
    if (!race || race.status !== "running") return;
    const timer = setTimeout(
      () => {
        void raceAction({ action: "tick", raceId: race.id }).catch(
          () => undefined,
        );
      },
      race.advance || race.mode === "fast" ? 30 : 250,
    );
    return () => clearTimeout(timer);
  }, [lab?.race, raceAction]);

  useEffect(() => {
    if (!lesson?.signed || !lesson.validation?.valid) return;
    setHistory((previous) =>
      [
        ...previous.filter((item) => item.signed?.id !== lesson.signed?.id),
        lesson,
      ].slice(-20),
    );
  }, [lesson]);

  const actions = useMemo(
    () => ({
      draft: (
        node: NodeId,
        senderAddress: string,
        recipientAddress: string,
        amount: number,
        fee = 1,
        selectedInputs?: string[],
      ) =>
        call((id) =>
          api.draft(id, node, {
            senderAddress,
            recipientAddress,
            amount,
            fee,
            selectedInputs,
          }),
        ),
      sign: (draft: UnsignedTransaction) => call((id) => api.sign(id, draft)),
      submit: (node: NodeId, tx: Transaction) =>
        call(async (id) => {
          // 앞선 제출이 끝난 뒤의 최신 상태에서 이미 등록된 거래는 다시 보내지 않는다
          const current = live.current.lab?.nodes[node];
          const known = (t: Transaction) => t.id === tx.id;
          if (
            current?.mempool.some(known) ||
            current?.chain.some((b) => b.transactions.some(known))
          )
            return null;
          return api.submit(id, node, tx);
        }),
      forgedSignature: (node: NodeId, tx: Transaction) =>
        call((id) => api.forgedSignature(id, node, tx)),
      doubleSpend: (node: NodeId) => call((id) => api.doubleSpend(id, node)),
      deliverNext: () => call((id) => api.deliverNext(id)),
      invalidPowBlock: (node: NodeId) =>
        call((id) => api.invalidPowBlock(id, node)),
      tamper: (node: NodeId, body: TamperRequest) =>
        call((id) => api.tamper(id, node, body)),
      privateKey: (address: string) =>
        call((id) => api.privateKey(id, address)),
    }),
    [call],
  );

  return {
    lab,
    storage: lab?.storage ?? setup?.storage,
    resumePractice,
    guided,
    setGuided,
    history,
    raceAction,
    lesson,
    setLesson,
    selectLesson,
    observationVersion,
    composer,
    setComposer,
    wallets: lab?.wallets ?? setup?.wallets ?? [],
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
  };
}
export type LabActions = ReturnType<typeof useLab>["actions"];
