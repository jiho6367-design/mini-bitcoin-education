"""계정 상태·보상·높이별 난이도로 후보를 만들고 실제 SHA-256을 탐색한다."""
import hashlib
import time
from block import Block
from blockchain import BLOCK_REWARD, MAX_TX_PER_BLOCK
from transaction import COINBASE, Transaction


def build_candidate(blockchain, mempool, miner_address):
    if not blockchain.is_valid():
        raise ValueError("유효하지 않은 체인은 먼저 동기화로 복구하세요")
    state = blockchain.state()
    coinbase = Transaction(COINBASE, "", miner_address, BLOCK_REWARD)
    ok, reason = state.apply(coinbase)
    if not ok:
        raise ValueError(reason)
    picked = []
    for tx in mempool.txs:
        if len(picked) >= MAX_TX_PER_BLOCK - 1:
            break
        if state.apply(tx)[0]:
            picked.append(tx.copy())
    tip = blockchain.tip
    return Block(tip.index + 1, tip.hash, [coinbase] + picked,
                 blockchain.difficulty_for_height(tip.index + 1), timestamp=max(time.time(), tip.timestamp))


def mine(blockchain, mempool, miner_address, on_try=None, show_every=0):
    block = build_candidate(blockchain, mempool, miner_address)
    prefix, target, attempts, started = block.header_prefix(), "0" * block.difficulty, 0, time.perf_counter()
    while True:
        block.hash = hashlib.sha256((prefix + str(block.nonce)).encode()).hexdigest()
        attempts += 1
        found = block.hash.startswith(target)
        if on_try and (found or (show_every and attempts % show_every == 1 % show_every)):
            on_try(block.nonce, block.hash)
        if found:
            break
        block.nonce += 1
    ok, reason = blockchain.add_block(block)
    if not ok:
        raise RuntimeError(f"블록 추가 실패: {reason}")
    mempool.refresh()
    return block.copy(), attempts, time.perf_counter() - started
