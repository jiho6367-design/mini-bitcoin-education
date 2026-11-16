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

    def test_primary_ui_is_basic_and_previous_basic_bookmark_remains_an_alias(self):
        with self.client.get("/") as main, self.client.get("/basic") as basic:
            self.assertEqual(main.status_code, 200)
            self.assertEqual(basic.status_code, 200)
            self.assertEqual(main.data, basic.data)
            self.assertIn(b'id="practice-overview-title"', main.data)
            self.assertNotIn(b'id="demo-video"', main.data)
            self.assertNotIn(b"/education-assets/", main.data)
            self.assertIn('href="/" aria-current="page">기초 실습'.encode(), main.data)
            self.assertIn('href="/?classroom=join">공동 실습'.encode(), main.data)
            self.assertNotIn('>통합 실습</a>'.encode(), main.data)
            self.assertEqual(main.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/api/education/health").json["version"], "3.0.0")
        self.assertFalse((self.root / "basic.sqlite3").exists())

    def test_hidden_advanced_and_existing_classroom_queries_keep_the_built_ui(self):
        owner = None
        for path in ("/advanced", "/?classroom=join&room=V5JHT522",
                     "/?classroom=teacher", "/?classroom=team"):
            with self.subTest(path=path), self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"/education-assets/", response.data)
                self.assertNotIn(b'id="demo-video"', response.data)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                token = self.client.get_cookie("team_education_owner").value
                owner = owner or token
                self.assertEqual(token, owner)
        self.assertFalse((self.root / "basic.sqlite3").exists())

    def test_basic_main_still_loads_when_the_react_build_is_missing(self):
        self.app.config["EDUCATION_FRONTEND"] = str(self.root / "missing-build")
        for path in ("/", "/basic"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'id="practice-overview-title"', response.data)
                self.assertNotIn(b'id="demo-video"', response.data)
        for path in ("/advanced", "/?classroom=join&room=V5JHT522"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 503)
        self.assertFalse((self.root / "basic.sqlite3").exists())

    def test_mode_routes_preserve_both_existing_ledgers_and_shared_room_code(self):
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)
        basic = self.client.get("/api/state").json
        wallet = self.client.post("/api/wallets", json={"name": "보존할 기초 지갑"}).json
        lab = self.client.post("/api/education/labs", json={"mode": "example", "difficulty": 1}).json["lab"]
        renamed = self.client.post(f"/api/education/practices/{lab['storage']['id']}/rename",
                                   json={"title": "보존할 이전 실습"})
        self.assertEqual(renamed.status_code, 200)
        room = self.client.post("/api/education/team/rooms", json={"name": "보존할 팀원"}).json
        headers = {"Authorization": "Bearer " + room["token"]}
        room_path = f"/api/education/classroom/rooms/{room['code']}"
        before_room = self.client.get(room_path, headers=headers).json
        owner = self.client.get_cookie("team_education_owner").value
        for path in ("/basic", "/advanced", f"/?classroom=join&room={room['code']}", "/"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
        after = self.client.get("/api/state").json
        self.assertEqual(after["practice"]["id"], basic["practice"]["id"])
        self.assertEqual(after["revision"], wallet["revision"])
        wallets = self.client.get("/api/wallets").json
        self.assertEqual(next(item["address"] for item in wallets if item["name"] == "보존할 기초 지갑"),
                         wallet["address"])
        preserved = self.client.get(f"/api/education/labs/{lab['labId']}").json["lab"]
        self.assertEqual(preserved["nodes"], lab["nodes"])
        archive = self.client.get("/api/education/practices").json["practices"]
        self.assertTrue(any(item["id"] == lab["storage"]["id"] and item["title"] == "보존할 이전 실습"
                            for item in archive))
        self.assertEqual(self.client.get(room_path, headers=headers).json["lab"], before_room["lab"])
        self.assertEqual(self.client.get_cookie("team_education_owner").value, owner)

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
