"""웹 서버: python app.py  ->  http://127.0.0.1:5000  (로컬 전용)"""
import os
import time

from flask import Flask, jsonify, request, send_from_directory

from blockchain import BLOCK_REWARD, FAUCET_AMOUNT, MAX_DIFFICULTY, MIN_DIFFICULTY
from node import Network
from transaction import FAUCET, TRANSFER, Transaction

app = Flask(__name__, static_folder="static")
NET = Network()


def node_or_404(node_id):
    node = NET.nodes.get(node_id)
    if node is None:
        raise KeyError(f"없는 노드: {node_id}")
    return node


@app.errorhandler(KeyError)
@app.errorhandler(ValueError)
def bad_request(e):
    return jsonify({"ok": False, "reason": str(e.args[0]) if e.args else str(e)}), 400


def wallet_info(w, node):
    st = node.blockchain.state()
    inc = sum(t.amount for t in node.mempool if t.receiver_address == w.address)
    out = sum(t.amount for t in node.mempool if t.sender_address == w.address)
    return {"name": w.name, "address": w.address, "public_key": w.public_key, "private_key": w.private_key,
            "balance": st.balance(w.address), "pending_in": inc, "pending_out": out,
            "is_node_wallet": any(n.wallet.address == w.address for n in NET.nodes.values())}


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/architecture")
def architecture():
    return send_from_directory("static", "architecture.html")


@app.get("/api/state")
def state():
    return jsonify({"difficulty": NET.difficulty, "min_difficulty": MIN_DIFFICULTY, "max_difficulty": MAX_DIFFICULTY,
                    "block_reward": BLOCK_REWARD, "faucet_amount": FAUCET_AMOUNT,
                    "nodes": [n.snapshot() for n in NET.nodes.values()]})


# ---- 1. Wallet -----------------------------------------------------------
@app.get("/api/wallets")
def wallets():
    node = node_or_404(request.args.get("node", "A"))
    return jsonify([wallet_info(w, node) for w in NET.wallets.values()])


@app.post("/api/wallets")
def create_wallet():
    body = request.get_json(force=True)
    w = NET.create_wallet((body.get("name") or "").strip())
    ok, why = node_or_404(body.get("node", "A")).receive_transaction(Transaction(FAUCET, "", w.address, FAUCET_AMOUNT))
    return jsonify({"ok": True, "address": w.address, "faucet": {"ok": ok, "reason": why}})


@app.post("/api/faucet")
def faucet():
    body = request.get_json(force=True)
    if body["address"] not in NET.wallets:
        raise KeyError("없는 지갑 주소")
    ok, why = node_or_404(body.get("node", "A")).receive_transaction(
        Transaction(FAUCET, "", body["address"], FAUCET_AMOUNT))
    return jsonify({"ok": ok, "reason": why})


# ---- 2. Transaction (작성 -> 서명 -> 제출 3단계) ----------------------------
@app.post("/api/tx/prepare")
def tx_prepare():
    body = request.get_json(force=True)
    wallet = NET.wallets.get(body["from"])
    if wallet is None:
        raise KeyError("송신 지갑 없음")
    node = node_or_404(body.get("node", "A"))
    amount = int(body["amount"])
    nonce = node.pending_state().nonces.get(wallet.address, 0)
    tx = Transaction(TRANSFER, wallet.public_key, body["to"], amount, nonce)
    return jsonify({"tx": tx.to_dict(), "signing_hash": tx.signing_hash()})


@app.post("/api/tx/sign")
def tx_sign():
    tx = Transaction.from_dict(request.get_json(force=True)["tx"])
    wallet = NET.wallets.get(tx.sender_address)
    if wallet is None:
        raise KeyError("이 공개키에 해당하는 지갑이 없습니다")
    tx.sign(wallet)
    return jsonify({"tx": tx.to_dict()})


@app.post("/api/tx/submit")
def tx_submit():
    body = request.get_json(force=True)
    tx = Transaction.from_dict(body["tx"])
    ok, why = node_or_404(body.get("node", "A")).receive_transaction(tx)
    return jsonify({"ok": ok, "reason": why, "txid": tx.txid()})


