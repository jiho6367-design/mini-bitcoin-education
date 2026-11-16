"""Basic-ledger mining comparisons use real hashes and isolated temporary DBs."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import COOKIE_NAME, create_app
from miner import build_candidate
from node import Network
from storage import StorageError


def candidate_with_first_miss(*args, **kwargs):
    block = build_candidate(*args, **kwargs)
    while block.calculate_hash().startswith("0" * block.difficulty):
        block.timestamp += .001
    block.hash = block.calculate_hash()
    return block


class BasicMiningComparisonTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = str(Path(self.folder.name) / "basic.sqlite3")
        self.apps = []
        self.app, self.client = self.make_client()

    def tearDown(self):
        for app in self.apps:
            app.extensions["practice_store"].close()
        self.folder.cleanup()

    def make_client(self, cookie=None):
        app = create_app({"TESTING": True, "DATABASE": self.database})
        self.apps.append(app)
        client = app.test_client()
        if cookie:
            client.set_cookie(COOKIE_NAME, cookie)
        return app, client

    def post(self, suffix, body=None, *, client=None, status=200):
        response = (client or self.client).post(suffix, json=body or {})
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def state(self, client=None):
        return (client or self.client).get("/api/state").get_json()

    def start(self):
        with patch("node.build_candidate", side_effect=candidate_with_first_miss):
            return self.post("/api/mining/comparison/start")

    def finish(self, comparison_id):
        for _ in range(100):
            result = self.post("/api/mining/comparison/step", {"comparison_id": comparison_id, "batch": 5000})
            if result["comparison"]["status"] == "found":
                return result
        self.fail("Real difficulty-two comparison did not finish in the test budget")

    def test_each_node_computes_one_real_hash_and_pause_resume_are_grouped(self):
        started = self.start()
        comparison_id = started["comparison"]["comparison_id"]
        parent_hashes = {node["candidate"]["previous_hash"] for node in started["nodes"]}
        self.assertEqual(len(parent_hashes), 1)
        paused = self.post("/api/mining/comparison/pause", {"comparison_id": comparison_id})
        self.assertEqual({node["candidate"]["status"] for node in paused["nodes"]}, {"paused"})
        stepped = self.post("/api/mining/comparison/step", {"comparison_id": comparison_id, "batch": 1})
        self.assertEqual([node["candidate"]["attempts"] for node in stepped["nodes"]], [1, 1, 1])
        self.assertEqual(stepped["comparison"]["rounds"], 1)
        network = self.app.extensions["practice_manager"].practices[stepped["practice"]["id"]].network
        for node in network.nodes.values():
            self.assertEqual(node.candidate_info()["hash"], node.candidate["block"].calculate_hash())
        self.post("/api/mining/comparison/step", {"comparison_id": comparison_id, "batch": 2}, status=409)
        resumed = self.post("/api/mining/comparison/resume", {"comparison_id": comparison_id})
        self.assertEqual({node["candidate"]["status"] for node in resumed["nodes"]}, {"running"})

    def test_first_winner_propagates_one_real_block_and_preserves_completed_summary(self):
        self.post("/api/difficulty", {"difficulty": 2})
        created = self.post("/api/wallets", {"name": "Comparison learner"})
        comparison_id = self.start()["comparison"]["comparison_id"]
        result = self.finish(comparison_id)
        comparison = result["comparison"]
        self.assertIn(comparison["winner"], "ABC")
        self.assertEqual(comparison["index"], 1)
        self.assertEqual(len({node["tip_hash"] for node in result["nodes"]}), 1)
        self.assertTrue(all(node["valid"] and not node["mining"] and node["height"] == 1 for node in result["nodes"]))
        attempts = [candidate["attempts"] for candidate in comparison["candidates"].values()]
        self.assertLessEqual(max(attempts) - min(attempts), 1)
        found = [candidate for candidate in comparison["candidates"].values() if candidate["status"] == "found"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["hash"].startswith("00"))
        chain = self.client.get("/api/nodes/A/chain").get_json()["blocks"]
        self.assertIn(created["address"], [tx["receiver_address"] for tx in chain[-1]["transactions"]])
        balances = {wallet["address"]: wallet["balance"] for wallet in self.client.get("/api/wallets").get_json()}
        self.assertEqual(balances[created["address"]], 50)
        self.post("/api/nodes/A/mine/start")
        self.assertEqual(self.state()["mining_comparison"], comparison)
        self.post("/api/mining/comparison/step", {"comparison_id": comparison_id}, status=409)

    def test_start_rejects_active_offline_and_diverged_nodes_without_reset(self):
        self.post("/api/wallets", {"name": "Keep this wallet"})
        initial = self.state()
        self.post("/api/nodes/B/online", {"online": False})
        rejected = self.post("/api/mining/comparison/start", status=409)
        self.assertEqual(rejected["code"], "COMPARISON_CONFLICT")
        self.assertFalse(any(node["mining"] for node in self.state()["nodes"]))
        self.post("/api/nodes/B/online", {"online": True})
        single = self.post("/api/nodes/A/mine/start")
        self.post("/api/mining/comparison/start", status=409)
        self.post("/api/nodes/A/mine/stop", {"job_id": single["job_id"]})
        self.post("/api/nodes/B/online", {"online": False})
        single = self.post("/api/nodes/A/mine/start")
        for _ in range(100):
            mined = self.post("/api/nodes/A/mine/step", {"job_id": single["job_id"], "batch": 5000})
            if mined["found"]:
                break
        else:
            self.fail("Single-node partition setup did not find a block")
        self.post("/api/nodes/B/online", {"online": True})
        heights = [node["height"] for node in self.state()["nodes"]]
        self.assertEqual(heights, [1, 0, 1])
        self.post("/api/mining/comparison/start", status=409)
        self.assertEqual([node["height"] for node in self.state()["nodes"]], heights)
        self.assertEqual(initial["practice"]["id"], self.state()["practice"]["id"])
        self.assertIn("Keep this wallet", [wallet["name"] for wallet in self.client.get("/api/wallets").get_json()])

    def test_old_comparison_ids_and_revisions_cannot_mutate_a_new_group(self):
        first = self.start()
        first_id = first["comparison"]["comparison_id"]
        self.post("/api/mining/comparison/stop", {"comparison_id": first_id})
        second = self.start()
        self.post("/api/mining/comparison/stop", {"comparison_id": first_id}, status=409)
        self.post("/api/mining/comparison/step", {"comparison_id": second["comparison"]["comparison_id"],
                  "expected_revision": first["revision"], "batch": 1}, status=409)
        self.assertEqual(self.state()["mining_comparison"], second["comparison"])

    def test_restart_keeps_same_practice_and_ledger_but_cancels_unfinished_group(self):
        started = self.start()
        self.post("/api/mining/comparison/pause", {"comparison_id": started["comparison"]["comparison_id"]})
        cookie = self.client.get_cookie(COOKIE_NAME).value
        _, restored_client = self.make_client(cookie)
        restored = self.state(restored_client)
        self.assertEqual(restored["practice"]["id"], started["practice"]["id"])
        self.assertTrue(restored["practice"]["job_cancelled_on_restore"])
        self.assertEqual(restored["mining_comparison"]["status"], "stopped")
        self.assertEqual(restored["mining_comparison"]["reason"], "restore_cancelled")
        self.assertTrue(all(not node["mining"] and node["height"] == 0 for node in restored["nodes"]))
        result = self.post("/api/mining/comparison/step", {"comparison_id": started["comparison"]["comparison_id"]},
                           client=restored_client, status=409)
        self.assertEqual(result["code"], "COMPARISON_CONFLICT")

    def test_backward_compatible_payload_and_running_reload(self):
        network = Network()
        legacy = network.to_dict()
        del legacy["mining_comparison"]
        self.assertIsNone(Network.from_dict(legacy).comparison_info())
        with patch("node.build_candidate", side_effect=candidate_with_first_miss):
            network.start_mining_comparison()
        restored = Network.from_dict(network.to_dict(), restore_jobs=True)
        restored.step_mining_comparison(restored.comparison_info()["comparison_id"], 1)
        self.assertEqual([node.candidate_info()["attempts"] for node in restored.nodes.values()], [1, 1, 1])

    def test_failed_save_rolls_back_all_candidates_and_comparison(self):
        initial = self.state()
        with patch.object(self.app.extensions["practice_store"], "save", side_effect=StorageError("disk unavailable")):
            self.post("/api/mining/comparison/start", status=503)
        restored = self.state()
        self.assertEqual(restored["revision"], initial["revision"])
        self.assertIsNone(restored["mining_comparison"])
        self.assertFalse(any(node["mining"] for node in restored["nodes"]))

    def test_difficulty_change_stops_group_and_old_request_cannot_continue(self):
        started = self.start()
        comparison_id = started["comparison"]["comparison_id"]
        self.post("/api/difficulty", {"difficulty": 3})
        current = self.state()
        self.assertEqual(current["mining_comparison"]["reason"], "difficulty_changed")
        self.assertFalse(any(node["mining"] for node in current["nodes"]))
        self.post("/api/mining/comparison/step", {"comparison_id": comparison_id}, status=409)


if __name__ == "__main__":
    unittest.main()
