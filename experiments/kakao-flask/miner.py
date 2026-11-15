"""[4단계] Mining: Mempool 거래로 블록을 만들고 nonce를 바꿔 가며 PoW를 수행."""
import hashlib
import time

from block import Block
from blockchain import BLOCK_REWARD, MAX_TX_PER_BLOCK
from transaction import COINBASE, Transaction


def build_candidate(blockchain, mempool, miner_address):
    state = blockchain.state()
    coinbase = Transaction(COINBASE, "", miner_address, BLOCK_REWARD)   # 채굴 보상
    state.apply(coinbase)
    picked = []
    for tx in mempool.txs:
        if len(picked) >= MAX_TX_PER_BLOCK - 1:
            break
        if state.apply(tx)[0]:
            picked.append(tx)
    tip = blockchain.tip
    return Block(tip.index + 1, tip.hash, [coinbase] + picked, blockchain.difficulty)


def mine(blockchain, mempool, miner_address, on_try=None, show_every=0):
    """채굴해서 체인에 추가. on_try(nonce, hash)로 해시 변화를 관찰할 수 있다.
    반환: (블록, 시도 횟수, 걸린 시간)"""
    block = build_candidate(blockchain, mempool, miner_address)
    prefix, target = block.header_prefix(), "0" * block.difficulty
    attempts, t0 = 0, time.time()
    while True:
        h = hashlib.sha256((prefix + str(block.nonce)).encode()).hexdigest()
        attempts += 1
        found = h.startswith(target)
        if on_try and (found or (show_every and attempts % show_every == 1 % show_every)):
            on_try(block.nonce, h)
        if found:
            block.hash = h
            break
        block.nonce += 1
    ok, reason = blockchain.add_block(block)
    if not ok:
        raise RuntimeError(f"블록 추가 실패: {reason}")
    mempool.refresh()
    return block, attempts, time.time() - t0