# ---- 3. Mempool / 4. Mining ------------------------------------------------
@app.get("/api/nodes/<nid>/mempool")
def mempool(nid):
    return jsonify([t.to_dict() for t in node_or_404(nid).mempool])


@app.post("/api/nodes/<nid>/mine/start")
def mine_start(nid):
    body = request.get_json(silent=True) or {}
    miner = body.get("miner")
    if miner and miner not in NET.wallets:
        raise KeyError("없는 채굴자 지갑")
    return jsonify({"ok": True, "candidate": node_or_404(nid).start_mining(miner)})


@app.post("/api/nodes/<nid>/mine/step")
def mine_step(nid):
    batch = min(int((request.get_json(silent=True) or {}).get("batch", 3000)), 50000)
    return jsonify(node_or_404(nid).mine_step(batch))


# ---- 5. Blockchain (조회 / 검증 / 변조 실험 / 복구) -------------------------
@app.get("/api/nodes/<nid>/chain")
def chain(nid):
    bc = node_or_404(nid).blockchain
    valid, results = bc.validate_chain()
    blocks = [dict(b.to_dict(), **{"checks": r["checks"], "reasons": r["reasons"], "valid": r["valid"]})
              for b, r in zip(bc.chain, results)]
    return jsonify({"valid": valid, "height": bc.tip.index, "blocks": blocks})


@app.post("/api/nodes/<nid>/tamper")
def tamper(nid):
    """변조 시뮬레이션. mode: simple(내용만) / rehash(머클·해시 재계산) / remine(그 뒤 블록까지 PoW 재수행)"""
    body = request.get_json(force=True)
    node = node_or_404(nid)
    chain_ = node.blockchain.chain
    bi, ti = int(body["block_index"]), int(body["tx_index"])
    if not (0 <= bi < len(chain_)) or not (0 <= ti < len(chain_[bi].transactions)):
        raise ValueError("블록/거래 번호가 범위를 벗어났습니다 (Genesis에는 거래가 없습니다)")
    tx, field, mode = chain_[bi].transactions[ti], body["field"], body.get("mode", "simple")
    if field == "amount":
        tx.amount = int(body["value"])
    elif field == "receiver_address":
        tx.receiver_address = str(body["value"])
    else:
        raise ValueError("변조 가능한 필드: amount, receiver_address")
    attempts, t0 = 0, time.time()
    if mode in ("rehash", "remine"):
        for i in range(bi, len(chain_) if mode == "remine" else bi + 1):
            blk = chain_[i]
            if i > bi:
                blk.previous_hash = chain_[i - 1].hash
            blk.merkle_root = blk.calculate_merkle_root()
            if mode == "remine":
                attempts += blk.mine_to_target()
            else:
                blk.hash = blk.calculate_hash()
    node.log("tamper", f"[실험] {node.name} 블록#{bi} 거래#{ti}의 {field}를 변조 (모드: {mode})")
    valid, results = node.blockchain.validate_chain()
    return jsonify({"ok": True, "valid": valid, "attempts": attempts,
                    "seconds": round(time.time() - t0, 2), "results": results})


@app.post("/api/nodes/<nid>/sync")
def sync(nid):
    node = node_or_404(nid)
    changed = node.resolve_conflicts()
    return jsonify({"ok": True, "changed": changed})


# ---- 6. Node 제어 / 로그 / 설정 --------------------------------------------
@app.post("/api/nodes/<nid>/online")
def online(nid):
    node = node_or_404(nid)
    node.online = bool(request.get_json(force=True)["online"])
    node.log("sys", f"{node.name} {'온라인' if node.online else '오프라인 (네트워크 분리)'}")
    return jsonify({"ok": True})


@app.post("/api/difficulty")
def difficulty():
    NET.set_difficulty(int(request.get_json(force=True)["difficulty"]))
    return jsonify({"ok": True, "difficulty": NET.difficulty})


@app.get("/api/events")
def events():
    since = int(request.args.get("since", 0))
    return jsonify([e for e in NET.events if e["seq"] > since])


@app.post("/api/reset")
def reset():
    global NET
    NET = Network()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # 로컬 학습용: 외부 접속이 불가능하도록 127.0.0.1에만 바인딩
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=False)
