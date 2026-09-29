import type { Block } from "../core/types";
export type MiningCommand =
  | { type: "start"; jobId: string; candidate: Block; step: boolean }
  | { type: "pause" | "resume" | "step" | "stop"; jobId: string };
export interface MiningUpdate {
  type: "progress" | "paused" | "found" | "stopped" | "error";
  jobId: string;
  attempts: number;
  elapsedMs: number;
  block?: Block;
  message?: string;
}
