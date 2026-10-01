"""HTTP API를 통한 실습 흐름, 오류 응답, 개인키 비노출."""

import json

import pytest
from fastapi.testclient import TestClient

from mini_bitcoin_backend import __version__
from mini_bitcoin_backend.api.app import create_app
from mini_bitcoin_backend.service.registry import LabRegistry


class Api:
    """응답을 모두 기록하는 얇은 테스트 클라이언트."""

    def __init__(self, registry: LabRegistry | None = None) -> None:
        self.client = TestClient(create_app(registry))
        self.bodies: list[str] = []

    def call(self, method: str, path: str, body=None, status: int = 200):
        response = self.client.request(method, f"/api{path}", json=body)
        assert response.status_code == status, response.text
        self.bodies.append(response.text)
        return response.json() if response.content else None

    def post(self, path: str, body=None, status: int = 200):
        return self.call("POST", path, body, status)

    def get(self, path: str, status: int = 200):
        return self.call("GET", path, None, status)

    def error(self, method: str, path: str, body, status: int) -> dict:
        return self.call(method, path, body, status)["error"]


@pytest.fixture
def api():
    return Api()


@pytest.fixture
def lab(api):
    return api.post("/labs", {"mode": "example", "difficulty": 1}, 201)["lab"]


def transfer(api, lab, node="A", sender=0, recipient=1, amount=30):
    wallets = lab["wallets"]
    drafted = api.post(
        f"/labs/{lab['labId']}/nodes/{node}/drafts",
        {
            "senderAddress": wallets[sender]["address"],
            "recipientAddress": wallets[recipient]["address"],
            "amount": amount,
        },
    )
    signed = api.post(f"/labs/{lab['labId']}/wallets/sign", {"draft": drafted["draft"]})["signed"]
    return drafted, signed


def submit(api, lab, signed, node="A"):
    return api.post(f"/labs/{lab['labId']}/nodes/{node}/transactions", {"transaction": signed})


def deliver_all(api, lab):
    snapshot = lab
    while True:
        result = api.post(f"/labs/{lab['labId']}/messages/deliver-next")
        snapshot = result["lab"]
        if result["validation"] is None:
            return snapshot


def mine(api, lab, node="A"):
    """한 번 계산을 반복해 시간에 의존하지 않고 채굴한다."""
    result = api.post(f"/labs/{lab['labId']}/mining", {"nodeId": node, "step": True})
    while result["mining"]["status"] == "paused":
        job = result["mining"]["jobId"]
        result = api.post(f"/labs/{lab['labId']}/mining/{job}/step")
    return result


def balances(snapshot, node):
    return [
        snapshot["nodes"][node]["balances"][w["address"]]["confirmed"] for w in snapshot["wallets"]
    ]


def test_health_reports_version(api):
    assert api.get("/health") == {"status": "ok", "version": __version__}


def test_example_lab_snapshot_shape(lab):
    assert lab["phase"] == "running"
    assert [w["name"] for w in lab["wallets"]] == ["Alice", "Bob"]
    assert set(lab["wallets"][0]) == {"name", "publicKey", "address"}
    assert lab["difficulty"] == lab["genesis"]["difficulty"] == 1
    assert lab["targetHex"] == "0" + "f" * 63
    assert set(lab["nodes"]) == {"A", "B", "C"}
    assert lab["limits"] == {"miningSampleLimit": 12, "learningIntervalMs": 250}
    assert lab["queue"] == [] and lab["mining"] is None and lab["lastDelivery"] is None
    node = lab["nodes"]["A"]
    assert set(node) == {
        "id",
        "chain",
        "utxos",
        "mempool",
        "lastValidation",
        "chainValidation",
        "reserved",
        "balances",
        "canStartMining",
    }
    assert node["chain"][0]["previousHash"] == "0" * 64
    assert node["chain"][0]["allocations"] == [
        {"address": lab["wallets"][0]["address"], "amount": 100}
    ]
    assert node["chainValidation"]["valid"] is True
    assert node["canStartMining"] is False
    assert balances(lab, "A") == [100, 0]


