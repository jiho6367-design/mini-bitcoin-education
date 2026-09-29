import { useCallback, useEffect, useRef, useState } from "react";
import { createWallet } from "../core/crypto";
import { Lab } from "../core/lab";
import type { Block, NodeId, Wallet } from "../core/types";
import type { MiningCommand, MiningUpdate } from "../mining/protocol";

export interface MiningState {
  status: "idle" | "running" | "paused" | "found" | "stopped" | "error";
  attempts: number;
  elapsedMs: number;
  block?: Block;
  node?: NodeId;
  message?: string;
}
const initialMining: MiningState = {
  status: "idle",
  attempts: 0,
  elapsedMs: 0,
};
export function useLab() {
  const [lab, setLab] = useState<Lab | null>(null);
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [automatic, setAutomatic] = useState(true);
  const [revision, setRevision] = useState(0);
  const [mining, setMining] = useState<MiningState>(initialMining);
  const [error, setError] = useState("");
  const worker = useRef<Worker | null>(null);
  const activeJob = useRef("");
  const touch = useCallback(() => setRevision((n) => n + 1), []);
  const terminate = useCallback(() => {
    activeJob.current = "";
    worker.current?.terminate();
    worker.current = null;
  }, []);
  useEffect(() => terminate, [terminate]);
  useEffect(() => {
    if (!automatic || !lab) return;
    const timer = setInterval(() => {
      if (lab.queue.length) {
        lab.deliverNext();
        touch();
      }
    }, 700);
    return () => clearInterval(timer);
  }, [lab, automatic, touch]);

  function reset() {
    terminate();
    setLab(null);
    setWallets([]);
    setMining(initialMining);
    setError("");
    touch();
  }
  function start(difficulty: number, example = false) {
    terminate();
    const selected = example
      ? [createWallet("Alice"), createWallet("Bob")]
      : wallets;
    const next = new Lab(selected, difficulty);
    setWallets(next.wallets);
    setLab(next);
    setMining(initialMining);
    setError("");
    touch();
  }
  function addWallet() {
    const name =
      wallets.length === 0
        ? "Alice"
        : wallets.length === 1
          ? "Bob"
          : `학생 ${wallets.length + 1}`;
    const wallet = createWallet(name);
    if (lab) {
      lab.wallets.push(wallet);
      setWallets([...lab.wallets]);
    } else setWallets([...wallets, wallet]);
    touch();
  }
  function beginMining(node: NodeId, step: boolean) {
    if (!lab || activeJob.current) return;
    try {
      const candidate = lab.candidate(node);
      terminate();
      const jobId = crypto.randomUUID();
      activeJob.current = jobId;
      const miner = new Worker(
        new URL("../mining/miner.worker.ts", import.meta.url),
        { type: "module" },
      );
      worker.current = miner;
      setMining({
        status: step ? "paused" : "running",
        node,
        attempts: 0,
        elapsedMs: 0,
        block: candidate,
      });
      miner.onmessage = (event: MessageEvent<MiningUpdate>) => {
        const update = event.data;
        if (update.jobId !== activeJob.current) return;
        let status: MiningState["status"] =
          update.type === "progress" ? "running" : update.type;
        if (update.type === "found" && update.block) {
          const validation = lab.acceptMined(node, update.block);
          if (!validation.valid) {
            status = "error";
            setError(validation.message);
          }
          touch();
          terminate();
        }
        if (update.type === "error" || update.type === "stopped") terminate();
        setMining({
          status,
          node,
          attempts: update.attempts,
          elapsedMs: update.elapsedMs,
          block: update.block,
          message: update.message,
        });
      };
      miner.onerror = () => {
        if (activeJob.current !== jobId) return;
        terminate();
        setMining((m) => ({
          ...m,
          status: "error",
          message: "Worker 실행 오류. 새로고침 후 다시 시작하세요.",
        }));
      };
      miner.postMessage({
        type: "start",
        jobId,
        candidate,
        step,
      } satisfies MiningCommand);
      setError("");
    } catch (e) {
      terminate();
      setError(e instanceof Error ? e.message : "채굴 시작 오류");
    }
  }
  function miningCommand(type: "pause" | "resume" | "step" | "stop") {
    if (!worker.current || !activeJob.current) return;
    worker.current.postMessage({
      type,
      jobId: activeJob.current,
    } satisfies MiningCommand);
    if (type === "resume") setMining((m) => ({ ...m, status: "running" }));
    if (type === "stop") {
      terminate();
      setMining((m) => ({ ...m, status: "stopped" }));
    }
  }
  return {
    lab,
    wallets,
    automatic,
    setAutomatic,
    revision,
    mining,
    error,
    setError,
    touch,
    reset,
    start,
    addWallet,
    beginMining,
    miningCommand,
  };
}
