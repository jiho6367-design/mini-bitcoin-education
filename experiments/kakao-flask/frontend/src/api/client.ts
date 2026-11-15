import type {
  RaceRequest,
  RaceSnapshot,
  AnySnapshot,
  DoubleSpendResult,
  DraftResult,
  ForgedSignatureResult,
  LabSnapshot,
  MiningCommand,
  MiningMode,
  MiningResult,
  NodeId,
  SubmitResult,
  TamperReport,
  TamperRequest,
  Transaction,
  UnsignedTransaction,
  ValidationResult,
  Wallet,
} from "./types";
import { RequestTimeoutError, withRequestTimeout } from "./transport";
import { uiText } from "../copy";

export const UNREACHABLE_MESSAGE =
  "백엔드에 연결할 수 없습니다 Flask 서버 실행 상태를 확인한 뒤 다시 시도하세요";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(uiText(message));
    this.status = status;
    this.code = code;
  }
}

async function request<T>(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  try {
    return await withRequestTimeout((signal) => readResponse<T>(method, path, body, signal), {
      mutation: method !== "GET",
    });
  } catch (error) {
    if (error instanceof RequestTimeoutError)
      throw new ApiError(0, "REQUEST_TIMEOUT", error.message);
    throw error;
  }
}

async function readResponse<T>(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body: unknown,
  signal: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/education${path}`, {
      method,
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch {
    throw new ApiError(0, "BACKEND_UNREACHABLE", UNREACHABLE_MESSAGE);
  }
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (
      data as { error?: { code?: string; message?: string } } | null
    )?.error;
    // 백엔드가 꺼져 있으면 개발 서버의 프록시가 JSON이 아닌 오류를 돌려준다
    if (!error?.code || !error.message)
      throw new ApiError(
        response.status,
        "BACKEND_UNREACHABLE",
        UNREACHABLE_MESSAGE,
      );
    throw new ApiError(response.status, error.code, error.message);
  }
  return data as T;
}

export const api = {
  race: (labId: string, body: RaceRequest) =>
    request<{
      lab: LabSnapshot | null;
      labId?: string;
      revision?: number;
      race?: RaceSnapshot;
    }>("POST", `/labs/${labId}/race`, body),
  health: () => request<{ status: string; version: string }>("GET", "/health"),
  createLab: (
    body: { mode: "example"; difficulty: number } | { mode: "manual" },
  ) => request<{ lab: AnySnapshot }>("POST", "/labs", body),
  deleteLab: (labId: string) => request<void>("DELETE", `/labs/${labId}`),
  addWallet: (labId: string) =>
    request<{ wallet: Wallet; lab: AnySnapshot }>(
      "POST",
      `/labs/${labId}/wallets`,
    ),
  privateKey: (labId: string, address: string) =>
    request<{ privateKey: string }>(
      "GET",
      `/labs/${labId}/wallets/${address}/private-key`,
    ),
  startLab: (labId: string, difficulty: number) =>
    request<{ lab: LabSnapshot }>("POST", `/labs/${labId}/start`, {
      difficulty,
    }),
  draft: (
    labId: string,
    node: NodeId,
    body: {
      senderAddress: string;
      recipientAddress: string;
      amount: number;
      fee?: number;
      selectedInputs?: string[];
    },
  ) =>
    request<DraftResult>("POST", `/labs/${labId}/nodes/${node}/drafts`, body),
  sign: (labId: string, draft: UnsignedTransaction) =>
    request<{ signed: Transaction }>("POST", `/labs/${labId}/wallets/sign`, {
      draft,
    }),
  submit: (labId: string, node: NodeId, transaction: Transaction) =>
    request<SubmitResult>("POST", `/labs/${labId}/nodes/${node}/transactions`, {
      transaction,
    }),
  deliverNext: (labId: string) =>
    request<{ validation: ValidationResult | null; lab: LabSnapshot }>(
      "POST",
      `/labs/${labId}/messages/deliver-next`,
    ),
  startMining: (
    labId: string,
    body: { nodeId: NodeId; mode: MiningMode; step: boolean },
  ) => request<MiningResult>("POST", `/labs/${labId}/mining`, body),
  miningCommand: (labId: string, jobId: string, command: MiningCommand) =>
    request<MiningResult>("POST", `/labs/${labId}/mining/${jobId}/${command}`),
  forgedSignature: (labId: string, node: NodeId, transaction: Transaction) =>
    request<ForgedSignatureResult>(
      "POST",
      `/labs/${labId}/nodes/${node}/experiments/forged-signature`,
      { transaction },
    ),
  doubleSpend: (labId: string, node: NodeId) =>
    request<DoubleSpendResult>(
      "POST",
      `/labs/${labId}/nodes/${node}/experiments/double-spend`,
    ),
  invalidPowBlock: (labId: string, node: NodeId) =>
    request<SubmitResult>(
      "POST",
      `/labs/${labId}/nodes/${node}/experiments/invalid-pow-block`,
    ),
  tamper: (labId: string, node: NodeId, body: TamperRequest) =>
    request<TamperReport>(
      "POST",
      `/labs/${labId}/nodes/${node}/experiments/tamper`,
      body,
    ),
};
