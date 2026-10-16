"""Record one functional timing sample per mode in fresh Python processes.

This is not a statistical speed comparison. Existing CUDA disk caches remain;
the preparation and verified-proof transitions are recorded separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .history import HistoryStore
from .server import SearchConfig, SearchJob


class PhaseJob(SearchJob):
    first_verified_proof_seconds = None

    def accept(self, batch):
        found = super().accept(batch)
        if found and self.first_verified_proof_seconds is None:
            self.first_verified_proof_seconds = time.perf_counter() - self.started
        return found


def sample(mode: str):
    with tempfile.TemporaryDirectory() as directory:
        store = HistoryStore(directory)
        job = PhaseJob(SearchConfig("한글🙂", 3, workers=1, mode=mode,
                                    participant_name="기능 계측"), history=store)
        job.run()
        total = time.perf_counter() - job.started
        state = job.snapshot()
        if state["status"] != "found" or not state["recordId"] or state["saveError"]:
            raise RuntimeError(f"Functional search/save failed: {state}")
        if mode == "gpu" and not state["backend"].startswith("GPU ·"):
            raise RuntimeError("GPU mode unexpectedly fell back")
        if mode == "hybrid" and not state["backend"].startswith("CPU + GPU"):
            raise RuntimeError("Hybrid mode unexpectedly fell back")
        proof = state["result"]
        value = hashlib.sha256(state["message"].encode("utf-8") + proof["nonce"].encode("ascii")).hexdigest()
        if value != proof["hash"] or not value.startswith("000") or store.query()["total"] != 1:
            raise RuntimeError("Independent proof or persistence verification failed")
        return {
            "mode": mode, "backend": state["backend"], "message": state["message"], "difficulty": 3,
            "cpu_workers": 1, "startup_elapsed": state["startupElapsed"],
            "first_verified_proof_seconds": job.first_verified_proof_seconds,
            "calculation_and_cleanup_elapsed": state["elapsed"],
            "total_wall_including_storage_seconds": total,
            "checked": state["checked"], "cpu_checked": state["cpuChecked"], "gpu_checked": state["gpuChecked"],
            "proof": proof, "saved_records": 1, "independent_sha256_verified": True,
            "storage": "TemporaryDirectory; participant database unchanged",
        }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("cpu", "gpu", "hybrid"))
    parser.add_argument("--output", type=Path, default=Path("work/hashcash-functional-timing.json"))
    args = parser.parse_args()
    if args.mode:
        print(json.dumps(sample(args.mode), ensure_ascii=True))
        return
    samples = []
    for mode in ("cpu", "gpu", "hybrid"):
        run = subprocess.run([sys.executable, "-m", "hashcash_lab.functional_benchmark", "--mode", mode],
                             check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
        samples.append(json.loads(run.stdout))
    result = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Sequential fresh Python processes, one sample per mode, same Korean input/target/start nonce, "
                  "one CPU worker. Existing CUDA disk caches retained. PhaseJob instruments the production accept() "
                  "transition; no search algorithm changes. Functional phase timing, not a speedup comparison.",
        "phase_definitions": {
            "startup_elapsed": "Backend initialized and Pool object created; spawned workers may still be starting.",
            "first_verified_proof_seconds": "Production accept() first returned a proof verified by SHA-256.",
            "calculation_and_cleanup_elapsed": "Production frozen elapsed after device/worker cleanup, before storage.",
            "total_wall_including_storage_seconds": "SearchJob.run returned after SQLite commit.",
        },
        "samples": samples,
        "limitations": ["One sample per mode is not a performance distribution or comparative speed guarantee.",
                        "Disk cache state is preexisting, not a cold compiler cache.",
                        "HTTP and browser polling are excluded."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    mp.freeze_support()
    main()