def test_full_flow_reaches_70_30_on_every_node(api, lab):
    drafted, signed = transfer(api, lab)
    assert [o["amount"] for o in drafted["draft"]["outputs"]] == [30, 70]
    assert drafted["inputs"][0]["amount"] == 100
    assert drafted["signingText"].startswith("[1,")
    assert len(drafted["signingHash"]) == 64

    result = submit(api, lab, signed)
    assert result["validation"]["code"] == "VALID"
    after = result["lab"]
    alice = lab["wallets"][0]["address"]
    assert after["nodes"]["A"]["balances"][alice] == {"confirmed": 100, "spendable": 0}
    assert after["nodes"]["A"]["reserved"] == [
        f"{i['txId']}:{i['outputIndex']}" for i in signed["inputs"]
    ]
    assert [(m["from"], m["to"], m["kind"]) for m in after["queue"]] == [
        ("A", "B", "transaction"),
        ("A", "C", "transaction"),
    ]
    assert "payload" not in after["queue"][0]
    assert after["nodes"]["A"]["canStartMining"] is True
    assert len(after["nodes"]["B"]["mempool"]) == 0
    assert after["revision"] > lab["revision"]

    after = deliver_all(api, lab)
    assert after["lastDelivery"]["message"]["to"] == "C"
    assert after["lastDelivery"]["validation"]["valid"] is True
    assert len(after["nodes"]["C"]["mempool"]) == 1

    found = mine(api, lab)
    assert found["mining"]["status"] == "found"
    assert found["mining"]["nextTickInMs"] is None
    assert found["mining"]["samples"][-1]["passed"] is True
    assert found["mining"]["block"]["hash"] == found["lab"]["nodes"]["A"]["chain"][1]["hash"]
    # 채굴 노드만 먼저 반영하고, 나머지는 블록 메시지를 받아 직접 검증한 뒤 반영한다.
    assert [len(found["lab"]["nodes"][n]["chain"]) for n in "ABC"] == [2, 1, 1]
    assert [m["kind"] for m in found["lab"]["queue"]] == ["block", "block"]
    assert found["lab"]["nodes"]["B"]["canStartMining"] is False

    final = deliver_all(api, lab)
    for node in "ABC":
        assert balances(final, node) == [70, 30]
        assert final["nodes"][node]["mempool"] == []
        assert final["nodes"][node]["chainValidation"]["valid"] is True
    assert api.get(f"/labs/{lab['labId']}")["lab"] == final


def test_manual_start_needs_two_wallets_and_names_them(api):
    lab = api.post("/labs", {"mode": "manual"}, 201)["lab"]
    assert lab == {"labId": lab["labId"], "phase": "setup", "revision": 0, "wallets": []}
    path = f"/labs/{lab['labId']}"
    error = api.error("POST", f"{path}/start", {"difficulty": 2}, 400)
    assert error == {
        "code": "NOT_ENOUGH_WALLETS",
        "message": "실습을 시작하려면 지갑 두 개가 필요합니다.",
    }
    assert api.post(f"{path}/wallets")["wallet"]["name"] == "Alice"
    assert api.post(f"{path}/wallets")["wallet"]["name"] == "Bob"
    started = api.post(f"{path}/start", {"difficulty": 3})["lab"]
    assert started["phase"] == "running" and started["difficulty"] == 3
    assert balances(started, "A") == [100, 0]
    added = api.post(f"{path}/wallets")
    assert added["wallet"]["name"] == "학생 3"
    assert balances(added["lab"], "B") == [100, 0, 0]
    assert api.error("POST", f"{path}/start", {"difficulty": 2}, 409)["code"] == "LAB_STARTED"


def test_private_keys_only_leave_through_the_reveal_endpoint(api, lab):
    _, signed = transfer(api, lab)
    submit(api, lab, signed)
    deliver_all(api, lab)
    mine(api, lab)
    deliver_all(api, lab)
    api.post(f"/labs/{lab['labId']}/nodes/A/experiments/tamper", {"height": 1, "mode": "header"})
    recorded = "".join(api.bodies)

    keys = [
        api.get(f"/labs/{lab['labId']}/wallets/{w['address']}/private-key")["privateKey"]
        for w in lab["wallets"]
    ]
    assert len(set(keys)) == 2 and all(len(k) == 64 for k in keys)
    for key in keys:
        assert key not in recorded
    assert "privateKey" not in recorded
    response = api.client.get(
        f"/api/labs/{lab['labId']}/wallets/{lab['wallets'][0]['address']}/private-key"
    )
    assert response.headers["cache-control"] == "no-store"
    assert api.error("GET", f"/labs/{lab['labId']}/wallets/mini_x/private-key", None, 400) == {
        "code": "UNKNOWN_WALLET",
        "message": "이 실습에 없는 지갑입니다.",
    }


