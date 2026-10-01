"""화면이 그리는 데 필요한 실습 상태 전체를 JSON 형태로 만든다."""

from typing import Any

from ..core.block import target_hex, validate_chain
from ..core.lab import Lab
from ..core.mining import LEARNING_INTERVAL_MS, MINING_SAMPLE_LIMIT, MiningJob
from ..core.transaction import balance, reserved_inputs
from ..core.types import NODE_IDS, NetworkMessage, NodeState, Wallet
from ..core.wire import public_wallet, to_wire

LIMITS = {"miningSampleLimit": MINING_SAMPLE_LIMIT, "learningIntervalMs": LEARNING_INTERVAL_MS}


def message_wire(message: NetworkMessage) -> dict[str, Any]:
    # 페이로드는 화면에 표시하지 않으므로 경로와 종류만 보낸다.
    return {"id": message.id, "from": message.sender, "to": message.to, "kind": message.kind}


def mining_wire(job: MiningJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "jobId": job.job_id,
        "seq": job.seq,
        "status": job.status,
        "node": job.node,
        "mode": job.mode,
        "attempts": job.attempts,
        "elapsedMs": job.elapsed_ms,
        "observationMs": job.observation_ms,
        "samples": to_wire(list(job.samples)),
        "block": to_wire(job.latest or job.candidate),
        "message": job.message,
        "nextTickInMs": job.next_tick_in_ms,
    }


def _node_wire(lab: Lab, node: NodeState, block_queued: bool) -> dict[str, Any]:
    reserved = reserved_inputs(node.mempool)
    return {
        "id": node.id,
        "chain": to_wire(node.chain),
        "utxos": to_wire(node.utxos),
        "mempool": to_wire(node.mempool),
        "lastValidation": to_wire(node.last_validation),
        "chainValidation": to_wire(validate_chain(node.chain, lab.genesis)),
        "reserved": sorted(reserved),
        "balances": {
            wallet.address: {
                "confirmed": balance(node.utxos, wallet.address),
                "spendable": balance(node.utxos, wallet.address, reserved),
            }
            for wallet in lab.wallets
        },
        "canStartMining": bool(node.mempool) and not block_queued,
    }


def lab_snapshot(
    lab_id: str,
    revision: int,
    wallets: list[Wallet],
    lab: Lab | None,
    mining: MiningJob | None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "labId": lab_id,
        "phase": "setup" if lab is None else "running",
        "revision": revision,
        "wallets": [public_wallet(w) for w in wallets],
    }
    if lab is None:
        return snapshot
    block_queued = any(m.kind == "block" for m in lab.queue)
    delivery = lab.last_delivery
    snapshot.update(
        {
            "sessionId": lab.session_id,
            "genesis": to_wire(lab.genesis),
            "difficulty": lab.genesis.difficulty,
            "targetHex": target_hex(lab.genesis.difficulty),
            "nodes": {n: _node_wire(lab, lab.nodes[n], block_queued) for n in NODE_IDS},
            "queue": [message_wire(m) for m in lab.queue],
            "events": to_wire(lab.events),
            "lastValidation": to_wire(lab.last_validation),
            "lastDelivery": None
            if delivery is None
            else {
                "message": message_wire(delivery.message),
                "validation": to_wire(delivery.validation),
            },
            "mining": mining_wire(mining),
            "limits": LIMITS,
        }
    )
    return snapshot
