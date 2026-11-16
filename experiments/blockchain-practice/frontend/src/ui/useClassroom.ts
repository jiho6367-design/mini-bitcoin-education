import { useEffect, useRef, useState } from "react";
import { uiText } from "../copy";
import { serverConnection } from "../api/serverConnection";
import type {
  LabSnapshot,
  NodeId,
  DraftResult,
  Transaction,
} from "../api/types";
import {
  createClassroomCommands,
  ClassroomRequestError,
  teamRequest,
  type CommandFeedback,
} from "./classroomCommands";
export { roomRequest, teamRequest } from "./classroomCommands";

export interface Credential {
  code: string;
  runId: string;
  participantId: string;
  token: string;
  teamMode?: boolean;
}
export interface RoomState {
  teamMode?: boolean;
  storage?: { id: string; savedAt: string | null; error: string };
  runId: string;
  code: string;
  seq: number;
  closed: boolean;
  limited?: boolean;
  automatic: boolean;
  participants: {
    id: string;
    name: string;
    role: string;
    team: NodeId | null;
    connected: boolean;
    assignmentId?: string;
  }[];
  activity: { seq: number; actor: string; message: string }[];
  lab: LabSnapshot;
}
export interface CommandResult {
  ok?: boolean;
  draft?: DraftResult["draft"];
  inputs?: DraftResult["inputs"];
  signingHash?: string;
  signingText?: string;
  signed?: Transaction;
  validation?: { valid: boolean; message: string };
}
export const ROOM_KEY = "team-blockchain-classroom";
export function savedCredential(): Credential | null {
  try {
    const c = JSON.parse(sessionStorage.getItem(ROOM_KEY) || "null");
    const linkedCode = new URLSearchParams(location.search).get("room");
    return c &&
      (!linkedCode || c.code === linkedCode.toUpperCase()) &&
      typeof c.token === "string" &&
      typeof c.code === "string" &&
      typeof c.runId === "string" &&
      typeof c.participantId === "string"
      ? c
      : null;
  } catch {
    return null;
  }
}

