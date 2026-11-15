import { RequestTimeoutError, withRequestTimeout } from "../api/transport";
import { uiText } from "../copy";

/** A timed-out mutation may already have succeeded. Keep its ID until confirmed. */
export class ClassroomRequestError extends Error {
  constructor(
    message: string,
    readonly uncertain = false,
    readonly code = "",
    readonly status = 0,
  ) {
    super(uiText(message));
    this.name = "ClassroomRequestError";
  }
}

export async function roomRequest<T>(
  path: string,
  body?: unknown,
  token?: string,
  timeoutMs = 15_000,
): Promise<T> {
  return educationRequest<T>("classroom", path, body, token, timeoutMs);
}

export async function teamRequest<T>(
  path: string,
  body?: unknown,
  token?: string,
  timeoutMs = 15_000,
): Promise<T> {
  return educationRequest<T>("team", path, body, token, timeoutMs);
}

async function educationRequest<T>(
  namespace: string,
  path: string,
  body: unknown,
  token: string | undefined,
  timeoutMs: number,
): Promise<T> {
  try {
    return await withRequestTimeout(
      async (signal) => {
        let response: Response;
        try {
          response = await fetch(`/api/education/${namespace}${path}`, {
            method: body === undefined ? "GET" : "POST",
            headers: {
              "Content-Type": "application/json",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: body === undefined ? undefined : JSON.stringify(body),
            signal,
          });
        } catch {
          throw new ClassroomRequestError(
            "서버에 연결할 수 없습니다 인터넷 연결을 확인하세요",
            body !== undefined,
          );
        }
        const data = await response.json().catch(() => null);
        if (!response.ok)
          throw new ClassroomRequestError(
            data?.error?.message || "공동 실습 요청에 실패했습니다",
            body !== undefined && response.status >= 500 && !data?.error?.code,
            data?.error?.code || "",
            response.status,
          );
        if (data === null)
          throw new ClassroomRequestError(
            "서버 응답을 읽지 못했습니다",
            body !== undefined,
          );
        return data as T;
      },
      { mutation: body !== undefined, timeoutMs },
    );
  } catch (error) {
    if (error instanceof RequestTimeoutError)
      throw new ClassroomRequestError(error.message, body !== undefined);
    throw error;
  }
}

export interface CommandContext {
  code: string;
  runId: string;
  token: string;
  assignmentId?: string;
}
export interface PendingClassroomCommand {
  action: string;
  commandId: string;
}
export interface CommandFeedback {
  busy: boolean;
  pending: PendingClassroomCommand | null;
  error: string;
}

/** One outstanding command; explicit retries always use the original body and ID. */
export function createClassroomCommands<T>(
  feedback: (state: CommandFeedback) => void,
  request: typeof roomRequest = roomRequest,
) {
  type Job = {
    context: CommandContext;
    body: {
      runId: string;
      assignmentId?: string;
      commandId: string;
      action: string;
      payload: object;
    };
    resolve: (result: T | undefined) => void;
  };
  let current: Job | null = null;
  let sending = false;
  const clear = (error = "") => {
    const old = current;
    current = null;
    sending = false;
    old?.resolve(undefined);
    feedback({ busy: false, pending: null, error });
  };
  async function send(job: Job) {
    if (sending || current !== job) return;
    sending = true;
    feedback({ busy: true, pending: null, error: "" });
    try {
      const result = await request<T>(
        `/rooms/${job.context.code}/commands`,
        job.body,
        job.context.token,
      );
      if (current !== job) return;
      current = null;
      sending = false;
      feedback({ busy: false, pending: null, error: "" });
      job.resolve(result);
    } catch (error) {
      if (current !== job) return;
      sending = false;
      if (error instanceof ClassroomRequestError && error.uncertain) {
        feedback({
          busy: false,
          pending: { action: job.body.action, commandId: job.body.commandId },
          error: `${error.message} 처리 결과가 아직 확인되지 않았습니다 같은 요청 확인을 사용하세요`,
        });
      } else clear(error instanceof Error ? error.message : "요청에 실패했습니다");
    }
  }
  return {
    execute(context: CommandContext, action: string, payload: object = {}) {
      if (current) return Promise.resolve(undefined);
      return new Promise<T | undefined>((resolve) => {
        const job: Job = {
          context: { ...context },
          body: {
            runId: context.runId,
            ...(context.assignmentId ? { assignmentId: context.assignmentId } : {}),
            commandId: crypto.randomUUID(),
            action,
            payload: structuredClone(payload),
          },
          resolve,
        };
        current = job;
        void send(job);
      });
    },
    retry() {
      if (current) void send(current);
    },
    isLeaving() {
      return !!current && ["leave", "close"].includes(current.body.action);
    },
    confirmEnded(result: T) {
      // The authoritative stream can report leave before its HTTP response arrives.
      const job = current;
      if (job && ["leave", "close"].includes(job.body.action)) {
        current = null;
        sending = false;
        feedback({ busy: false, pending: null, error: "" });
        job.resolve(result);
      } else clear();
    },
    cancel: clear,
  };
}
