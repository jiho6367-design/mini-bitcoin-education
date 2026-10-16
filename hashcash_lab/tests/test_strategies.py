"""Independent exact coverage and SHA checks for finite visitation strategies."""

from hashlib import sha256
import random
import unittest

from hashcash_lab.cpu import MAX_NONCE, scan_batch
from hashcash_lab.strategy_benchmark import (
    NonceLane, STRATEGIES, _stringify_nonce_fields, plan_lanes, scan_lane, validate_partition,
)


class StrategyCoverageTests(unittest.TestCase):
    def test_all_plans_cover_identical_window_once_even_with_remainders(self):
        rng = random.Random(20261016)
        windows = [(0, 1, 1), (0, 4, 4), (7, 13, 4), (2 ** 53 + 1, 29, 7),
                   (MAX_NONCE - 100, 101, 4)]
        windows += [(rng.randrange(1_000_000), rng.randrange(2, 200), 1) for _ in range(20)]
        for start, count, initial_workers in windows:
            for workers in {initial_workers, 1, min(4, count), count}:
                for strategy in STRATEGIES:
                    with self.subTest(start=start, count=count, workers=workers, strategy=strategy):
                        lanes = plan_lanes(strategy, start, count, workers)
                        visits = [nonce for lane in lanes for nonce in lane.nonces()]
                        self.assertEqual(len(visits), len(set(visits)))
                        self.assertEqual(sorted(visits), list(range(start, start + count)))

    def test_four_mixed_workers_match_front_back_middle_proposal(self):
        lanes = plan_lanes("front_back_middle", 100, 16, 4)
        self.assertEqual([list(lane.nonces()) for lane in lanes],
                         [[100, 101, 102, 103], [115, 114, 113, 112],
                          [108, 109, 110, 111], [107, 106, 105, 104]])

    def test_partition_checker_rejects_overlap_and_omission(self):
        invalid = ([NonceLane(0, 3, 1), NonceLane(2, 3, 1)],
                   [NonceLane(0, 2, 1), NonceLane(4, 4, 1)],
                   [NonceLane(0, 3, 2), NonceLane(0, 3, 2)],
                   [NonceLane(0, 3, 3), NonceLane(1, 3, 3)])
        for lanes in invalid:
            with self.subTest(lanes=lanes), self.assertRaises(ValueError):
                validate_partition(lanes, 0, 6)

    def test_domain_end_and_invalid_inputs_are_rejected_without_wrapping(self):
        for args in (("forward", MAX_NONCE, 2, 1), ("reverse", -1, 4, 4),
                     ("forward", 0, 0, 1), ("strided", 0, 2, 3),
                     ("unknown", 0, 4, 4), ("forward", True, 4, 4),
                     ("forward", 0, True, 1), ("forward", 0, 4, True)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                plan_lanes(*args)

    def test_json_records_keep_uint64_nonce_fields_as_decimal_strings(self):
        value = {"start": MAX_NONCE - 2, "count": 3,
                 "workers": [{"nonce": MAX_NONCE, "last_nonce": MAX_NONCE, "hash": "x"}],
                 "empty": {"nonce": None}}
        saved = _stringify_nonce_fields(value)
        self.assertEqual(saved["start"], str(MAX_NONCE - 2))
        self.assertEqual(saved["workers"][0]["nonce"], str(MAX_NONCE))
        self.assertEqual(saved["workers"][0]["last_nonce"], str(MAX_NONCE))
        self.assertEqual(saved["count"], 3)
        self.assertIsNone(saved["empty"]["nonce"])


class StrategyHashTests(unittest.TestCase):
    def test_signed_scans_match_independent_reference_order_and_first_proof(self):
        message = " \ud55c\uae00\U0001f642\n"
        for strategy in STRATEGIES:
            for lane in plan_lanes(strategy, 0, 4_003, 4):
                with self.subTest(strategy=strategy, lane=lane):
                    nonces = list(lane.nonces())
                    hashes = [sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
                              for nonce in nonces]
                    proofs = [(nonce, value) for nonce, value in zip(nonces, hashes)
                              if value.startswith("00")]
                    full = scan_lane(message, 2, lane, search=False)
                    self.assertEqual(full["checked"], len(nonces))
                    self.assertEqual(full["last_nonce"], nonces[-1])
                    self.assertEqual(full["last_hash"], hashes[-1])
                    self.assertEqual(full["nonce"], proofs[0][0] if proofs else None)
                    self.assertEqual(full["hash"], proofs[0][1] if proofs else None)
                    early = scan_lane(message, 2, lane, search=True)
                    expected_checked = nonces.index(proofs[0][0]) + 1 if proofs else len(nonces)
                    self.assertEqual(early["checked"], expected_checked)
                    self.assertEqual(early["nonce"], full["nonce"])
                    self.assertEqual(early["hash"], full["hash"])

    def test_forward_and_strided_scanner_agree_with_production_hot_loop(self):
        for lane in (NonceLane(0, 5_000, 1), NonceLane(3, 5_000, 4),
                     NonceLane(2 ** 53 + 1, 20, 3)):
            for search in (True, False):
                with self.subTest(lane=lane, search=search):
                    self.assertEqual(scan_lane("You are the best of best!!!!", 3, lane, search=search),
                                     scan_batch("You are the best of best!!!!", 3, lane.start,
                                                lane.count, search=search, step=lane.step))

    def test_reverse_does_not_encode_negative_nonce_at_zero(self):
        result = scan_lane("boundary", 64, NonceLane(2, 3, -1))
        self.assertEqual(result["checked"], 3)
        self.assertEqual(result["last_nonce"], 0)
        self.assertEqual(result["last_hash"], sha256(b"boundary0").hexdigest())
        result = scan_lane("boundary", 64, NonceLane(MAX_NONCE, 3, -1))
        expected = sha256(b"boundary" + str(MAX_NONCE - 2).encode("ascii")).hexdigest()
        self.assertEqual(result["last_hash"], expected)

    def test_invalid_signed_scan_inputs_are_rejected(self):
        for lane in (NonceLane(0, 0, 1), NonceLane(0, 1, 0), NonceLane(0, 2, -1),
                     NonceLane(MAX_NONCE, 2, 1), NonceLane(0, 1, True)):
            with self.subTest(lane=lane), self.assertRaises(ValueError):
                scan_lane("message", 3, lane)


if __name__ == "__main__":
    unittest.main()