/** Renew identity through the owner cookie, never reuse commands from an old run. */
export async function recoverTeamCredential(
  credential: Credential,
  request: typeof teamRequest = teamRequest,
): Promise<Credential | "join" | "ended"> {
  try {
    const next = await request<Credential>(`/rooms/${credential.code}/reconnect`, {});
    if (
      next.code !== credential.code || !next.teamMode ||
      !next.runId || !next.participantId || !next.token
    ) throw new ClassroomRequestError("접속 정보를 다시 확인하고 있습니다");
    return next;
  } catch (error) {
    if (error instanceof ClassroomRequestError) {
      if (error.code === "MEMBERSHIP_REQUIRED") return "join";
      if (error.code === "TEAM_ROOM_NOT_FOUND" ||
          (error.code === "ROOM_GONE" && error.status === 410)) return "ended";
    }
    throw error;
  }
}
export function mergePatch(previous: unknown, patch: unknown): unknown {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return patch;
  if ("$replace" in patch) return (patch as Record<string, unknown>).$replace;
  const result = {
    ...(previous && typeof previous === "object" ? previous : {}),
  } as Record<string, unknown>;
  for (const [key, value] of Object.entries(patch))
    result[key] = mergePatch(result[key], value);
  return result;
}
export function useClassroom(
  credential: Credential | null,
  onCredential: (next: Credential | null, message?: string) => void,
) {
  const [state, setState] = useState<RoomState | null>(null);
  const [connection, setConnection] = useState("연결 대기");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState<CommandFeedback>({
    busy: false,
    pending: null,
    error: "",
  });
  const [commands] = useState(() =>
    createClassroomCommands<CommandResult>(setFeedback),
  );
  const authority = state?.participants.find(
    (p) => p.id === credential?.participantId,
  );
  const authorityKey = credential && authority
    ? `${credential.runId}:${credential.participantId}:${authority?.assignmentId ?? `${authority?.role}:${authority?.team}`}`
    : "";
  const previousAuthority = useRef(authorityKey);
  useEffect(() => {
    if (previousAuthority.current !== authorityKey) {
      commands.cancel(
        previousAuthority.current && authority
          ? "역할이 변경되었습니다 새 역할에서 거래를 다시 준비하세요"
          : "",
      );
      previousAuthority.current = authorityKey;
    }
  }, [authorityKey, authority, commands]);
  useEffect(() => {
    setState(null);
    setError("");
    if (!credential) {
      setConnection("연결 대기");
      return;
    }
    let cancelled = false,
      ended = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let retry = 500;
    let current: RoomState | null = null;
    const recover = async (message: string) => {
      ended = true;
      commands.cancel();
      setState(null);
      setConnection("기록 복구 중 · 조작 대기");
      socket?.close();
      try {
        const next = await recoverTeamCredential(credential);
        if (cancelled) return;
        if (next === "join") {
          onCredential(null, "이름을 입력해 같은 방에 입장하세요 저장한 기록은 이어집니다");
        } else if (next === "ended") {
          setConnection("수업 종료");
          setError(uiText(message));
        } else {
          onCredential(next);
        }
      } catch {
        if (cancelled) return;
        setError("서버가 준비되면 같은 방의 기록을 자동으로 이어갑니다");
        timer = setTimeout(() => void recover(message), retry);
        retry = Math.min(retry * 2, 5000);
      }
    };
    const connect = () => {
      if (cancelled || ended) return;
      setConnection("연결 중");
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/education/classroom/rooms/${credential.code}/stream`,
      );
      const ws = socket;
      ws.onopen = () => {
        if (!cancelled) ws.send(JSON.stringify({ token: credential.token }));
      };
      ws.onmessage = (event) => {
        if (cancelled || ended) return;
        const message = JSON.parse(event.data);
        if (message.type === "ping") {
          ws.send("pong");
          return;
        }
        if (message.type === "ended") {
          if (!commands.isLeaving() && (credential.teamMode ||
              ["ROOM_GONE", "UNAUTHORIZED"].includes(message.code))) {
            void recover(message.message || "이 방의 실습이 종료되었습니다");
            return;
          }
          ended = true;
          setConnection("수업 종료");
          setError(uiText(message.message));
          commands.confirmEnded({ ok: true });
          ws.close();
          return;
        }
        if (message.type === "snapshot") {
          if (message.state.runId !== credential.runId) {
            void recover("서버가 재시작되었습니다 입장 정보를 확인하세요");
            return;
          }
          current = message.state;
        } else if (message.type === "patch") {
          if (
            !current ||
            message.runId !== current.runId ||
            message.baseSeq !== current.seq
          ) {
            ws.close();
            return;
          }
          if (message.seq <= current.seq) return;
          current = mergePatch(current, message.patch) as RoomState;
        }
        if (current) {
          setState(current);
          setConnection("연결됨");
          retry = 500;
        }
      };
      ws.onclose = () => {
        if (cancelled || ended) return;
        setConnection("재연결 중 · 조작 대기");
        serverConnection.reportUnavailable();
        timer = setTimeout(connect, retry);
        retry = Math.min(retry * 2, 5000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      cancelled = true;
      commands.cancel();
      clearTimeout(timer);
      if (socket?.readyState === WebSocket.OPEN) socket.close();
      else if (socket) socket.onopen = () => socket?.close();
    };
  }, [credential, commands, onCredential]);
  const command = async (
    action: string,
    payload: object = {},
  ): Promise<CommandResult | undefined> => {
    if (!credential || connection !== "연결됨") return;
    setError("");
    return commands.execute(
      { ...credential, assignmentId: authority?.assignmentId },
      action,
      payload,
    );
  };
  return {
    state,
    connection,
    error: feedback.error || error,
    busy: feedback.busy,
    pending: feedback.pending,
    retryPending: () => {
      if (connection === "연결됨") commands.retry();
    },
    command,
  };
}
