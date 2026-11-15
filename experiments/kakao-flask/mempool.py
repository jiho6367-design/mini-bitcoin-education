"""[3단계] Mempool: 검증을 통과한 거래의 대기열."""
from transaction import COINBASE


class Mempool:
    def __init__(self, blockchain):
        self.blockchain = blockchain
        self.txs = []

    def pending_state(self):
        """확정된 체인 + 대기 중인 거래를 모두 반영한 상태 (이중 지불·잔액 초과 방지)."""
        state = self.blockchain.state()
        for tx in self.txs:
            state.apply(tx)
        return state

    def add(self, tx):
        """검증(형식·서명·nonce·잔액) 통과 시 등록. 반환: (성공 여부, 사유)"""
        if tx.tx_type == COINBASE:
            return False, "coinbase 거래는 직접 등록할 수 없습니다"
        if any(t.txid() == tx.txid() for t in self.txs):
            return False, "이미 Mempool에 있는 거래"
        ok, reason = self.pending_state().apply(tx)
        if ok:
            self.txs.append(tx)
        return ok, reason

    def refresh(self):
        """블록이 체인에 들어간 뒤, 처리됐거나 무효가 된 거래를 제거."""
        state, keep = self.blockchain.state(), []
        for tx in self.txs:
            if state.apply(tx)[0]:
                keep.append(tx)
        self.txs = keep