import { validateSettings } from "./core";

export const HISTORY_KEY = "team-hashcash-history-v1";
export const NAME_KEY = "team-hashcash-name-v1";
export const HISTORY_LIMIT = 50;

export type HistoryRecord = {
  id: string;
  participantName: string;
  message: string;
  difficulty: number;
  startNonce: string;
  nonce: string;
  hash: string;
  checked: string;
  elapsedMs: number;
  createdAt: string;
};

function isRecord(value: unknown): value is HistoryRecord {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<HistoryRecord>;
  if (typeof record.id !== "string" || !record.id || record.id.length > 100 ||
      typeof record.participantName !== "string" || record.participantName.length > 60 ||
      typeof record.message !== "string" || typeof record.difficulty !== "number" ||
      typeof record.startNonce !== "string" || typeof record.nonce !== "string" ||
      typeof record.hash !== "string" || !/^[0-9a-f]{64}$/.test(record.hash) ||
      typeof record.checked !== "string" || !/^[1-9][0-9]{0,19}$/.test(record.checked) ||
      typeof record.elapsedMs !== "number" || !Number.isFinite(record.elapsedMs) || record.elapsedMs < 0 ||
      typeof record.createdAt !== "string" || record.createdAt.length > 40 || !Number.isFinite(Date.parse(record.createdAt))) return false;
  try {
    const settings = validateSettings({ message: record.message, difficulty: record.difficulty, startNonce: record.startNonce });
    const result = validateSettings({ ...settings, startNonce: record.nonce });
    return result.startNonce === record.nonce && settings.startNonce === record.startNonce &&
      BigInt(record.nonce) >= BigInt(record.startNonce) && record.hash.startsWith("0".repeat(record.difficulty));
  } catch { return false; }
}

export function parseHistory(raw: string | null): HistoryRecord[] {
  if (!raw || raw.length > 1_000_000) return [];
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    const seen = new Set<string>();
    return value.slice(0, HISTORY_LIMIT * 2).filter(isRecord).filter((record) => {
      if (seen.has(record.id)) return false;
      seen.add(record.id);
      return true;
    }).slice(0, HISTORY_LIMIT);
  } catch { return []; }
}

export function addHistory(previous: HistoryRecord[], record: HistoryRecord): HistoryRecord[] {
  return [record, ...previous.filter((item) => item.id !== record.id)].slice(0, HISTORY_LIMIT);
}
