import { useSyncExternalStore } from "react";
import { OFFLINE_MESSAGE, PREPARING_MESSAGE, serverConnection } from "../api/serverConnection";

type ConnectionState = ReturnType<typeof serverConnection.getSnapshot>;

/** Keep an uncertain operation visible after transport recovery without stale waiting copy. */
export function connectionMessage(message: string, phase: ConnectionState["phase"]) {
  if (phase !== "ready") return message;
  for (const [waiting, recovered] of [
    [PREPARING_MESSAGE, "서버 연결이 복구되었습니다"],
    [OFFLINE_MESSAGE, "인터넷 연결이 복구되었습니다"],
  ]) {
    // Structured 503 errors retain their original rollback message before this
    // shared suffix. Replace only the complete connection notice, wherever used.
    if (message.includes(waiting)) return message.replace(waiting, recovered);
  }
  return message;
}

const connectionPhase = () => serverConnection.getSnapshot().phase;

export function useConnectionMessage(message: string) {
  const phase = useSyncExternalStore(
    serverConnection.subscribe,
    connectionPhase,
    connectionPhase,
  );
  return connectionMessage(message, phase);
}

export function ServerConnection({
  connection,
  initial,
}: {
  connection: ConnectionState;
  initial: boolean;
}) {
  if (connection.phase === "ready") return null;
  const title = connection.phase === "offline"
    ? "인터넷 연결 확인"
    : connection.phase === "unavailable"
      ? "실습 서버 연결 확인"
      : "서버 준비 중";
  return (
    <section
      className={`server-connection ${initial ? "server-connection-initial" : "server-connection-banner"}`}
      data-testid="server-connection"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div className="server-connection-card">
        <strong>{title}</strong>
        <p>{["checking", "preparing"].includes(connection.phase)
          ? "준비되면 자동으로 연결됩니다" : connection.message}</p>
        {initial && ["checking", "preparing"].includes(connection.phase) && (
          <small>프로그램을 따로 실행할 필요가 없습니다</small>
        )}
        {!initial && <small>현재 화면과 입력은 유지됩니다</small>}
        {connection.phase === "unavailable" && (
          <button type="button" onClick={serverConnection.retry}>다시 연결</button>
        )}
      </div>
    </section>
  );
}
