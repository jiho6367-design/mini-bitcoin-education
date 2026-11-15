import { useEffect, useState } from "react";
import { App } from "./App";
import { ClassroomApp } from "./ClassroomApp";
import { roomRequest } from "./useClassroom";

export function Root() {
  const [config, setConfig] = useState<{
    classroomOnly: boolean;
    publicUrl?: string;
    defaultTeamCode?: string;
  } | null>(null);
  const [error, setError] = useState(false);
  const [mode, setMode] = useState(
    new URLSearchParams(location.search).get("classroom") || "",
  );
  useEffect(() => {
    let active = true;
    roomRequest<{ classroomOnly: boolean; publicUrl?: string }>("/config")
      .then((c) => {
        if (active) setConfig(c);
      })
      .catch(() => {
        if (active) {
          setConfig({ classroomOnly: false });
          setError(true);
        }
      });
    return () => {
      active = false;
    };
  }, []);
  if (!config) return <p role="status">실습 환경 확인 중…</p>;
  if (mode || config.classroomOnly)
    return (
      <ClassroomApp
        mode={mode || "team"}
        publicUrl={config.publicUrl || ""}
        defaultTeamCode={config.defaultTeamCode}
        personal={!config.classroomOnly}
        back={() => {
          setMode("");
          history.replaceState(null, "", location.pathname);
        }}
      />
    );
  return (
    <App
      entry={
        <div className="classroom-entry" aria-label="실습 선택">
          <strong>실습 모드</strong>
          <button onClick={() => setMode("join")}>공동 실습 참여</button>
          <button className="secondary" onClick={() => setMode("team")}>
            팀 실습 시작
          </button>
          {error && <span>Flask 서버 연결 확인 필요</span>}
        </div>
      }
    />
  );
}
