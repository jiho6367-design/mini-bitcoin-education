import { NODE_IDS, type LabSnapshot, type NodeId } from "../api/types";
import { Notice } from "./shared";
import { walletAvailability, type WalletAvailability } from "./walletAvailability";
import "./teamWalletBalances.css";

function Amount({ team, kind, value }: { team: NodeId; kind: string; value: number | null }) {
  return <><strong data-testid={`wallet-${kind}-${team}`}>{value === null ? "확인 중" : value}</strong>{value !== null && <span> 코인</span>}</>;
}

function availabilityReason(balance: WalletAvailability) {
  if (!balance.known) return "잔액을 확인 중입니다";
  if (balance.confirmed === 0) return "현재 확정된 코인이 없습니다";
  if (balance.spendable === 0 && balance.reserved! > 0)
    return "대기 거래가 코인 입력을 예약해 새 송금에 사용할 수 없습니다";
  if (balance.reserved! > 0)
    return "예약된 코인을 제외한 사용 가능액으로 새 송금을 만들 수 있습니다";
  if (balance.immature! > 0)
    return "채굴 보상 중 성숙 대기 금액은 아직 새 송금에 사용할 수 없습니다";
  return balance.confirmed === balance.spendable
    ? "현재 확정 잔액을 새 송금에 사용할 수 있습니다"
    : "사용 가능액은 현재 노드의 검증 상태를 확인하세요";
}

/** Shared classroom wallets start with 100 each; current amounts come from each team's node. */
export function TeamWalletBalances({ lab }: { lab: LabSnapshot }) {
  return (
    <section className="team-wallet-balances" data-testid="team-wallet-balances" aria-label="팀 지갑과 시작 코인">
      <div className="team-wallet-allocation">
        <Notice>
          <strong>처음에는 팀마다 100코인 고정 배정</strong>
          <p>A·B·C 팀 지갑에 각각 100코인씩 총 300코인을 처음 한 번 배정합니다 초기 배정은 채굴 보상이 아닙니다</p>
          <p>팀 지갑 기준입니다 참가자가 늘거나 재접속해도 추가 지급되지 않으며 저장된 잔액을 이어서 사용합니다</p>
          <details className="team-wallet-zero-help">
            <summary>잔액이 있는데 사용 가능이 0인 이유</summary>
            <p>검증·전파한 대기 거래는 사용할 코인 묶음(UTXO) 전체를 예약합니다 작성·서명만 한 상태에서는 예약되지 않습니다</p>
            <p>예를 들어 초기 100코인이 한 묶음일 때 송금액 10·수수료 1인 거래를 제출하면 입력 100코인이 예약돼 사용 가능액은 0이 됩니다</p>
            <p>거래가 블록에 포함되고 자기 노드에서 검증되면 이 예시의 거스름돈 89코인을 사용할 수 있습니다 전액을 사용한 거래는 거스름돈이 없을 수 있습니다</p>
            <p>노드마다 거래·블록을 받는 시점이 다르므로 메시지 전달과 채굴 결과를 함께 확인하세요</p>
          </details>
        </Notice>
      </div>
      <div className="classroom-grid">
        {NODE_IDS.map((team, index) => {
          const address = lab.wallets[index]?.address;
          const balance = walletAvailability(lab.nodes[team], address || "");
          return (
            <section className="panel team-wallet-balance-card" data-team={team} key={team} aria-labelledby={`team-wallet-${team}-title`}>
              <h2 id={`team-wallet-${team}-title`}>팀 {team} 지갑</h2>
              <dl className="team-wallet-amounts" aria-live="polite" aria-atomic="true">
                <div><dt>확정 잔액</dt><dd><Amount team={team} kind="confirmed" value={balance.confirmed} /></dd></div>
                <div><dt>사용 가능</dt><dd><Amount team={team} kind="spendable" value={balance.spendable} /></dd></div>
                <div><dt>대기 거래 예약</dt><dd><Amount team={team} kind="reserved" value={balance.reserved} /></dd></div>
                {balance.immature! > 0 && <div><dt>보상 성숙 대기</dt><dd><Amount team={team} kind="immature" value={balance.immature} /></dd></div>}
              </dl>
              <p className={balance.reserved! > 0 ? "team-wallet-reservation" : "team-wallet-availability"}>{availabilityReason(balance)}</p>
              {balance.reserved! > 0 && <p className="team-wallet-next">새 송금 전에 기존 대기 거래의 채굴·전파 결과를 확인하세요</p>}
              <small>자기 노드 {team} 기준 · 초기 배정 100코인</small>
            </section>
          );
        })}
      </div>
    </section>
  );
}
