import type { Block } from "../core/types";
export type MiningMode = "fast" | "learning";
export interface MiningSample {
  nonce: number;
  hash: string;
  passed: boolean;
}
export const MINING_SAMPLE_LIMIT = 12;
export const LEARNING_INTERVAL_MS = 250;
export type MiningCommand =
  | {
      type: "start";
      jobId: string;
      candidate: Block;
      step: boolean;
      mode?: MiningMode;
    }
  | { type: "pause" | "resume" | "step" | "stop"; jobId: string };
export interface MiningUpdate {
  type: "progress" | "paused" | "found" | "stopped" | "error";
  jobId: string;
  attempts: number;
  mode: MiningMode;
  elapsedMs: number;
  observationMs: number;
  samples: MiningSample[];
  block?: Block;
  message?: string;
}
