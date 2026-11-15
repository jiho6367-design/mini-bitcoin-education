import { useState, useSyncExternalStore } from "react";
import { App } from "./App";
import { ClassroomApp } from "./ClassroomApp";
import { serverConnection } from "../api/serverConnection";
import { ServerConnection } from "./ServerConnection";

export function Root() {
  const connection = useSyncExternalStore(
    serverConnection.subscribe,
    serverConnection.getSnapshot,
    serverConnection.getSnapshot,
  );
  const config = connection.config;
  const [mode, setMode] = useState(
    new URLSearchParams(location.search).get("classroom") || "",
  );
  return (
    <>
      <ServerConnection connection={connection} initial={!config} />
      {config && (
        <fieldset
          className="practice-controls"
          data-testid="practice-controls"
          disabled={connection.phase !== "ready"}
          aria-busy={connection.phase !== "ready"}
        >
          {mode || config.classroomOnly ? (
            <ClassroomApp
              mode={mode || "team"}
              publicUrl={config.publicUrl || ""}
              defaultTeamCode={config.defaultTeamCode || undefined}
              personal={!config.classroomOnly}
              back={() => {
                setMode("");
                history.replaceState(null, "", location.pathname);
              }}
            />
          ) : (
            <App
              entry={
                <div className="classroom-entry" aria-label="실습 선택">
                  <strong>실습 모드</strong>
                  <button onClick={() => setMode("join")}>공동 실습 참여</button>
                  <button className="secondary" onClick={() => setMode("team")}>
                    팀 실습 시작
                  </button>
                </div>
              }
            />
          )}
        </fieldset>
      )}
    </>
  );
}
