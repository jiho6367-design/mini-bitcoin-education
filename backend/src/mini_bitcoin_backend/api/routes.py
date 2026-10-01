"""API 경로. 각 경로는 실습(LabSession)의 동작 하나에 대응한다."""

from typing import Any, Literal

from fastapi import APIRouter, Response

from .. import __version__
from ..core.types import NodeId
from ..service.registry import LabRegistry
from .schemas import (
    CreateLabRequest,
    DraftRequest,
    MiningRequest,
    SignRequest,
    StartLabRequest,
    TamperRequest,
    TransactionRequest,
)

MiningCommand = Literal["tick", "pause", "resume", "step", "stop"]


def create_router(registry: LabRegistry) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # ----- 실습 생성·조회·삭제 -----

    @router.post("/labs", status_code=201)
    def create_lab(body: CreateLabRequest) -> dict[str, Any]:
        session = registry.create()
        if body.mode == "example":
            try:
                return session.start(body.difficulty, example=True)
            except Exception:
                registry.delete(session.lab_id)
                raise
        return {"lab": session.snapshot()}

    @router.get("/labs/{lab_id}")
    def read_lab(lab_id: str) -> dict[str, Any]:
        return {"lab": registry.get(lab_id).snapshot()}

    @router.delete("/labs/{lab_id}", status_code=204)
    def delete_lab(lab_id: str) -> Response:
        registry.delete(lab_id)
        return Response(status_code=204)

    @router.post("/labs/{lab_id}/wallets")
    def add_wallet(lab_id: str) -> dict[str, Any]:
        return registry.get(lab_id).add_wallet()

    @router.get("/labs/{lab_id}/wallets/{address}/private-key")
    def private_key(lab_id: str, address: str, response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "no-store"
        return registry.get(lab_id).private_key(address)

    @router.post("/labs/{lab_id}/start")
    def start_lab(lab_id: str, body: StartLabRequest) -> dict[str, Any]:
        return registry.get(lab_id).start(body.difficulty)

    # ----- 거래 -----

    @router.post("/labs/{lab_id}/nodes/{node_id}/drafts")
    def draft(lab_id: str, node_id: NodeId, body: DraftRequest) -> dict[str, Any]:
        return registry.get(lab_id).draft(
            node_id, body.senderAddress, body.recipientAddress, body.amount
        )

    @router.post("/labs/{lab_id}/wallets/sign")
    def sign(lab_id: str, body: SignRequest) -> dict[str, Any]:
        return registry.get(lab_id).sign(body.draft)

    @router.post("/labs/{lab_id}/nodes/{node_id}/transactions")
    def submit(lab_id: str, node_id: NodeId, body: TransactionRequest) -> dict[str, Any]:
        return registry.get(lab_id).submit(node_id, body.transaction)

    @router.post("/labs/{lab_id}/messages/deliver-next")
    def deliver_next(lab_id: str) -> dict[str, Any]:
        return registry.get(lab_id).deliver_next()

    # ----- 채굴 -----

    @router.post("/labs/{lab_id}/mining")
    def start_mining(lab_id: str, body: MiningRequest) -> dict[str, Any]:
        return registry.get(lab_id).start_mining(body.nodeId, body.mode, body.step)

    @router.post("/labs/{lab_id}/mining/{job_id}/{command}")
    def mining_command(lab_id: str, job_id: str, command: MiningCommand) -> dict[str, Any]:
        return registry.get(lab_id).mining_command(job_id, command)

    # ----- 실패 실험 -----

    @router.post("/labs/{lab_id}/nodes/{node_id}/experiments/forged-signature")
    def forged_signature(lab_id: str, node_id: NodeId, body: TransactionRequest) -> dict[str, Any]:
        return registry.get(lab_id).forged_signature(node_id, body.transaction)

    @router.post("/labs/{lab_id}/nodes/{node_id}/experiments/double-spend")
    def double_spend(lab_id: str, node_id: NodeId) -> dict[str, Any]:
        return registry.get(lab_id).double_spend(node_id)

    @router.post("/labs/{lab_id}/nodes/{node_id}/experiments/invalid-pow-block")
    def invalid_pow_block(lab_id: str, node_id: NodeId) -> dict[str, Any]:
        return registry.get(lab_id).invalid_pow_block(node_id)

    @router.post("/labs/{lab_id}/nodes/{node_id}/experiments/tamper")
    def tamper(lab_id: str, node_id: NodeId, body: TamperRequest) -> dict[str, Any]:
        return registry.get(lab_id).tamper(
            node_id, body.height, body.mode, body.amount, body.recomputeHashes
        )

    return router
