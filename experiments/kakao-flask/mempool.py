"""노드별 미확정 거래. 입력 객체와 사본을 분리하고 재구성 후 순번대로 재검증한다."""
from transaction import COINBASE, Transaction


class Mempool:
    def __init__(self, blockchain):
        self.blockchain, self.txs = blockchain, []

    def pending_state(self):
        state = self.blockchain.state()
        for tx in self.txs:
            state.apply(tx)
        return state

    def contains(self, tx):
        return any(t.txid() == tx.txid() for t in self.txs)

    def add(self, tx):
        if not isinstance(tx, Transaction):
            return False, "거래 형식 오류"
        ok, reason = tx.basic_check()
        if not ok:
            return False, reason
        if tx.tx_type == COINBASE:
            return False, "coinbase 거래는 직접 등록할 수 없습니다"
        if not self.blockchain.is_valid():
            return False, "체인이 유효하지 않습니다. 먼저 동기화로 복구하세요"
        if self.contains(tx):
            return False, "이미 Mempool에 있는 거래"
        candidate = tx.copy()
        ok, reason = self.pending_state().apply(candidate)
        if ok:
            self.txs.append(candidate)
        return ok, reason

    def rebuild(self, transactions):
        """확정 거래를 제외하고, 부모 지급·이전 순번을 먼저 반영해 대기열을 재구성한다."""
        confirmed = {tx.txid() for block in self.blockchain.chain for tx in block.transactions}
        pending, seen = [], set()
        for tx in transactions:
            if not isinstance(tx, Transaction) or tx.tx_type == COINBASE or not tx.basic_check()[0]:
                continue
            txid = tx.txid()
            if txid in confirmed or txid in seen:
                continue
            seen.add(txid)
            pending.append(tx.copy())
        state, kept = self.blockchain.state(), []
        while pending:
            deferred, progress = [], False
            for tx in pending:
                if state.apply(tx)[0]:
                    kept.append(tx)
                    progress = True
                else:
                    deferred.append(tx)
            if not progress:
                break
            pending = deferred
        self.txs = kept

    def refresh(self):
        self.rebuild(self.txs)
