"""Move this app's browser identity between two explicitly configured HTTPS sites.

The old-site application deliberately imports no database or education engine.
Cookie capabilities travel only inside a short-lived encrypted POST, never a URL.
The primary must run one worker: the nonce ledger is deliberately process-local.
"""
from dataclasses import dataclass
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import unquote, urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from flask import Blueprint, Flask, Response, jsonify, redirect, request


TRANSFER_TTL = 180
MAX_STORAGE_BYTES = 512 * 1024
MAX_FORM_BYTES = 3 * MAX_STORAGE_BYTES + 65536
MAX_NONCES = 4096
NONCE_COOKIE = "site_transfer_nonce"
COMPLETE_COOKIE = "site_transfer_complete"
APP_COOKIES = ("mini_bitcoin_practice", "team_education_owner")
LOCAL_KEYS = frozenset(("team-blockchain-theme", "blockchain-lab-theme",
                        "team-hashcash-history-v1", "team-hashcash-name-v1"))
SESSION_KEYS = frozenset(("team-blockchain-classroom",))
GUIDE_KEY = re.compile(r"team-practice-guide-target:[A-HJ-NP-Z2-9]{8}:[A-Za-z0-9_-]{1,128}\Z")
STATE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
COOKIE_PATTERNS = {
    "mini_bitcoin_practice": re.compile(r"[A-Za-z0-9_-]{32,128}\Z"),
    "team_education_owner": re.compile(r"[A-Za-z0-9_-]{43}\.[0-9a-f]{64}\Z"),
}
HTML_ENTRIES = frozenset(("/", "/basic", "/advanced", "/index.html",
                          "/hashcash", "/hashcash/", "/architecture"))


class TransferError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _origin(value):
    if not isinstance(value, str):
        raise ValueError("An HTTPS origin is required")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or any(c.isspace() for c in value) or "\\" in value):
        raise ValueError("An HTTPS origin is required")
    # Accessing .port also rejects malformed ports.
    port = parsed.port
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return "https://" + host + (f":{port}" if port and port != 443 else "")


