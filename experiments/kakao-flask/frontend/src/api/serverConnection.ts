/** One read-only readiness probe shared by the whole page.
 * Mutations are never retained or replayed by this monitor.
 */
export interface ServerConfig {
  classroomOnly: boolean;
  publicUrl?: string;
  defaultTeamCode?: string | null;
  teamMode?: boolean;
}

export interface ServerConnectionSnapshot {
  phase: "checking" | "preparing" | "offline" | "ready" | "unavailable";
  config: ServerConfig | null;
  message: string;
}

export const PREPARING_MESSAGE = "서버 준비 중 준비되면 자동으로 연결됩니다";
export const OFFLINE_MESSAGE = "인터넷 연결을 기다리는 중 연결되면 자동으로 다시 연결됩니다";
const UNKNOWN_MUTATION = "작업이 처리됐을 수 있으므로 현재 상태를 확인한 뒤 다시 시도하세요";

const online = () => typeof navigator === "undefined" || navigator.onLine !== false;

export function serverUnavailableMessage(mutation = false) {
  const message = online() ? PREPARING_MESSAGE : OFFLINE_MESSAGE;
  return mutation ? `${message} ${UNKNOWN_MUTATION}` : message;
}

function validConfig(data: unknown): data is ServerConfig {
  if (!data || typeof data !== "object" || Array.isArray(data)) return false;
  const value = data as Record<string, unknown>;
  return typeof value.classroomOnly === "boolean" &&
    (value.publicUrl === undefined || typeof value.publicUrl === "string") &&
    (value.defaultTeamCode === undefined || value.defaultTeamCode === null || typeof value.defaultTeamCode === "string") &&
    (value.teamMode === undefined || typeof value.teamMode === "boolean");
}

export function createServerConnection() {
  let snapshot: ServerConnectionSnapshot = {
    phase: "checking", config: null, message: "서버 연결 확인 중",
  };
  const listeners = new Set<() => void>();
  let starts = 0;
  let running = false;
  let generation = 0;
  let delay = 2_000;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let flight: { cancel: () => void } | undefined;

  const emit = (phase: ServerConnectionSnapshot["phase"], message: string, config = snapshot.config) => {
    if (snapshot.phase === phase && snapshot.message === message && snapshot.config === config) return;
    snapshot = { phase, message, config };
    for (const listener of listeners) listener();
  };
  const cancel = () => {
    generation++;
    clearTimeout(timer);
    timer = undefined;
    flight?.cancel();
    flight = undefined;
  };
  const schedule = (wait: number) => {
    if (!running || timer !== undefined || flight) return;
    timer = setTimeout(() => {
      timer = undefined;
      void probe();
    }, wait);
  };
  async function probe() {
    if (!running || flight) return;
    if (!online()) {
      emit("offline", OFFLINE_MESSAGE);
      return;
    }
    const version = generation;
    const controller = new AbortController();
    let rejectCancelled: (reason: Error) => void = () => undefined;
    const cancelled = new Promise<never>((_, reject) => { rejectCancelled = reject; });
    const timeout = setTimeout(() => {
      rejectCancelled(new Error("Readiness timeout"));
      controller.abort();
    }, 12_000);
    const current = {
      cancel: () => {
        clearTimeout(timeout);
        rejectCancelled(new Error("Readiness cancelled"));
        controller.abort();
      },
    };
    flight = current;
    let retry = false;
    try {
      const result = await Promise.race([
        (async () => {
          const response = await fetch("/api/education/classroom/config", {
            method: "GET", cache: "no-store", headers: { Accept: "application/json" },
            signal: controller.signal,
          });
          const data: unknown = await response.json().catch(() => null);
          return { response, data };
        })(),
        cancelled,
      ]);
      if (!running || generation !== version) return;
      if (result.response.ok && validConfig(result.data)) {
        delay = 2_000;
        emit("ready", "연결됨", result.data);
      } else if (result.response.status >= 400 && result.response.status < 500 &&
                 ![408, 429].includes(result.response.status)) {
        emit("unavailable", "실습에 연결할 수 없습니다 접속 주소를 확인하고 다시 연결하세요");
      } else {
        retry = true;
        emit(online() ? "preparing" : "offline", serverUnavailableMessage());
      }
    } catch {
      if (!running || generation !== version) return;
      retry = true;
      emit(online() ? "preparing" : "offline", serverUnavailableMessage());
    } finally {
      clearTimeout(timeout);
      if (flight === current) flight = undefined;
      if (running && generation === version && retry && online()) {
        schedule(delay);
        delay = Math.min(delay * 2, 10_000);
      }
    }
  }
  function retry() {
    if (!running) return;
    if (!online()) {
      cancel();
      emit("offline", OFFLINE_MESSAGE);
      return;
    }
    clearTimeout(timer);
    timer = undefined;
    emit("preparing", PREPARING_MESSAGE);
    void probe();
  }
  const offline = () => {
    cancel();
    emit("offline", OFFLINE_MESSAGE);
  };
  const becameOnline = () => {
    if (snapshot.phase !== "ready" && snapshot.phase !== "unavailable") retry();
  };
  const becameVisible = () => {
    if (typeof document !== "undefined" && document.visibilityState === "visible" &&
        snapshot.phase !== "ready" && snapshot.phase !== "unavailable") retry();
  };
  function activate() {
    if (running) return;
    running = true;
    delay = 2_000;
    if (typeof window !== "undefined") {
      window.addEventListener("online", becameOnline);
      window.addEventListener("offline", offline);
    }
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", becameVisible);
    emit(online() ? "checking" : "offline", online() ? "서버 연결 확인 중" : OFFLINE_MESSAGE);
    void probe();
  }
  function deactivate() {
    if (!running || listeners.size || starts) return;
    running = false;
    cancel();
    if (typeof window !== "undefined") {
      window.removeEventListener("online", becameOnline);
      window.removeEventListener("offline", offline);
    }
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", becameVisible);
  }
  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      activate();
      return () => { listeners.delete(listener); deactivate(); };
    },
    start() { starts++; activate(); },
    stop() { starts = Math.max(0, starts - 1); deactivate(); },
    retry,
    reportUnavailable() {
      if (!online()) {
        cancel();
        emit("offline", OFFLINE_MESSAGE);
        return;
      }
      emit("preparing", PREPARING_MESSAGE);
      if (running && !flight && timer === undefined) schedule(0);
    },
  };
}

export const serverConnection = createServerConnection();