def test_domain_errors_use_korean_messages(api, lab):
    path = f"/labs/{lab['labId']}"
    alice, bob = (w["address"] for w in lab["wallets"])

    def draft(**changes):
        body = {"senderAddress": alice, "recipientAddress": bob, "amount": 30} | changes
        return api.error("POST", f"{path}/nodes/A/drafts", body, 400)

    for amount in (None, 0, -1, 1.5, "30", True):
        assert draft(amount=amount) == {
            "code": "INVALID_AMOUNT",
            "message": "금액은 1 이상의 안전한 정수여야 합니다.",
        }
    assert draft(amount=101)["message"].startswith("송금 가능 잔액 부족: 100 코인입니다.")
    assert draft(recipientAddress="bob")["message"] == "올바른 교육용 수신 주소를 선택하세요."
    assert draft(senderAddress="nobody")["code"] == "UNKNOWN_WALLET"
    assert api.error("POST", f"{path}/wallets/sign", {"draft": {"version": 1}}, 400)["code"] == (
        "INVALID_DRAFT"
    )
    for difficulty in (0, 5, "2", 2.0, None):
        error = api.error("POST", "/labs", {"mode": "example", "difficulty": difficulty}, 400)
        assert error == {"code": "INVALID_DIFFICULTY", "message": "난이도 범위는 1~4입니다."}


def test_request_and_lookup_errors_share_one_envelope(api, lab):
    path = f"/labs/{lab['labId']}"
    assert api.error("POST", f"{path}/nodes/Z/transactions", {}, 400)["code"] == "INVALID_REQUEST"
    assert api.error("POST", "/labs", {"mode": "other"}, 400)["code"] == "INVALID_REQUEST"
    missing = api.error("GET", "/labs/unknown", None, 404)
    assert missing["code"] == "LAB_NOT_FOUND"
    assert "실습을 찾을 수 없습니다" in missing["message"]
    assert api.error("GET", "/nowhere", None, 404)["code"] == "HTTP_ERROR"


def test_rejected_transactions_are_results_not_errors(api, lab):
    for junk in (None, 1, "tx", [], {"version": 1}):
        result = submit(api, lab, junk)
        assert result["validation"]["code"] == "FORMAT"
        assert result["validation"]["steps"][-1]["ok"] is False
    _, signed = transfer(api, lab)
    assert submit(api, lab, signed)["validation"]["code"] == "VALID"
    # 필드 순서가 달라도 같은 거래로 알아본다.
    reordered = dict(reversed(list(signed.items())))
    assert submit(api, lab, reordered)["validation"]["code"] == "DUPLICATE"
    assert submit(api, lab, signed, node="B")["validation"]["code"] == "VALID"


def test_mining_guards_and_stale_jobs(api):
    # 난이도 1에서는 첫 Nonce가 1/16 확률로 성공한다. 첫 계산이 실패한 실습으로 검사한다.
    for _ in range(50):
        lab = api.post("/labs", {"mode": "example", "difficulty": 1}, 201)["lab"]
        path = f"/labs/{lab['labId']}"
        assert api.error("POST", f"{path}/mining", {"nodeId": "A"}, 409) == {
            "code": "EMPTY_MEMPOOL",
            "message": "채굴할 유효 거래가 Mempool에 없습니다.",
        }
        _, signed = transfer(api, lab)
        submit(api, lab, signed)
        deliver_all(api, lab)
        body = {"nodeId": "A", "mode": "learning", "step": True}
        first = api.post(f"{path}/mining", body)["mining"]
        if first["status"] == "paused":
            break
    assert (first["status"], first["attempts"], first["mode"]) == ("paused", 1, "learning")
    assert first["block"]["nonce"] == 0 and first["nextTickInMs"] is None
    assert api.error("POST", f"{path}/mining", {"nodeId": "B"}, 409)["code"] == "MINING_ACTIVE"
    assert api.error("POST", f"{path}/mining/other/tick", None, 409)["code"] == "STALE_JOB"
    assert api.error("POST", f"{path}/mining/{first['jobId']}/jump", None, 400)["code"] == (
        "INVALID_REQUEST"
    )
    # 일시정지 중의 tick은 계산하지 않는다.
    held = api.post(f"{path}/mining/{first['jobId']}/tick")
    assert held["mining"]["attempts"] == 1 and held["lab"] is None
    stopped = api.post(f"{path}/mining/{first['jobId']}/stop")["mining"]
    assert stopped["status"] == "stopped"
    assert api.get(path)["lab"]["mining"]["status"] == "stopped"

    found = mine(api, lab, "B")
    assert found["mining"]["jobId"] != first["jobId"]
    assert found["mining"]["node"] == "B"
    # 이전 작업의 늦은 요청은 새 작업에 영향을 주지 않는다.
    assert api.error("POST", f"{path}/mining/{first['jobId']}/resume", None, 409)["code"] == (
        "STALE_JOB"
    )
    late = api.post(f"{path}/mining/{found['mining']['jobId']}/tick")
    assert late["mining"]["attempts"] == found["mining"]["attempts"] and late["lab"] is None

    _, second = transfer(api, found["lab"], node="B", amount=10)
    submit(api, lab, second, node="B")
    error = api.error("POST", f"{path}/mining", {"nodeId": "B"}, 409)
    assert error == {"code": "BLOCK_PENDING", "message": "이전 블록의 전달을 먼저 완료하세요."}


