"""Exercise the actual Flask factory with both engines and built React assets."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.app = create_app({
            "TESTING": True,
            "EDUCATION_ENABLED": True,
            "DATABASE": str(self.root / "basic.sqlite3"),
            "EDUCATION_DATABASE": str(self.root / "education.sqlite3"),
            "TEAM_TEACHER_SECRET": "factory-test-teacher",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["education"].close()
        self.app.extensions["practice_store"].close()

    def test_primary_ui_and_basic_ui_use_one_flask_server(self):
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"/education-assets/", response.data)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with self.client.get("/basic") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'id="demo-video"', response.data)
            self.assertIn(b'href="/"', response.data)
        self.assertEqual(self.client.get("/api/education/health").json["version"], "3.0.0")
        self.assertFalse((self.root / "basic.sqlite3").exists())

    def test_unknown_api_and_assets_do_not_allocate_a_basic_practice(self):
        self.assertEqual(self.client.get("/api/not-a-real-route").status_code, 404)
        self.assertEqual(self.client.get("/education-assets/../../app.py").status_code, 404)
        self.assertFalse((self.root / "basic.sqlite3").exists())

    def test_cross_origin_mutations_are_rejected_in_both_engines(self):
        for route, payload in [
            ("/api/reset", {}),
            ("/api/education/labs", {"mode": "example", "difficulty": 1}),
        ]:
            response = self.client.post(route, json=payload, headers={"Origin": "https://unrelated.example"})
            self.assertEqual(response.status_code, 403)
        self.assertFalse((self.root / "basic.sqlite3").exists())

    def test_basic_state_and_utxo_state_do_not_share_their_ledgers(self):
        basic = self.client.get("/api/state").json
        created = self.client.post("/api/education/labs", json={"mode": "example", "difficulty": 1})
        self.assertEqual(created.status_code, 201)
        unchanged = self.client.get("/api/state").json
        self.assertEqual(basic["practice"]["id"], unchanged["practice"]["id"])
        self.assertTrue(all(node["height"] == 0 for node in unchanged["nodes"]))
        self.assertTrue((self.root / "basic.sqlite3").is_file())
        self.assertTrue((self.root / "education.sqlite3").is_file())

    def test_database_path_collision_is_rejected_before_modifying_old_file(self):
        path = self.root / "collision.sqlite3"
        path.write_bytes(b"existing data that must remain unchanged")
        with self.assertRaisesRegex(ValueError, "서로 다른 파일"):
            create_app({"TESTING": True, "EDUCATION_ENABLED": True,
                        "DATABASE": str(path), "EDUCATION_DATABASE": str(path)})
        self.assertEqual(path.read_bytes(), b"existing data that must remain unchanged")

    def test_hosted_public_url_and_https_proxy_preserve_same_origin_actions(self):
        with patch.dict("os.environ", {"TEAM_TRUST_PROXY": "1", "TEAM_PUBLIC_URL": "",
                                       "RENDER_EXTERNAL_URL": "https://team.example"}):
            hosted = create_app({
                "TESTING": True, "EDUCATION_ENABLED": True,
                "DATABASE": str(self.root / "hosted-basic.sqlite3"),
                "EDUCATION_DATABASE": str(self.root / "hosted-education.sqlite3"),
                "TEAM_TEACHER_SECRET": "hosted-test-teacher",
            })
        self.addCleanup(hosted.extensions["practice_store"].close)
        self.addCleanup(hosted.extensions["education"].close)
        client = hosted.test_client()
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "team.example",
                   "Origin": "https://team.example"}
        self.assertEqual(hosted.config["TEAM_PUBLIC_URL"], "https://team.example")
        created = client.post("/api/education/labs", json={"mode": "example", "difficulty": 1},
                              headers=headers)
        self.assertEqual(created.status_code, 201)
        self.assertIn("Secure", created.headers["Set-Cookie"])
        self.assertEqual(client.post("/api/reset", json={}, headers=headers).status_code, 200)
        headers["Origin"] = "https://unrelated.example"
        self.assertEqual(client.post("/api/reset", json={}, headers=headers).status_code, 403)

    def test_basic_limits_preserve_reads_and_reset(self):
        self.app.config["BASIC_MAX_PENDING"] = 0
        limited = self.client.post("/api/wallets", json={"name": "too many"})
        self.assertEqual(limited.status_code, 409)
        self.assertEqual(limited.json["code"], "MEMPOOL_LIMIT")
        self.app.config["BASIC_MAX_HEIGHT"] = 0
        self.assertEqual(self.client.post("/api/nodes/A/mine/start", json={}).json["code"], "CHAIN_LIMIT")
        self.assertEqual(self.client.get("/api/state").status_code, 200)
        self.assertEqual(self.client.post("/api/reset", json={}).status_code, 200)
