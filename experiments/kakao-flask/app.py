"""로컬 교육용 Flask 앱: 격리된 실습, SQLite 저장, 단계별 채굴·변조 실험."""
import math
import os
import re
import time
from contextlib import nullcontext
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from blockchain import BLOCK_REWARD, FAUCET_AMOUNT, MAX_DIFFICULTY, MIN_DIFFICULTY
from node import Network
from operations import OperationError, PracticeManager, TamperJob, chain_fingerprint
from storage import PracticeStore, StorageError
from transaction import FAUCET, TRANSFER, Transaction

APP_VERSION = "3.0.0"
COOKIE_NAME = "mini_bitcoin_practice"
MAX_BATCH = 5000
MAX_INTEGER = 2 ** 53 - 1


def integer(value, label, minimum=0, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise OperationError("INVALID_REQUEST", f"{label}: {minimum}~{maximum} 범위의 정수를 입력하세요.")
    return value


def text(value, label, maximum=128, *, blank=False):
    if not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip()):
        raise OperationError("INVALID_REQUEST", f"{label}: 올바른 문자열을 입력하세요 (최대 {maximum}자).")
    return value


def address(value, label="지갑 주소"):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise OperationError("INVALID_REQUEST", f"{label} 형식을 확인하세요.")
    return value


def transaction(body):
    raw = body.get("tx")
    if not isinstance(raw, dict):
        raise OperationError("INVALID_TRANSACTION", "tx 객체에 거래 내용을 전달하세요.")
    if raw.get("type") != TRANSFER:
        raise OperationError("INVALID_TRANSACTION", "이 경로에서는 일반 송금 거래만 처리합니다.")
    public = text(raw.get("sender_public_key"), "송신자 공개키", 256)
    receiver = address(raw.get("receiver_address"), "수신 지갑 주소")
    amount = integer(raw.get("amount"), "금액", 1)
    nonce = integer(raw.get("nonce"), "거래 순번")
    stamp = raw.get("timestamp")
    if type(stamp) not in (int, float) or not 0 <= stamp <= MAX_INTEGER or not math.isfinite(stamp):
        raise OperationError("INVALID_TRANSACTION", "거래 시각은 0 이상의 유한한 숫자여야 합니다.")
    signature = raw.get("signature")
    if signature is not None:
        text(signature, "전자서명", 512, blank=True)
    return Transaction(TRANSFER, public, receiver, amount, nonce, stamp, signature)


