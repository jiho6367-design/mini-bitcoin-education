"""Two-origin handoff tests; no main application or database is constructed."""
import builtins
from html.parser import HTMLParser
import json
import secrets
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.fernet import Fernet
from flask import Flask, jsonify

from site_transfer import (
    APP_COOKIES, COMPLETE_COOKIE, MAX_STORAGE_BYTES, NONCE_COOKIE, TRANSFER_TTL,
    create_transfer_app, register_site_transfer,
)


OLD = "https://old.example"
NEW = "https://new.example"
EMPTY = json.dumps({"local": {}, "session": {}})


class TransferForm(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.action = None
        self.fields = {}
        self.feed(response.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form":
            self.action = values.get("action")
        elif tag == "input":
            self.fields[values["name"]] = values.get("value", "")


class SiteTransferTests(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key()
        self.cipher = Fernet(self.key)
        self.config = {
            "TESTING": True,
            "TEAM_SITE_TRANSFER_KEY": self.key.decode(),
            "TEAM_SITE_PREVIOUS_ORIGIN": OLD,
            "TEAM_SITE_CANONICAL_ORIGIN": NEW,
        }
        self.old = create_transfer_app(self.config)
        self.primary = Flask("transfer-test", static_folder=None)
        self.primary.config.update(self.config, MAX_CONTENT_LENGTH=64 * 1024)
        register_site_transfer(self.primary)
        self.primary.add_url_rule("/", "home", lambda: "<h1>실습실</h1>")
        self.primary.add_url_rule("/basic", "basic", lambda: "<h1>기초</h1>")
        self.primary.add_url_rule("/api/state", "state", lambda: jsonify(untouched=True))
        self.primary.add_url_rule("/static/example.js", "asset", lambda: "/* unchanged */")
        self.primary.add_url_rule("/api/education/health", "health", lambda: jsonify(status="ok"))
        self.source = self.old.test_client()
        self.target = self.primary.test_client()
        self.basic = secrets.token_urlsafe(32)
        self.owner = secrets.token_urlsafe(32) + "." + "a" * 64

    def seed_source(self):
        self.source.set_cookie(APP_COOKIES[0], self.basic, domain="old.example", httponly=True, secure=True)
        self.source.set_cookie(APP_COOKIES[1], self.owner, domain="old.example", httponly=True, secure=True)

    def start(self, target="/?classroom=join&room=V5JHT522"):
        begin = self.target.get("/_site-transfer/begin?" + urlencode({"next": target}), base_url=NEW)
        self.assertEqual(begin.status_code, 302)
        parsed = urlsplit(begin.location)
        self.assertEqual(parsed.scheme + "://" + parsed.netloc, OLD)
        exported = self.source.get(parsed.path + "?" + parsed.query, base_url=OLD)
        self.assertEqual(exported.status_code, 200)
        form = TransferForm(exported)
        self.assertEqual(form.action, NEW + "/_site-transfer/accept")
        form.fields["storage"] = EMPTY
        return begin, exported, form.fields

    def accept(self, fields, origin=OLD):
        headers = {"Origin": origin} if origin is not None else {}
        return self.target.post("/_site-transfer/accept", base_url=NEW, data=fields, headers=headers)

    def secure_headers(self, response, referrer="no-referrer"):
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], referrer)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_copies_exact_identity_and_allowed_storage_only_through_encrypted_post(self):
        self.seed_source()
        begin, exported, fields = self.start()
        nonce = self.target.get_cookie(NONCE_COOKIE, domain="new.example", path="/_site-transfer")
        self.assertTrue(nonce.secure)
        self.assertTrue(nonce.http_only)
        self.assertEqual(nonce.same_site, "None")
        self.assertTrue(nonce.origin_only)
        payload = json.loads(self.cipher.decrypt(fields["token"].encode(), ttl=TRANSFER_TTL))
        self.assertEqual(payload["cookies"], dict(zip(APP_COOKIES, (self.basic, self.owner))))
        storage = {
            "local": {
                "team-blockchain-theme": "dark",
                "blockchain-lab-theme": "light",
                "team-hashcash-history-v1": '[{"id":"proof"}]',
                "team-hashcash-name-v1": "별명",
                "team-practice-guide-target:V5JHT522:some-session-id": "a" * 64,
            },
            "session": {"team-blockchain-classroom": '{"code":"V5JHT522","token":"existing"}'},
        }
        fields["storage"] = json.dumps(storage)
        response = self.accept(fields)
        self.assertEqual(response.status_code, 200)
        for name, value in zip(APP_COOKIES, (self.basic, self.owner)):
            cookie = self.target.get_cookie(name, domain="new.example")
            self.assertEqual(cookie.value, value)
            self.assertTrue(cookie.secure)
            self.assertTrue(cookie.http_only)
            self.assertTrue(cookie.origin_only)
            self.assertEqual(cookie.same_site, "Lax")
        self.assertEqual(self.target.get_cookie(COMPLETE_COOKIE, domain="new.example").value, "1")
        self.assertIsNone(self.target.get_cookie(NONCE_COOKIE, domain="new.example", path="/_site-transfer"))
        body = response.get_data(as_text=True)
        self.assertIn("storage.getItem(key)===null", body)
        self.assertIn("storage.setItem(key,value)", body)
        self.assertIn("location.replace", body)
        for name in storage["local"]:
            self.assertIn(name, body)
        for result in (begin, exported, response):
            self.secure_headers(result, "origin" if result is exported else "no-referrer")
            for capability in (self.basic, self.owner):
                self.assertNotIn(capability, result.get_data(as_text=True))
                self.assertNotIn(capability, result.headers.get("Location", ""))
        self.assertNotIn(fields["token"], exported.headers.get("Location", ""))

    def test_new_visitor_without_old_cookies_gets_only_completion_marker(self):
        _, _, fields = self.start("/basic")
        response = self.accept(fields)
        self.assertEqual(response.status_code, 200)
        for name in APP_COOKIES:
            self.assertIsNone(self.target.get_cookie(name, domain="new.example"))
        self.assertIsNotNone(self.target.get_cookie(COMPLETE_COOKIE, domain="new.example"))
        self.assertEqual(self.target.get("/", base_url=NEW).status_code, 200)

    def test_state_requires_nonce_cookie_and_cannot_be_replayed(self):
        _, _, fields = self.start()
        state = fields["state"]
        self.target.set_cookie(NONCE_COOKIE, secrets.token_urlsafe(32), domain="new.example", path="/_site-transfer")
        self.assertEqual(self.accept(fields).status_code, 409)
        self.target.set_cookie(NONCE_COOKIE, state, domain="new.example", path="/_site-transfer")
        self.assertEqual(self.accept(fields).status_code, 200)
        self.target.delete_cookie(COMPLETE_COOKIE, domain="new.example")
        self.target.set_cookie(NONCE_COOKIE, state, domain="new.example", path="/_site-transfer")
        self.assertEqual(self.accept(fields).status_code, 409)

    def test_nonce_expiry_fails_closed(self):
        _, _, fields = self.start()
        self.primary.extensions["site_transfer_nonces"].items[fields["state"]] = time.monotonic() - 1
        self.assertEqual(self.accept(fields).status_code, 409)

    def test_csrf_and_foreign_host_are_rejected_without_consuming_valid_handoff(self):
        _, _, fields = self.start()
        for origin in (None, "null", "https://evil.example", OLD + ".evil.example", OLD + "/"):
            with self.subTest(origin=origin):
                self.assertEqual(self.accept(fields, origin).status_code, 403)
        response = self.target.post("/_site-transfer/accept", base_url="https://evil.example",
                                    data=fields, headers={"Origin": OLD})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.accept(fields).status_code, 200)

    def test_invalid_expired_and_wrong_audience_tokens_are_rejected(self):
        _, _, original = self.start()
        payload = json.loads(self.cipher.decrypt(original["token"].encode()))
        for patch_values in ({"aud": OLD}, {"iss": NEW}, {"v": 2}, {"v": True},
                             {"state": secrets.token_urlsafe(32)}, {"next": "//evil.example"}):
            with self.subTest(changes=patch_values):
                fields = {**original, "token": self.cipher.encrypt(json.dumps({**payload, **patch_values}).encode()).decode()}
                self.assertIn(self.accept(fields).status_code, (400, 403))
        for token in ("invalid", original["token"][:-4] + "AAAA",
                      self.cipher.encrypt_at_time(json.dumps(payload).encode(), int(time.time()) - TRANSFER_TTL - 1).decode()):
            self.assertEqual(self.accept({**original, "token": token}).status_code, 400)
        self.assertEqual(self.accept(original).status_code, 200)

    def test_open_redirects_and_transfer_loops_are_rejected(self):
        for target in ("https://evil.example", "//evil.example", "/\\evil.example", "/%2f%2fevil.example",
                       "/%252f%252fevil.example", "/%0d%0aLocation:evil", "/_site-transfer/begin", "//[bad"):
            with self.subTest(target=target):
                query = urlencode({"next": target})
                self.assertEqual(self.target.get("/_site-transfer/begin?" + query, base_url=NEW).status_code, 400)
                query = urlencode({"next": target, "state": secrets.token_urlsafe(32)})
                self.assertEqual(self.source.get("/_site-transfer/export?" + query, base_url=OLD).status_code, 400)

    def test_storage_allowlist_checks_both_namespace_and_guide_prefix(self):
        _, _, original = self.start()
        for storage in ({"local": {"another-app-secret": "no"}, "session": {}},
                        {"local": {"team-blockchain-classroom": "wrong area"}, "session": {}},
                        {"local": {}, "session": {"team-hashcash-name-v1": "wrong area"}},
                        {"local": {"team-practice-guide-target:bad:bad": "no"}, "session": {}},
                        {"local": {"team-hashcash-name-v1": {"nested": "no"}}, "session": {}},
                        {"local": {}, "session": {}, "other": {}}, []):
            with self.subTest(storage=storage):
                self.assertEqual(self.accept({**original, "storage": json.dumps(storage)}).status_code, 400)
        self.assertEqual(self.accept(original).status_code, 200)

    def test_storage_limit_counts_utf8_bytes_and_accepts_more_than_normal_app_body_limit(self):
        _, _, fields = self.start()
        too_big = json.dumps({"local": {"team-hashcash-name-v1": "가" * (MAX_STORAGE_BYTES // 3)},
                              "session": {}}, ensure_ascii=False)
        self.assertEqual(self.accept({**fields, "storage": too_big}).status_code, 413)
        acceptable = json.dumps({"local": {"team-hashcash-history-v1": "x" * 100000}, "session": {}})
        self.assertEqual(self.accept({**fields, "storage": acceptable}).status_code, 200)
        self.assertEqual(self.primary.config["MAX_CONTENT_LENGTH"], 64 * 1024)

    def test_restoration_escapes_script_closing_tags_and_unicode_line_separators(self):
        _, _, fields = self.start()
        attack = '</script><script>alert("x")</script>&\u2028\u2029\ud800'
        fields["storage"] = json.dumps({"local": {"team-hashcash-name-v1": attack}, "session": {}})
        response = self.accept(fields)
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(attack, body)
        self.assertEqual(body.count("<script"), 1)
        self.assertIn("\\u003c/script\\u003e", body)
        self.assertIn("\\u2028\\u2029", body)
        self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])

    def test_existing_new_site_marker_and_identity_are_not_overwritten(self):
        self.seed_source()
        self.target.set_cookie(COMPLETE_COOKIE, "1", domain="new.example")
        response = self.target.get("/_site-transfer/begin?next=/basic", base_url=NEW)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/basic")
        self.assertEqual(self.primary.extensions["site_transfer_nonces"].items, {})
        self.target.delete_cookie(COMPLETE_COOKIE, domain="new.example")
        _, _, fields = self.start()
        existing = secrets.token_urlsafe(32)
        self.target.set_cookie(APP_COOKIES[0], existing, domain="new.example")
        self.assertEqual(self.accept(fields).status_code, 409)
        self.assertEqual(self.target.get_cookie(APP_COOKIES[0], domain="new.example").value, existing)
        self.assertEqual(self.target.get("/", base_url=NEW).status_code, 200)

    def test_html_entry_redirect_preserves_relative_path_and_query_but_leaves_api_assets_untouched(self):
        response = self.target.get("/?classroom=join&room=V5JHT522", base_url=NEW)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.location).query)["next"], ["/?classroom=join&room=V5JHT522"])
        self.assertEqual(self.target.get("/api/state", base_url=NEW).json, {"untouched": True})
        self.assertEqual(self.target.get("/api/education/health", base_url=NEW).json, {"status": "ok"})
        self.assertEqual(self.target.get("/static/example.js", base_url=NEW).data, b"/* unchanged */")
        moved = self.source.get("/basic?node=A", base_url=OLD)
        self.assertEqual(moved.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(moved.location).query)["next"], ["/basic?node=A"])

    def test_old_mode_never_constructs_a_database_and_old_api_requests_cannot_write(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"app", "storage", "postgres_backend", "education_api", "operations"}:
                self.fail("Transfer-only mode imported a database-backed application")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            transfer = create_transfer_app({**self.config, "TEAM_DATABASE_URL": "must-not-connect"})
            client = transfer.test_client()
            self.assertEqual(client.get("/api/education/health", base_url=OLD).json, {"status": "ok", "mode": "transfer"})
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                response = client.open("/api/education/classroom/rooms/V5JHT522/commands", method=method, base_url=OLD)
                self.assertEqual(response.status_code, 410)
                self.assertEqual(response.json["error"]["code"], "SITE_MOVED")
            self.assertEqual(client.get("/", base_url=OLD).status_code, 302)
        self.assertNotIn("postgres_backend", transfer.extensions)
        self.assertNotIn("practice_store", transfer.extensions)

    def test_missing_key_or_bad_origin_fails_closed_while_transfer_health_remains_available(self):
        for values in ({"TEAM_SITE_TRANSFER_KEY": ""}, {"TEAM_SITE_TRANSFER_KEY": None},
                       {"TEAM_SITE_TRANSFER_KEY": "bad"},
                       {"TEAM_SITE_PREVIOUS_ORIGIN": "http://old.example"},
                       {"TEAM_SITE_CANONICAL_ORIGIN": OLD},
                       {"TEAM_SITE_CANONICAL_ORIGIN": "https://new.example/path"}):
            with self.subTest(values=values):
                app = create_transfer_app({**self.config, **values})
                client = app.test_client()
                self.assertEqual(client.get("/api/education/health", base_url=OLD).status_code, 200)
                self.assertEqual(client.get("/", base_url=OLD).status_code, 503)
        self.primary.config["TEAM_SITE_TRANSFER_KEY"] = ""
        self.assertEqual(self.target.get("/_site-transfer/begin", base_url=NEW).status_code, 503)


if __name__ == "__main__":
    unittest.main()