def test_failure_experiments(api, lab):
    path = f"/labs/{lab['labId']}"
    assert api.error("POST", f"{path}/nodes/A/experiments/double-spend", None, 409)["code"] == (
        "EMPTY_MEMPOOL"
    )
    assert api.error("POST", f"{path}/nodes/A/experiments/invalid-pow-block", None, 409)[
        "code"
    ] == ("EMPTY_MEMPOOL")
    _, signed = transfer(api, lab)

    forged = api.post(f"{path}/nodes/A/experiments/forged-signature", {"transaction": signed})
    assert forged["validation"]["code"] == "SIGNATURE"
    assert forged["checked"]["signature"] == "ff" * 64
    assert forged["checked"]["id"] != signed["id"]
    assert forged["lab"]["nodes"]["A"]["mempool"] == []

    submit(api, lab, signed)
    conflict = api.post(f"{path}/nodes/A/experiments/double-spend")
    assert conflict["validation"]["code"] == "DOUBLE_SPEND"
    assert conflict["conflict"]["inputs"] == signed["inputs"]
    assert conflict["conflict"]["outputs"][0]["address"] == lab["wallets"][0]["address"]
    assert conflict["inputs"][0]["amount"] == 100
    assert len(conflict["lab"]["nodes"]["A"]["mempool"]) == 1

    bad = api.post(f"{path}/nodes/A/experiments/invalid-pow-block")
    assert bad["validation"]["code"] == "POW"
    assert bad["lab"]["nodes"]["A"]["lastValidation"]["code"] == "POW"
    assert len(bad["lab"]["nodes"]["A"]["chain"]) == 1
    assert len(bad["lab"]["queue"]) == 2


def test_tamper_report_leaves_the_chain_untouched(api, lab):
    path = f"/labs/{lab['labId']}"
    _, signed = transfer(api, lab)
    submit(api, lab, signed)
    deliver_all(api, lab)
    mine(api, lab)
    before = deliver_all(api, lab)

    def tamper(**body):
        return api.post(f"{path}/nodes/A/experiments/tamper", body)

    changed = tamper(height=1, mode="transaction", amount=999)
    assert changed["altered"]["transactions"][0]["outputs"][0]["amount"] == 999
    assert changed["chainValidation"]["code"] == "TRANSACTIONS_HASH"
    assert changed["hasNextBlock"] is False
    assert {c["label"]: c["ok"] for c in changed["checks"]} == {
        "거래 전자서명": False,
        "거래 ID 일치": False,
        "거래 목록 해시": False,
        "헤더와 블록 해시 일치": True,
        "현재 헤더의 작업증명": True,
    }
    rehashed = tamper(height=1, mode="transaction", amount=999, recomputeHashes=True)
    assert rehashed["altered"]["hash"] == rehashed["recomputedHash"] != changed["altered"]["hash"]
    assert [c["ok"] for c in rehashed["checks"]][2:4] == [True, True]
    header = tamper(height=1, mode="header")
    assert header["altered"]["timestamp"] == before["nodes"]["A"]["chain"][1]["timestamp"] + 1
    assert header["chainValidation"]["code"] == "BLOCK_HASH"

    for body in ({"height": 0, "mode": "header"}, {"height": 2, "mode": "header"}):
        assert api.error("POST", f"{path}/nodes/A/experiments/tamper", body, 400)["code"] == (
            "INVALID_HEIGHT"
        )
    bad_amount = {"height": 1, "mode": "transaction", "amount": 0}
    assert api.error("POST", f"{path}/nodes/A/experiments/tamper", bad_amount, 400)["code"] == (
        "INVALID_AMOUNT"
    )
    assert api.get(path)["lab"] == before


def test_reset_and_eviction_forget_the_lab():
    api = Api(LabRegistry(max_labs=2))
    first = api.post("/labs", {"mode": "example", "difficulty": 1}, 201)["lab"]["labId"]
    second = api.post("/labs", {"mode": "manual"}, 201)["lab"]["labId"]
    api.get(f"/labs/{first}")
    third = api.post("/labs", {"mode": "manual"}, 201)["lab"]["labId"]
    # 가장 오래 쓰지 않은 실습(second)이 정리된다.
    assert api.error("GET", f"/labs/{second}", None, 404)["code"] == "LAB_NOT_FOUND"
    api.get(f"/labs/{first}")
    api.call("DELETE", f"/labs/{first}", None, 204)
    api.call("DELETE", f"/labs/{first}", None, 204)
    assert api.error("POST", f"/labs/{first}/messages/deliver-next", None, 404)["code"] == (
        "LAB_NOT_FOUND"
    )
    assert json.loads(api.bodies[-1])["error"]["message"].startswith("실습을 찾을 수 없습니다")
    api.get(f"/labs/{third}")