def create_app(config=None, *, database_path=None):
    app = Flask(__name__, static_folder="static", instance_relative_config=True)
    app.config.update(MAX_CONTENT_LENGTH=64 * 1024,
                      DATABASE=database_path or os.environ.get("KAKAO_PRACTICE_DB") or str(Path(app.instance_path) / "practices.sqlite3"))
    if config:
        app.config.update(config)
    app.config.setdefault("TEAM_APP_VERSION", APP_VERSION)
    app.config.setdefault("TEAM_DATABASE_URL", os.environ.get("TEAM_DATABASE_URL", ""))
    app.config.setdefault("TEAM_DATABASE_PREFIX", os.environ.get("TEAM_DATABASE_PREFIX", "team"))
    app.config.setdefault("TEAM_SHARED_ROOM_CODE", os.environ.get("TEAM_SHARED_ROOM_CODE", ""))
    app.config.setdefault("TEAM_PUBLIC_URL", os.environ.get("TEAM_PUBLIC_URL")
                          or os.environ.get("RENDER_EXTERNAL_URL", ""))
    app.config.setdefault("EDUCATION_ENABLED", not app.testing)
    app.config.setdefault("EDUCATION_RUNNER_ENABLED", not app.testing and os.environ.get("TEAM_EDUCATION_RUNNER", "1") == "1")
    app.config.setdefault("EDUCATION_FRONTEND", str(Path(__file__).parent / "frontend" / "dist"))
    if os.environ.get("TEAM_TRUST_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if app.config["TEAM_DATABASE_URL"]:
        from postgres_backend import PostgresBackend
        from postgres_basic_store import PostgresBasicStore
        backend = PostgresBackend(app.config["TEAM_DATABASE_URL"], prefix=app.config["TEAM_DATABASE_PREFIX"],
                                  defer_claim=True)
        app.extensions["postgres_backend"] = backend
        try:
            store = PostgresBasicStore(backend, max_practices=app.config.get("BASIC_MAX_PRACTICES", 500))
        except Exception:
            backend.close()
            raise
    else:
        store = PracticeStore(app.config["DATABASE"], max_practices=app.config.get("BASIC_MAX_PRACTICES", 500))
    manager = PracticeManager(store)
    app.extensions["practice_store"] = store
    app.extensions["practice_manager"] = manager

    def json_body(*, empty=False):
        if empty and not request.get_data():
            return {}
        if not request.is_json:
            raise OperationError("INVALID_JSON", "Content-Type: application/json 형식으로 요청하세요.")
        try:
            data = request.get_json(silent=True)
        except (ValueError, RecursionError, OverflowError) as error:
            raise OperationError("INVALID_JSON", "JSON을 해석할 수 없습니다. 입력 형식과 중첩 깊이를 확인하세요.") from error
        if not isinstance(data, dict):
            raise OperationError("INVALID_JSON", "JSON 객체를 전달하세요.")
        return data

    def node(p, nid):
        if not isinstance(nid, str) or nid not in p.network.nodes:
            raise OperationError("NODE_NOT_FOUND", "없는 노드입니다.", 404)
        return p.network.nodes[nid]

    def wallet(p, value):
        value = address(value)
        found = p.network.wallets.get(value)
        if found is None:
            raise OperationError("WALLET_NOT_FOUND", "이 실습에 없는 지갑입니다.", 404)
        return found

    def valid_node(p, nid):
        n = node(p, nid)
        if not n.blockchain.is_valid():
            raise OperationError("INVALID_CHAIN", "선택한 체인의 검증이 실패했습니다. 먼저 동기화로 복구하세요.", 409)
        return n

    def pending_space(n):
        if len(n.mempool) >= app.config.get("BASIC_MAX_PENDING", 300):
            raise OperationError("MEMPOOL_LIMIT", "대기 거래 한도에 도달했습니다. 먼저 채굴하세요.", 409)

    def active_mining(p):
        return any(n.candidate is not None for n in p.network.nodes.values())

    def no_mining(p):
        if active_mining(p):
            raise OperationError("MINING_ACTIVE", "진행 중인 채굴을 중지한 뒤 변조 실험을 실행하세요.", 409)

    def state_data(p):
        return {"version": APP_VERSION, "difficulty": p.network.difficulty,
                "difficulty_rules": p.network.difficulty_rules,
                "min_difficulty": MIN_DIFFICULTY, "max_difficulty": MAX_DIFFICULTY,
                "block_reward": BLOCK_REWARD, "faucet_amount": FAUCET_AMOUNT,
                "nodes": [n.snapshot() for n in p.network.nodes.values()],
                "mining_comparison": p.network.comparison_info(),
                "tamper_job": p.tamper_job.progress() if p.tamper_job else None}

    def response(data, status=200):
        p = getattr(g, "practice", None)
        if p is not None:
            g.response_revision = p.revision
            if isinstance(data, dict):
                data = {**data, "practice": p.metadata(), "revision": p.revision}
        return jsonify(data), status

    def read(action):
        with g.practice.lock:
            return response(action(g.practice))

    def change(body, action, *, allow_tamper=False):
        expected_practice = body.get("expected_practice_id")
        if expected_practice is not None:
            text(expected_practice, "실습 ID", 128)
        if expected_practice is not None and expected_practice != g.practice.id:
            raise OperationError("PRACTICE_CHANGED", "다른 탭에서 선택한 실습이 변경됐습니다. 현재 실습을 다시 확인하세요.", 409)
        expected = body.get("expected_revision")
        if expected is not None:
            integer(expected, "실습 버전")
        with g.practice.lock:
            result = manager.mutate(g.practice, expected, action, allow_tamper=allow_tamper)
            data, status = result if isinstance(result, tuple) else (result, 200)
            return response(data, status)

    @app.before_request
    def choose_practice():
        if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                message = "다른 출처의 변경 요청은 허용하지 않습니다"
                if request.path.startswith("/api/education/"):
                    return jsonify(error={"code": "ORIGIN_REJECTED", "message": message}), 403
                return jsonify(ok=False, code="ORIGIN_REJECTED", reason=message), 403
        if (request.endpoint is not None and request.path.startswith("/api/")
                and not request.path.startswith("/api/education/")
                and request.path != "/api/health"):
            g.practice = manager.get(request.cookies.get(COOKIE_NAME))

    @app.after_request
    def protect_response(r):
        if request.path.startswith("/api/"):
            r.headers["Cache-Control"] = "no-store"
        r.headers["X-Content-Type-Options"] = "nosniff"
        p = getattr(g, "practice", None)
        if p:
            r.headers["X-Practice-Revision"] = str(getattr(g, "response_revision", p.revision))
            if request.cookies.get(COOKIE_NAME) != p.id:
                r.set_cookie(COOKIE_NAME, p.id, max_age=365 * 24 * 60 * 60,
                             httponly=True, secure=request.is_secure, samesite="Lax", path="/")
        return r

    @app.errorhandler(OperationError)
    def operation_error(error):
        return response({"ok": False, "code": error.code, "reason": error.reason}, error.status)

    @app.errorhandler(StorageError)
    def storage_error(error):
        return response({"ok": False, "code": "STORAGE_ERROR", "reason": str(error)}, 503)

    @app.errorhandler(HTTPException)
    def http_error(error):
        return response({"ok": False, "code": "HTTP_ERROR", "reason": error.description}, error.code)

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.error("Unexpected request failure", exc_info=error)
        return response({"ok": False, "code": "INTERNAL_ERROR", "reason": "요청을 처리하지 못했습니다. 현재 상태를 확인한 뒤 다시 시도하세요."}, 500)

    @app.get("/")
    def index():
        if request.args.get("classroom"):
            return education_page()
        return basic()

    @app.get("/advanced")
    def education_page():
        # Preserve access to earlier private exercises without a public mode tab.
        frontend = Path(app.config["EDUCATION_FRONTEND"])
        if (frontend / "index.html").is_file():
            result = send_from_directory(frontend, "index.html")
            result.headers["Cache-Control"] = "no-cache"
            return result
        return "공동 실습 화면을 준비하지 못했습니다 잠시 후 다시 접속하세요", 503

    @app.get("/basic")
    def basic():
        result = send_from_directory(app.static_folder, "index.html")
        result.headers["Cache-Control"] = "no-cache"
        return result

    @app.get("/hashcash", strict_slashes=False)
    def hashcash():
        return education_page()

    @app.get("/education-assets/<path:filename>")
    def education_asset(filename):
        return send_from_directory(app.config["EDUCATION_FRONTEND"], filename)

    @app.get("/architecture")
    def architecture():
        return send_from_directory(app.static_folder, "architecture.html")

    @app.get("/api/health")
    def health():
        return response({"ok": True, "version": APP_VERSION})

    @app.get("/api/state")
    def state():
        return read(state_data)

    def wallet_info(w, n):
        valid = n.blockchain.is_valid()
        confirmed = n.blockchain.state().balance(w.address) if valid else None
        return {"name": w.name, "address": w.address, "public_key": w.public_key,
                "balance": confirmed, "balance_valid": valid,
                "pending_in": sum(t.amount for t in n.mempool if t.receiver_address == w.address),
                "pending_out": sum(t.amount for t in n.mempool if t.sender_address == w.address),
                "is_node_wallet": w.address == n.wallet.address or any(w.address == x.wallet.address for x in g.practice.network.nodes.values())}

    @app.get("/api/wallets")
    def wallets():
        return read(lambda p: [wallet_info(w, node(p, request.args.get("node", "A"))) for w in p.network.wallets.values()])

    @app.get("/api/wallets/<value>/keys")
    def keys(value):
        def reveal(p):
            w = wallet(p, value)
            return {"address": w.address, "public_key": w.public_key, "private_key": w.private_key}
        return read(reveal)

    @app.post("/api/wallets")
    def create_wallet():
        body = json_body()
        name = text(body.get("name"), "지갑 이름", 80).strip()
        def action(p):
            n = valid_node(p, body.get("node", "A"))
            if not n.online:
                raise OperationError("NODE_OFFLINE", "오프라인 노드에는 지급 거래를 제출할 수 없습니다.", 409)
            pending_space(n)
            if len(p.network.wallets) >= 100:
                raise OperationError("WALLET_LIMIT", "한 실습에서는 최대 100개 지갑을 사용할 수 있습니다.", 409)
            w = p.network.create_wallet(name)
            ok, why = n.receive_transaction(Transaction(FAUCET, "", w.address, FAUCET_AMOUNT))
            if not ok:
                raise OperationError("FAUCET_REJECTED", why, 409)
            return {"ok": True, "address": w.address, "faucet": {"ok": ok, "reason": why}}
        return change(body, action)

    @app.post("/api/faucet")
    def faucet():
        body = json_body()
        def action(p):
            w = wallet(p, body.get("address"))
            n = valid_node(p, body.get("node", "A"))
            pending_space(n)
            ok, why = n.receive_transaction(Transaction(FAUCET, "", w.address, FAUCET_AMOUNT))
            return {"ok": ok, "reason": why}
        return change(body, action)

    @app.post("/api/tx/prepare")
    def prepare():
        body = json_body()
        amount = integer(body.get("amount"), "금액", 1)
        def action(p):
            w = wallet(p, body.get("from"))
            target = wallet(p, body.get("to"))
            n = valid_node(p, body.get("node", "A"))
            nonce = n.pending_state().nonces.get(w.address, 0)
            tx = Transaction(TRANSFER, w.public_key, target.address, amount, nonce)
            return {"tx": tx.to_dict(), "signing_hash": tx.signing_hash()}
        return change(body, action)

    @app.post("/api/tx/sign")
    def sign():
        body = json_body()
        tx = transaction(body)
        def action(p):
            w = p.network.wallets.get(tx.sender_address)
            if w is None or w.public_key != tx.sender_public_key:
                raise OperationError("WALLET_NOT_FOUND", "이 실습의 송신자 지갑을 확인하세요.", 404)
            tx.sign(w)
            return {"tx": tx.to_dict()}
        return change(body, action)

    @app.post("/api/tx/submit")
    def submit():
        body = json_body()
        tx = transaction(body)
        def action(p):
            n = valid_node(p, body.get("node", "A"))
            pending_space(n)
            ok, why = n.receive_transaction(tx)
            return {"ok": ok, "reason": why, "txid": tx.txid()}
        return change(body, action)

    @app.get("/api/nodes/<nid>/mempool")
    def mempool(nid):
        return read(lambda p: [t.to_dict() for t in node(p, nid).mempool])

    def mining_job(p, nid, body):
        n = node(p, nid)
        job_id = text(body.get("job_id"), "채굴 작업 ID", 128)
        info = n.candidate_info()
        if not info or info.get("job_id") != job_id:
            raise OperationError("STALE_JOB", "교체되거나 종료된 채굴 작업입니다. 현재 상태를 확인하세요.", 409)
        if n.candidate is None:
            raise OperationError("NO_ACTIVE_JOB", "이미 종료된 채굴 작업입니다.", 409)
        return n, job_id

    def mining_result(result):
        if result.get("error") or result.get("ok") is False:
            raise OperationError(result.get("code", "MINING_CONFLICT"), result.get("error") or result.get("reason") or "채굴 상태를 확인하세요.", 409)
        return result

    @app.post("/api/nodes/<nid>/mine/start")
    def mine_start(nid):
        body = json_body(empty=True)
        def action(p):
            n = valid_node(p, nid)
            if len(n.blockchain.chain) - 1 >= app.config.get("BASIC_MAX_HEIGHT", 500):
                raise OperationError("CHAIN_LIMIT", "기초 실습의 블록 한도에 도달했습니다. 새 실습을 시작하세요.", 409)
            if n.candidate is not None:
                raise OperationError("ALREADY_MINING", "현재 채굴을 중지한 뒤 새 후보를 만드세요.", 409)
            miner = body.get("miner")
            if miner is not None:
                wallet(p, miner)
            try:
                candidate = n.start_mining(miner)
            except ValueError as error:
                raise OperationError("MINING_CONFLICT", str(error), 409) from error
            return {"ok": True, "candidate": candidate, "job_id": candidate["job_id"], "status": candidate["status"]}
        return change(body, action)

    @app.post("/api/nodes/<nid>/mine/step")
    def mine_step(nid):
        body = json_body()
        batch = integer(body.get("batch", 1), "계산 횟수", 1, MAX_BATCH)
        def action(p):
            n, job_id = mining_job(p, nid, body)
            return mining_result(n.mine_step(batch, job_id=job_id))
        return change(body, action)

    @app.post("/api/nodes/<nid>/mine/<command>")
    def mine_control(nid, command):
        if command not in ("pause", "resume", "stop"):
            raise OperationError("NOT_FOUND", "없는 채굴 명령입니다.", 404)
        body = json_body()
        def action(p):
            n, job_id = mining_job(p, nid, body)
            result = mining_result(getattr(n, command + "_mining")(job_id=job_id))
            return {**result, "candidate": n.candidate_info()}
        return change(body, action)

    def comparison_response(p, comparison):
        return {"ok": True, "comparison": comparison,
                "nodes": [n.snapshot() for n in p.network.nodes.values()]}

    @app.post("/api/mining/comparison/start")
    def comparison_start():
        body = json_body(empty=True)
        def action(p):
            if any(len(n.blockchain.chain) - 1 >= app.config.get("BASIC_MAX_HEIGHT", 500)
                   for n in p.network.nodes.values()):
                raise OperationError("CHAIN_LIMIT", "기초 실습의 블록 한도에 도달했습니다. 새 실습을 시작하세요.", 409)
            try:
                comparison = p.network.start_mining_comparison()
            except ValueError as error:
                raise OperationError("COMPARISON_CONFLICT", str(error), 409) from error
            return comparison_response(p, comparison)
        return change(body, action)

    @app.post("/api/mining/comparison/step")
    def comparison_step():
        body = json_body()
        comparison_id = text(body.get("comparison_id"), "채굴 비교 ID", 128)
        batch = integer(body.get("batch", 1), "노드별 계산 횟수", 1, MAX_BATCH)
        def action(p):
            try:
                comparison = p.network.step_mining_comparison(comparison_id, batch)
            except ValueError as error:
                raise OperationError("COMPARISON_CONFLICT", str(error), 409) from error
            return comparison_response(p, comparison)
        return change(body, action)

    @app.post("/api/mining/comparison/<command>")
    def comparison_control(command):
        if command not in ("pause", "resume", "stop"):
            raise OperationError("NOT_FOUND", "없는 채굴 비교 명령입니다.", 404)
        body = json_body()
        comparison_id = text(body.get("comparison_id"), "채굴 비교 ID", 128)
        def action(p):
            try:
                comparison = p.network.control_mining_comparison(comparison_id, command)
            except ValueError as error:
                raise OperationError("COMPARISON_CONFLICT", str(error), 409) from error
            return comparison_response(p, comparison)
        return change(body, action)

    @app.get("/api/nodes/<nid>/chain")
    def chain(nid):
        def inspect(p):
            bc = node(p, nid).blockchain
            valid, results = bc.validate_chain()
            blocks = [dict(b.to_dict(), checks=r["checks"], reasons=r["reasons"], valid=r["valid"]) for b, r in zip(bc.chain, results)]
            return {"valid": valid, "height": bc.tip.index, "blocks": blocks}
        return read(inspect)

    def tamper_fields(p, nid, body):
        n = node(p, nid)
        bi = integer(body.get("block_index"), "블록 번호")
        ti = integer(body.get("tx_index"), "거래 번호")
        if bi >= len(n.blockchain.chain) or ti >= len(n.blockchain.chain[bi].transactions):
            raise OperationError("INVALID_INDEX", "블록 또는 거래 번호가 범위를 벗어났습니다.")
        field_name = body.get("field")
        if field_name == "amount":
            value = integer(body.get("value"), "변조 금액", -MAX_INTEGER)
        elif field_name == "receiver_address":
            value = text(body.get("value"), "변조 주소", 128, blank=True)
        else:
            raise OperationError("INVALID_FIELD", "금액(amount) 또는 수신 주소(receiver_address)를 선택하세요.")
        mode = body.get("mode", "simple")
        if mode not in ("simple", "rehash", "remine"):
            raise OperationError("INVALID_MODE", "변조 모드를 확인하세요.")
        return n, bi, ti, field_name, value, mode

    @app.post("/api/nodes/<nid>/tamper")
    def tamper(nid):
        body = json_body()
        def action(p):
            no_mining(p)
            n, bi, ti, field_name, value, mode = tamper_fields(p, nid, body)
            if mode == "remine":
                p.tamper_job = TamperJob(nid, bi, ti, field_name, value,
                                        [b.copy() for b in n.blockchain.chain], chain_fingerprint(n.blockchain.chain))
                return {"ok": True, **p.tamper_job.progress()}, 202
            started = time.monotonic()
            tx = n.blockchain.chain[bi].transactions[ti]
            setattr(tx, field_name, value)
            if mode == "rehash":
                block = n.blockchain.chain[bi]
                block.merkle_root = block.calculate_merkle_root()
                block.hash = block.calculate_hash()
            n.invalidate_validation_cache()
            n.log("tamper", f"[실험] {n.name} 블록#{bi} 거래#{ti}의 {field_name} 변조 (모드: {mode})")
            valid, results = n.blockchain.validate_chain()
            return {"ok": True, "status": "completed", "valid": valid, "results": results,
                    "attempts": 0, "seconds": round(time.monotonic() - started, 3)}
        return change(body, action)

    def tamper_job(p, nid, body):
        node(p, nid)
        job_id = text(body.get("job_id"), "재채굴 실험 ID", 128)
        job = p.tamper_job
        if job is None or job.job_id != job_id or job.node_id != nid:
            raise OperationError("STALE_TAMPER_JOB", "교체되거나 취소된 재채굴 실험입니다.", 409)
        if chain_fingerprint(p.network.nodes[nid].blockchain.chain) != job.original_fingerprint:
            raise OperationError("STALE_TAMPER_CHAIN", "원본 체인이 변경돼 실험을 적용할 수 없습니다.", 409)
        return job

    @app.post("/api/nodes/<nid>/tamper/step")
    def tamper_step(nid):
        body = json_body()
        batch = integer(body.get("batch", 1), "계산 횟수", 1, MAX_BATCH)
        def action(p):
            job = tamper_job(p, nid, body)
            complete = job.step(batch)
            if not complete:
                return {"ok": True, **job.progress()}
            n = node(p, nid)
            n.blockchain.chain = job.chain
            n.invalidate_validation_cache()
            job.status = "completed"
            n.log("tamper", f"[실험] {n.name} 블록#{job.block_index} 이후 PoW 재수행 완료 (원본 서명은 유지)")
            valid, results = n.blockchain.validate_chain()
            result = {"ok": True, **job.progress(), "valid": valid, "results": results,
                      "seconds": round(time.monotonic() - job.started, 3)}
            p.tamper_job = None
            return result
        return change(body, action, allow_tamper=True)

    @app.post("/api/nodes/<nid>/tamper/cancel")
    def tamper_cancel(nid):
        body = json_body()
        def action(p):
            job = tamper_job(p, nid, body)
            job.status = "cancelled"
            result = {"ok": True, **job.progress()}
            p.tamper_job = None
            return result
        return change(body, action, allow_tamper=True)

    @app.post("/api/nodes/<nid>/sync")
    def sync(nid):
        body = json_body(empty=True)
        def action(p):
            n = node(p, nid)
            if not n.online:
                raise OperationError("NODE_OFFLINE", "오프라인 노드는 동기화할 수 없습니다.", 409)
            return {"ok": True, "changed": n.resolve_conflicts()}
        return change(body, action)

    @app.post("/api/nodes/<nid>/online")
    def online(nid):
        body = json_body()
        enabled = body.get("online")
        if type(enabled) is not bool:
            raise OperationError("INVALID_REQUEST", "online 값은 true 또는 false여야 합니다.")
        def action(p):
            n = node(p, nid)
            n.online = enabled
            n.log("sys", f"{n.name} {'온라인' if enabled else '오프라인 (네트워크 분리)'}")
            return {"ok": True, "online": enabled}
        return change(body, action)

    @app.post("/api/difficulty")
    def difficulty():
        body = json_body()
        value = integer(body.get("difficulty"), "난이도", MIN_DIFFICULTY, MAX_DIFFICULTY)
        def action(p):
            policy = p.network.set_difficulty(value)
            return {"ok": True, **policy}
        return change(body, action)

    @app.get("/api/events")
    def events():
        raw = request.args.get("since", "0")
        if not re.fullmatch(r"[0-9]{1,16}", raw):
            raise OperationError("INVALID_REQUEST", "이벤트 순번을 확인하세요.")
        since = integer(int(raw), "이벤트 순번")
        return read(lambda p: [e for e in p.network.events if e["seq"] > since])

    @app.post("/api/reset")
    def reset():
        body = json_body(empty=True)
        def action(p):
            p.network = Network()
            p.tamper_job = None
            p.recovery_notice = ""
            p.job_cancelled_on_restore = False
            return {"ok": True, **state_data(p)}
        return change(body, action, allow_tamper=True)

    @app.post("/api/practices/new")
    def new_practice():
        body = json_body(empty=True)
        expected_practice = body.get("expected_practice_id")
        if expected_practice is not None:
            text(expected_practice, "실습 ID", 128)
        if expected_practice is not None and expected_practice != g.practice.id:
            raise OperationError("PRACTICE_CHANGED", "다른 탭에서 선택한 실습이 변경됐습니다. 현재 실습을 다시 확인하세요.", 409)
        expected = body.get("expected_revision")
        if expected is not None:
            integer(expected, "실습 버전")
        with g.practice.lock:
            if expected is not None and expected != g.practice.revision:
                raise OperationError("STALE_REVISION", "현재 실습이 변경됐습니다. 상태를 다시 확인하세요.", 409)
            g.practice = manager.create()
            return response({"ok": True, **state_data(g.practice)})

    if app.config["EDUCATION_ENABLED"]:
        from education_api import register_education
        legacy_database = app.config["DATABASE"]
        education_database = (app.config.get("EDUCATION_DATABASE")
                              or os.environ.get("TEAM_EDUCATION_DB")
                              or (":memory:" if legacy_database == ":memory:" else
                                  str(Path(legacy_database).parent / "education.sqlite3")))
        if not app.config["TEAM_DATABASE_URL"] and legacy_database != ":memory:" and education_database != ":memory:":
            legacy_path = Path(legacy_database).resolve()
            education_path = Path(education_database).resolve()
            same_file = (legacy_path == education_path or
                         (legacy_path.exists() and education_path.exists()
                          and os.path.samefile(legacy_path, education_path)))
            if same_file:
                raise ValueError("기초 실습 DB와 통합 실습 DB는 서로 다른 파일이어야 합니다")
        try:
            register_education(app, education_database)
        except Exception:
            runtime = app.extensions.get("education")
            if runtime is not None:
                runtime.close()
            store.close()
            raise

    backend = app.extensions.get("postgres_backend")
    try:
        # Keep the prior deployment's writer lease if link migration fails.
        # Claiming and migrating commit together, fencing old writes only once ready.
        with backend.transaction(write=True) if backend is not None else nullcontext():
            if backend is not None:
                backend.activate()
            shared_code = app.config["TEAM_SHARED_ROOM_CODE"].strip()
            if shared_code and "education" in app.extensions:
                if not app.extensions["education"].team_rooms.migrate_existing(shared_code):
                    raise RuntimeError("Configured shared room has no saved checkpoint")
    except Exception:
        runtime = app.extensions.get("education")
        if runtime is not None:
            runtime.close()
        store.close()
        raise

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
