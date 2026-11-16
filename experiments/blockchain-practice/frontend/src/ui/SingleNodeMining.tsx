import type { NodeId, RaceSnapshot } from "../api/types";
import { NODE_IDS } from "../api/types";
import { useState } from "react";
import { Panel } from "./shared";
import "./singleNodeMining.css";

export function SingleNodeMining({ race, selected, select, disabled, command }: {
  race: RaceSnapshot | null | undefined;
  selected: NodeId;
  select: (node: NodeId) => void;
  disabled: boolean;
  command: (action: string, payload?: object) => Promise<unknown>;
}) {
  const [mode, setMode] = useState(race?.mode || "learning");
  const ready = race?.singleNode === selected;
  const act = (action: string) => void command(action, { raceId: race?.id });
  return <Panel title="선택한 노드로 채굴">
    <p>선택한 노드만 실제 해시를 계산합니다 다른 노드는 블록을 전달받아 독립적으로 검증합니다</p>
    <fieldset disabled={disabled}>
      <div className="button-row">
        <label>채굴할 노드
          <select value={selected} onChange={(e) => select(e.target.value as NodeId)}>
            {NODE_IDS.map((node) => <option key={node} value={node}>노드 {node}</option>)}
          </select>
        </label>
        <label>계산 속도
          <select value={mode} onChange={(e) => setMode(e.target.value as "learning" | "fast")}>
            <option value="learning">천천히 · 실제 한 번씩</option>
            <option value="fast">빠르게 · 묶음 계산</option>
          </select>
        </label>
        <button onClick={() => void command("prepare", { raceId: race?.id, singleNode: selected, guided: true, mode })}>선택 노드 후보 준비</button>
        <button disabled={disabled || !ready || race?.status === "running" || race?.status === "stopped"}
          onClick={() => act("resume")}>채굴 시작 / 계속</button>
        <button disabled={disabled || !ready || race?.status !== "paused"}
          onClick={() => act("step")}>한 번 계산</button>
        <button disabled={disabled || !ready || race?.status !== "running"}
          onClick={() => act("pause")}>채굴 일시정지</button>
        <button disabled={disabled || !ready || race?.status === "stopped"}
          onClick={() => act("stop")}>채굴 중지</button>
      </div>
    </fieldset>
    <p>노드·속도 선택 → 후보 준비 → 시작 순서입니다 첫 블록을 찾으면 멈춥니다 새 후보 준비는 기존 체인과 잔액을 유지합니다</p>
    {!ready && race && <p>선택을 적용하려면 후보를 새로 준비하세요 진행 중인 계산은 새 준비 시 멈춥니다</p>}
  </Panel>;
}
