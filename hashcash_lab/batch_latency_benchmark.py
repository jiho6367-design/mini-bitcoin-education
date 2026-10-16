"""Warmed real proof search and batch-boundary latency, separate from throughput."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import time

from .gpu import CudaBackend, batch_count
from .bottleneck_benchmark import hardware_state


def compare(messages=13):
    if messages < 5:
        raise ValueError("At least 5 predetermined messages required")
    samples = []
    for index in range(messages):
        message = f"Batch latency {index:02d} 한글"
        began = time.perf_counter()
        backend = CudaBackend(message, 7)
        backend._kernel(10).compile()
        setup = time.perf_counter() - began
        # Same backend/input/target and a warmed search kernel for all variants.
        for _ in range(8):
            backend.scan_batch(1_000_000_000, 8_388_608)
        variants = {"previous_8M": 8_388_608, "candidate_16M": 16_777_216,
                    "candidate_64M": 67_108_864}
        order = list(variants) if index % 2 == 0 else list(reversed(variants))
        row = {"message": message, "setup_seconds_excluded": setup, "order": order, "results": {}}
        for name in order:
            cursor, stop = 1_000_000_000, 1_000_000_000 + 2_147_483_648
            largest = 0.0
            proof = None
            state_before = hardware_state()
            began = time.perf_counter()
            while cursor < stop:
                take = batch_count(cursor, min(variants[name], stop - cursor))
                first = time.perf_counter()
                batch = backend.scan_batch(cursor, take)
                largest = max(largest, time.perf_counter() - first)
                if batch["checked"] != take:
                    raise RuntimeError("Incomplete search range")
                cursor += take
                if batch["nonce"] is not None:
                    reference = hashlib.sha256(message.encode() + str(batch["nonce"]).encode("ascii")).hexdigest()
                    if reference != batch["hash"] or not reference.startswith("0" * 7):
                        raise RuntimeError("Independent SHA-256 verification failed")
                    proof = {"nonce": str(batch["nonce"]), "hash": reference}
                    break
            row["results"][name] = {"seconds_until_proof_or_exhaustion": time.perf_counter() - began,
                                     "checked": str(cursor - 1_000_000_000), "proof": proof,
                                     "largest_synchronous_batch_seconds": largest,
                                     "hardware_before": state_before, "hardware_after": hardware_state()}
        # Each variant must agree whether the finite set has a proof, even
        # though CUDA atomic winners inside the same batch can differ.
        if len({r["proof"] is not None for r in row["results"].values()}) != 1:
            raise RuntimeError("Search variants disagree about proof presence")
        samples.append(row)
    summary = {}
    for name in ("previous_8M", "candidate_16M", "candidate_64M"):
        rows = [s["results"][name] for s in samples]
        values = [r["seconds_until_proof_or_exhaustion"] for r in rows]
        summary[name] = {"median_seconds": statistics.median(values), "mean_seconds": statistics.mean(values),
                         "found_samples": sum(r["proof"] is not None for r in rows),
                         "faster_pairs": sum(r["seconds_until_proof_or_exhaustion"] <
                                             s["results"]["previous_8M"]["seconds_until_proof_or_exhaustion"]
                                             for s, r in zip(samples, rows)),
                         "maximum_observed_batch_seconds": max(r["largest_synchronous_batch_seconds"] for r in rows)}
    return {"measured_at_utc": datetime.now(timezone.utc).isoformat(), "difficulty": 7,
            "start": "1000000000", "maximum_count": "2147483648", "summary": summary, "samples": samples,
            "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (Path(__file__), Path(__file__).with_name("gpu.py"))},
            "method": "13 predetermined numbered UTF-8 messages; same message and nonce start per paired "
                      "variant. Alternating order. Real search stops after the first completed batch containing "
                      "a proof. No selected lucky proof; all outcomes retained. Startup/compile excluded and "
                      "recorded separately. Largest synchronous batch is an observed bound on polling granularity, "
                      "not a directly measured end-to-end HTTP cancel latency or a hardware-wide worst-case bound."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = compare()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document["summary"], indent=2))


if __name__ == "__main__":
    main()
