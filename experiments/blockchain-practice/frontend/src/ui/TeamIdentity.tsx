import { useState } from "react";
import { teamRequest, type Credential, type RoomState } from "./useClassroom";
import { useConnectionMessage } from "./ServerConnection";

export function TeamIdentity({ state, credential, disabled }: {
  state: RoomState | null; credential: Credential; disabled: boolean;
}) {
  const me = state?.participants.find((person) => person.id === credential.participantId);
  const online = state?.participants.filter((person) => person.connected) || [];
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const message = useConnectionMessage(error);
  return <div className="team-identity" data-testid="team-identity">
    <div className="button-row">
      <strong>내 별명 · {me?.name || "확인 중"}</strong>
      <span>{me?.team ? `팀 ${me.team} 담당` : "아직 팀을 선택하지 않았습니다"}</span>
      <button className="text-button" disabled={disabled || busy}
        onClick={() => { setName(me?.name || ""); setError(""); setEditing(!editing); }}>별명 변경</button>
    </div>
    {editing && <form className="button-row" onSubmit={async (event) => {
      event.preventDefault();
      if (busy || disabled) return;
      setBusy(true); setError("");
      try {
        await teamRequest(`/rooms/${credential.code}/profile`, { name }, credential.token);
        setEditing(false);
      } catch (reason) { setError(reason instanceof Error ? reason.message : "별명을 저장하지 못했습니다"); }
      finally { setBusy(false); }
    }}>
      <label>새 별명<input required maxLength={30} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <button disabled={disabled || busy}>{busy ? "저장 중…" : "별명 저장"}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(false)}>취소</button>
    </form>}
    {message && <p role="alert">{message}</p>}
    <div className="team-online" aria-label="접속 중인 참가자">
      <span>접속 중 {online.length}명</span>
      {online.map((person) => <span key={person.id} className="team-person">
        {person.name}{person.id === credential.participantId ? " (나)" : ""}{person.team ? ` · 팀 ${person.team}` : ""}
      </span>)}
    </div>
  </div>;
}