def _safe_next(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise TransferError("이동할 실습 주소가 올바르지 않습니다")
    decoded = value
    for _ in range(3):
        try:
            parsed = urlsplit(decoded)
        except ValueError:
            raise TransferError("이동할 실습 주소가 올바르지 않습니다") from None
        if (not decoded.startswith("/") or decoded.startswith("//")
                or parsed.scheme or parsed.netloc or "\\" in decoded
                or any(ord(c) < 32 or ord(c) == 127 or c in "\u2028\u2029" for c in decoded)
                or parsed.path.startswith("/_site-transfer")):
            raise TransferError("현재 사이트의 실습 주소로만 이동할 수 있습니다")
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return value
        decoded = next_decoded
    if unquote(decoded) != decoded:
        raise TransferError("이동할 실습 주소가 올바르지 않습니다")
    return value


def _script_json(value):
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _cookies():
    return {name: value for name in APP_COOKIES
            if (value := request.cookies.get(name)) and COOKIE_PATTERNS[name].fullmatch(value)}


def _storage(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_STORAGE_BYTES:
        raise TransferError("이전할 브라우저 기록은 512KiB 이하여야 합니다", 413)
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError):
        raise TransferError("브라우저 기록 형식을 확인하세요") from None
    if not isinstance(parsed, dict) or set(parsed) != {"local", "session"}:
        raise TransferError("브라우저 기록 형식을 확인하세요")
    for kind, allowed in (("local", LOCAL_KEYS), ("session", SESSION_KEYS)):
        items = parsed[kind]
        if not isinstance(items, dict) or len(items) > 1024:
            raise TransferError("브라우저 기록 형식을 확인하세요")
        for key, value in items.items():
            if (not isinstance(key, str) or not isinstance(value, str)
                    or not (key in allowed or (kind == "local" and GUIDE_KEY.fullmatch(key)))):
                raise TransferError("이 앱의 허용된 브라우저 기록만 이전할 수 있습니다")
    return parsed


@dataclass(frozen=True)
class Settings:
    old: str
    new: str
    cipher: Fernet


class Nonces:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def _prune(self):
        now = time.monotonic()
        self.items = {key: expiry for key, expiry in self.items.items() if expiry > now}

    def issue(self):
        with self.lock:
            self._prune()
            if len(self.items) >= MAX_NONCES:
                raise TransferError("접속 정보 이전을 준비 중입니다 잠시 후 다시 접속하세요", 503)
            state = secrets.token_urlsafe(32)
            self.items[state] = time.monotonic() + TRANSFER_TTL
            return state

    def consume(self, state, cookie):
        with self.lock:
            self._prune()
            if (not isinstance(state, str) or not STATE.fullmatch(state)
                    or not isinstance(cookie, str) or not hmac.compare_digest(state, cookie)
                    or state not in self.items):
                raise TransferError("이전 요청이 만료되었거나 이미 사용됐습니다 다시 접속하세요", 409)
            del self.items[state]


def _configure(app):
    for name in ("TEAM_SITE_TRANSFER_KEY", "TEAM_SITE_PREVIOUS_ORIGIN", "TEAM_SITE_CANONICAL_ORIGIN"):
        app.config.setdefault(name, os.environ.get(name, ""))


def _settings(app):
    try:
        old = _origin(app.config.get("TEAM_SITE_PREVIOUS_ORIGIN"))
        new = _origin(app.config.get("TEAM_SITE_CANONICAL_ORIGIN"))
        if old == new:
            raise ValueError("The two origins must differ")
        key = app.config.get("TEAM_SITE_TRANSFER_KEY")
        if not isinstance(key, str):
            raise ValueError("An encryption key is required")
        return Settings(old, new, Fernet(key.encode("ascii")))
    except (ValueError, TypeError, UnicodeError):
        raise TransferError("사이트 이전 설정을 준비 중입니다 잠시 후 다시 접속하세요", 503) from None


def _require_host(origin):
    if request.host_url.rstrip("/") != origin:
        raise TransferError("허용된 사이트에서 다시 접속하세요", 403)


def _secure(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Content-Security-Policy",
                                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
    return response


def _html(title, script, *, form="", form_origin=None):
    nonce = secrets.token_urlsafe(24)
    response = Response(
        '<!doctype html><html lang="ko"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{html.escape(title)} · 블록체인 실습실</title></head><body>'
        f'<main><h1>{html.escape(title)}</h1><p id="status" role="status">저장된 기록을 연결하고 있습니다</p>'
        f'{form}<noscript>브라우저 기록을 연결하려면 JavaScript를 켜고 새로고침하세요</noscript></main>'
        f'<script nonce="{nonce}">{script}</script></body></html>', content_type="text/html; charset=utf-8")
    form_action = form_origin or "'none'"
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{nonce}'; base-uri 'none'; frame-ancestors 'none'; "
        f"form-action {form_action}")
    return _secure(response)


def _error(error):
    return _secure(Response(str(error), status=error.status, content_type="text/plain; charset=utf-8"))


def _complete(response):
    response.set_cookie(COMPLETE_COOKIE, "1", max_age=365 * 24 * 60 * 60,
                        secure=True, httponly=True, samesite="Lax", path="/")
    return response


def register_site_transfer(app):
    """Register primary-site handoff routes and an opt-in first-entry redirect."""
    _configure(app)
    nonces = Nonces()
    app.extensions["site_transfer_nonces"] = nonces
    bp = Blueprint("site_transfer", __name__, url_prefix="/_site-transfer")

    @bp.errorhandler(TransferError)
    def transfer_error(error):
        return _error(error)

    @bp.after_request
    def secure_transfer(response):
        return _secure(response)

    @app.before_request
    def first_entry():
        # Empty configuration leaves development, existing tests and API clients unchanged.
        enabled = any(app.config.get(name) for name in
                      ("TEAM_SITE_TRANSFER_KEY", "TEAM_SITE_PREVIOUS_ORIGIN", "TEAM_SITE_CANONICAL_ORIGIN"))
        if (not enabled or request.method not in ("GET", "HEAD") or request.path not in HTML_ENTRIES
                or request.cookies.get(COMPLETE_COOKIE) == "1" or _cookies()):
            return None
        try:
            settings = _settings(app)
            _require_host(settings.new)
            target = _safe_next(request.full_path.rstrip("?"))
            return _secure(redirect("/_site-transfer/begin?" + urlencode({"next": target}), 302))
        except TransferError as error:
            return _error(error)

    @bp.get("/begin")
    def begin():
        settings = _settings(app)
        _require_host(settings.new)
        target = _safe_next(request.args.get("next", "/"))
        if request.cookies.get(COMPLETE_COOKIE) == "1" or _cookies():
            return _complete(_secure(redirect(target, 303)))
        state = nonces.issue()
        response = _secure(redirect(settings.old + "/_site-transfer/export?" +
                                    urlencode({"state": state, "next": target}), 302))
        response.set_cookie(NONCE_COOKIE, state, max_age=TRANSFER_TTL, secure=True,
                            httponly=True, samesite="None", path="/_site-transfer")
        return response

    @bp.post("/accept")
    def accept():
        settings = _settings(app)
        _require_host(settings.new)
        if request.headers.get("Origin") != settings.old:
            raise TransferError("이전 사이트에서 시작한 요청만 받을 수 있습니다", 403)
        request.max_content_length = MAX_FORM_BYTES
        request.max_form_memory_size = MAX_FORM_BYTES
        request.max_form_parts = 8
        if request.mimetype != "application/x-www-form-urlencoded":
            raise TransferError("이전 요청 형식을 확인하세요", 415)
        if any(len(request.form.getlist(name)) != 1 for name in ("token", "state", "storage")):
            raise TransferError("이전 요청 형식을 확인하세요")
        token = request.form["token"]
        state = request.form["state"]
        try:
            if len(token) > 8192:
                raise ValueError("Oversized identity")
            payload = json.loads(settings.cipher.decrypt(token.encode("ascii"), ttl=TRANSFER_TTL))
        except (InvalidToken, ValueError, TypeError, UnicodeError, RecursionError):
            raise TransferError("접속 정보가 만료되었거나 올바르지 않습니다 다시 접속하세요", 400) from None
        if (not isinstance(payload, dict) or type(payload.get("v")) is not int or payload.get("v") != 1
                or payload.get("iss") != settings.old or payload.get("aud") != settings.new
                or payload.get("state") != state):
            raise TransferError("이 사이트의 이전 요청이 아닙니다", 403)
        target = _safe_next(payload.get("next"))
        cookies = payload.get("cookies")
        if (not isinstance(cookies, dict) or set(cookies) - set(APP_COOKIES)
                or any(not isinstance(value, str) or not COOKIE_PATTERNS[name].fullmatch(value)
                       for name, value in cookies.items())):
            raise TransferError("접속 정보 형식을 확인하세요")
        storage = _storage(request.form["storage"])
        if request.cookies.get(COMPLETE_COOKIE) == "1" or _cookies():
            raise TransferError("현재 사이트에서 사용 중인 기록을 보호했습니다 이 화면을 닫고 실습을 이어가세요", 409)
        nonces.consume(state, request.cookies.get(NONCE_COOKIE))
        script = f"""const saved={_script_json(storage)};
let failed=false;
for(const [kind,values] of Object.entries(saved)){{
  try{{const storage=kind==='local'?localStorage:sessionStorage;
    for(const [key,value] of Object.entries(values)){{
      if(storage.getItem(key)===null) storage.setItem(key,value);
    }}
  }}catch{{failed=true;}}
}}
if(failed){{document.getElementById('status').textContent='접속 정보는 연결했습니다 브라우저 저장이 차단되어 일부 화면 기록을 옮기지 못했습니다';
 const link=document.createElement('a');link.href={_script_json(target)};link.textContent='실습 계속하기';document.querySelector('main').append(link);
}}else{{location.replace({_script_json(target)});}}"""
        response = _complete(_html("기록 연결 완료", script))
        for name, value in cookies.items():
            response.set_cookie(name, value, max_age=365 * 24 * 60 * 60,
                                secure=True, httponly=True, samesite="Lax", path="/")
        response.delete_cookie(NONCE_COOKIE, path="/_site-transfer", secure=True,
                               httponly=True, samesite="None")
        return response

    app.register_blueprint(bp)


def create_transfer_app(config=None):
    """Build the old hostname's lightweight redirect/export app, without any DB."""
    app = Flask(__name__, static_folder=None)
    app.config.update(TEAM_SITE_MODE="transfer", MAX_CONTENT_LENGTH=8192)
    if config:
        app.config.update(config)
    _configure(app)
    if os.environ.get("TEAM_TRUST_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.errorhandler(TransferError)
    def transfer_error(error):
        return _error(error)

    @app.after_request
    def secure_transfer(response):
        return _secure(response)

    @app.get("/api/education/health")
    def health():
        return jsonify(status="ok", mode="transfer")

    @app.get("/_site-transfer/export")
    def export():
        settings = _settings(app)
        _require_host(settings.old)
        origin = request.headers.get("Origin")
        if origin and origin != settings.new:
            raise TransferError("새 사이트에서 시작한 요청만 받을 수 있습니다", 403)
        state = request.args.get("state", "")
        if not STATE.fullmatch(state):
            raise TransferError("이전 요청을 새 사이트에서 다시 시작하세요")
        target = _safe_next(request.args.get("next", "/"))
        token = settings.cipher.encrypt(json.dumps({"v": 1, "iss": settings.old, "aud": settings.new,
                                                   "state": state, "next": target, "cookies": _cookies()},
                                                  separators=(",", ":")).encode()).decode("ascii")
        action = settings.new + "/_site-transfer/accept"
        form = (f'<form id="transfer" method="post" action="{html.escape(action, quote=True)}" hidden>'
                f'<input type="hidden" name="token" value="{html.escape(token, quote=True)}">'
                f'<input type="hidden" name="state" value="{html.escape(state, quote=True)}">'
                '<input type="hidden" name="storage" value=""></form>')
        script = f"""const localKeys=new Set({_script_json(sorted(LOCAL_KEYS))});
const sessionKeys=new Set({_script_json(sorted(SESSION_KEYS))});
const guidePattern=new RegExp({_script_json(GUIDE_KEY.pattern.replace(chr(92) + 'Z', '$'))});
const saved={{local:{{}},session:{{}}}};
for(const kind of ['local','session']){{
  try{{const storage=kind==='local'?localStorage:sessionStorage;
    for(let i=0;i<storage.length;i++){{const key=storage.key(i);
      if((kind==='local'&&(localKeys.has(key)||guidePattern.test(key)))||(kind==='session'&&sessionKeys.has(key))){{
        const value=storage.getItem(key);if(value!==null)saved[kind][key]=value;
      }}
    }}
  }}catch{{}}
}}
const serialized=JSON.stringify(saved);
if(new TextEncoder().encode(serialized).length>{MAX_STORAGE_BYTES}){{
 document.getElementById('status').textContent='브라우저 기록이 너무 커 자동 이전하지 못했습니다 기존 기록은 그대로 유지됩니다';
}}else{{document.querySelector('[name=storage]').value=serialized;document.getElementById('transfer').submit();}}"""
        response = _html("새 주소로 기록 연결 중", script, form=form, form_origin=settings.new)
        # no-referrer makes browsers send Origin:null on this cross-site POST.
        # Send only the source origin so accept can enforce its exact-origin check.
        response.headers["Referrer-Policy"] = "origin"
        return response

    @app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def moved_api(path):
        settings = _settings(app)
        _require_host(settings.old)
        message = "사이트 주소가 바뀌었습니다 새로고침하면 저장된 기록을 연결합니다"
        return jsonify(ok=False, code="SITE_MOVED", reason=message,
                       error={"code": "SITE_MOVED", "message": message},
                       url=settings.new), 410

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def moved_page(path):
        settings = _settings(app)
        _require_host(settings.old)
        if path.startswith(("static/", "education-assets/", "_site-transfer/")):
            raise TransferError("사이트 주소가 바뀌었습니다 새로고침해 실습을 이어가세요", 410)
        target = _safe_next(request.full_path.rstrip("?"))
        return redirect(settings.new + "/_site-transfer/begin?" + urlencode({"next": target}), 302)

    return app
