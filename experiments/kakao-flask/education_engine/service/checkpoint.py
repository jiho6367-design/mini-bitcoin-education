"""Versioned JSON checkpoints. Only explicitly allowlisted educational objects are decoded."""

import time
import uuid
from collections import deque
from dataclasses import fields, is_dataclass

from ..core import types
from ..core.block import validate_chain
from ..core.crypto import create_wallet
from ..core.lab import Lab
from ..core.mining import MiningJob, MiningSample
from ..core.transaction import reserved_inputs, validate_transaction
from .race import MiningRace

RECORDS = {
    cls.__name__: cls
    for cls in (
        types.Wallet,
        types.TxInput,
        types.TxOutput,
        types.UnsignedTransaction,
        types.Transaction,
        types.UTXO,
        types.Block,
        types.Check,
        types.ValidationResult,
        types.NodeState,
        types.NetworkMessage,
        types.Delivery,
        types.LabEvent,
        MiningSample,
    )
}
OBJECTS = {"Lab": Lab, "MiningJob": MiningJob, "MiningRace": MiningRace}
OMIT = {"_clock", "_waiting_since", "_event_sink"}


def encode(value):
    if value is None or type(value) in (str, int, float, bool):
        return value
    if is_dataclass(value):
        return {
            "record": type(value).__name__,
            "fields": {f.name: encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, dict):
        return {"map": {k: encode(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple, set, deque)):
        return {"sequence": type(value).__name__, "items": [encode(v) for v in value]}
    if type(value).__name__ in OBJECTS and type(value) in OBJECTS.values():
        return {
            "object": type(value).__name__,
            "fields": {
                k: encode(v)
                for k, v in vars(value).items()
                if k not in OMIT and not (isinstance(value, MiningRace) and k == "lab")
            },
        }
    raise ValueError(f"Unsupported checkpoint value: {type(value).__name__}")


def decode(value):
    if not isinstance(value, dict):
        return value
    if "map" in value:
        return {k: decode(v) for k, v in value["map"].items()}
    if "sequence" in value:
        items = [decode(v) for v in value["items"]]
        return {"list": list, "tuple": tuple, "set": set, "deque": lambda x: deque(x, maxlen=12)}[
            value["sequence"]
        ](items)
    attrs = {k: decode(v) for k, v in value["fields"].items()}
    if "record" in value:
        return RECORDS[value["record"]](**attrs)
    cls = OBJECTS[value["object"]]
    # Reject arbitrary attributes, even for known classes.
    allowed = {
        Lab: {
            "session_id",
            "wallets",
            "genesis",
            "nodes",
            "miners",
            "queue",
            "events",
            "last_validation",
            "last_delivery",
            "_sequence",
        },
        MiningJob: {
            "job_id",
            "node",
            "candidate",
            "mode",
            "status",
            "attempts",
            "elapsed_ms",
            "observation_ms",
            "samples",
            "latest",
            "message",
            "seq",
        },
        MiningRace: {
            "id",
            "guided",
            "mode",
            "status",
            "seq",
            "rotation",
            "enabled",
            "single_node",
            "jobs",
            "completed",
            "message",
            "target_reward",
            "advance_node",
            "advance_start",
        },
    }[cls]
    # Existing stored competitions predate the explicit single-node scope.
    if cls is MiningRace and "single_node" not in attrs:
        attrs["single_node"] = None
    if set(attrs) != allowed:
        raise ValueError("Invalid checkpoint fields")
    if cls is MiningRace and attrs["single_node"] is not None and attrs["single_node"] not in types.NODE_IDS:
        raise ValueError("Invalid single mining node")
    obj = cls.__new__(cls)
    obj.__dict__.update(attrs)
    if cls is MiningJob:
        obj._clock = time.perf_counter
        obj._waiting_since = None
    return obj


def capture(session):
    return {
        "version": 1,
        "wallets": encode(session.wallets),
        "lab": encode(session.lab),
        "mining": encode(session.mining),
        "race": encode(session.race),
        "revision": session.revision,
    }


def restore(session, data, *, fresh=True, validate=True):
    if data.get("version") != 1:
        raise ValueError("지원하지 않는 저장 형식입니다.")
    wallets, lab = decode(data["wallets"]), decode(data["lab"])
    mining, race = decode(data["mining"]), decode(data["race"])
    if validate:
        for wallet in wallets + (list(lab.miners.values()) if lab else []):
            if create_wallet(wallet.name, wallet.private_key) != wallet:
                raise ValueError("지갑 키 검증에 실패했습니다.")
    if lab and validate:
        if wallets != lab.wallets or lab.session_id != lab.genesis.session_id:
            raise ValueError("실습 식별자 또는 지갑이 다릅니다.")
        if set(lab.nodes) != set(types.NODE_IDS):
            raise ValueError("노드가 누락됐습니다.")
        for node in lab.nodes.values():
            if not validate_chain(node.chain, lab.genesis).valid:
                raise ValueError("저장된 체인 검증에 실패했습니다.")
            for key, block in node.blocks.items():
                branch = [block]
                visited = {block.hash}
                while branch[-1].height:
                    parent = node.blocks[branch[-1].previous_hash]
                    if parent.hash in visited or parent.height != branch[-1].height - 1:
                        raise ValueError("분기 연결이 올바르지 않습니다.")
                    branch.append(parent)
                    visited.add(parent.hash)
                if (
                    key != block.hash
                    or not validate_chain(list(reversed(branch)), lab.genesis).valid
                ):
                    raise ValueError("저장된 분기 검증에 실패했습니다.")
            if any(node.blocks.get(block.hash) != block for block in node.chain):
                raise ValueError("활성 체인과 블록 목록이 다릅니다.")
            if lab.branch_utxos(node.chain) != node.utxos:
                raise ValueError("저장된 UTXO가 체인과 다릅니다.")
            pending = []
            for tx in node.mempool:
                if not validate_transaction(
                    tx,
                    node.utxos,
                    lab.session_id,
                    reserved_inputs(pending),
                    node.chain[-1].height + 1,
                ).valid:
                    raise ValueError("저장된 멤풀 검증에 실패했습니다.")
                pending.append(tx)
    session.lab = lab
    session.wallets = lab.wallets if lab else wallets
    session.mining, session.race = mining, race
    session.revision = data["revision"]
    if race:
        race.lab = lab
    if fresh:
        for job in ([mining] if mining else []) + (list(race.jobs.values()) if race else []):
            job.job_id = str(uuid.uuid4())
            if job.status == "running":
                job.status = "paused"
        if race:
            race.id = str(uuid.uuid4())
            if race.status == "running":
                race.status = "paused"
