"""Final finite native candidate: larger balanced batches, no finite CPU tail.

Reuses the diagnostic harness unchanged. The two exact checked transforms are
recorded with their generated-source hashes. Production scheduling/CPU code is
unchanged. Only the experimental allocator avoids new CPU work in the final
two GPU batches, allowing the last already-submitted CPU task to finish.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import sys
import types

from . import bottleneck_benchmark as harness
from . import hybrid


def transformed_module(name, path, changes):
    source = path.read_text(encoding="utf-8")
    for before, after in changes:
        if source.count(before) != 1:
            raise RuntimeError("Experimental transform no longer matches exactly once")
        source = source.replace(before, after)
    module = types.ModuleType(name)
    module.__package__, module.__file__ = "hashcash_lab", str(path)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module, hashlib.sha256(source.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=13)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 7:
        parser.error("At least 7 repeats required")
    allocator_changes = [("            if cursor >= stop:\n                return None",
                          "            if cursor >= stop or (gpu is not None and stop - cursor <= 2 * self.gpu_batch):\n"
                          "                return None")]
    harness_changes = [("0, 10_000, 16_777_216", "0, 10_000, 67_108_864"),
                       ("workers, 50_000, 16_777_216", "workers, 250_000, 67_108_864")]
    # The native_thread line is a separate identical occurrence. Transform the
    # whole shared dictionary block once, rather than silently replacing many.
    text = Path(harness.__file__).read_text(encoding="utf-8")
    before = text[text.index('        variants = {"gpu_only": (hybrid, 0, 10_000, 16_777_216)}'):
                  text.index('    maximum = max(row[1] for row in variants.values())')]
    after = before.replace("16_777_216", "67_108_864").replace("50_000", "250_000")
    harness_changes = [(before, after)]
    scheduler, scheduler_hash = transformed_module("hashcash_lab._native_tail_guard", Path(hybrid.__file__),
                                                    allocator_changes)
    experiment, experiment_hash = transformed_module("hashcash_lab._native_large_batches", Path(harness.__file__),
                                                      harness_changes)
    experiment.hybrid = scheduler
    # Real importable worker functions, rather than dynamic module functions,
    # are necessary on Windows spawn. Their sources remain in the base harness.
    experiment._warm_native_worker = harness._warm_native_worker
    experiment._warm_native_thread = harness._warm_native_thread
    result = experiment.compare("native_threads", args.repeats, 1_073_741_824)
    result["stage"] = "native_amortized_tail_guard"
    result["experimental_transforms"] = {"allocator": allocator_changes, "harness": harness_changes,
                                          "generated_scheduler_sha256": scheduler_hash,
                                          "generated_harness_sha256": experiment_hash,
                                          "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({n: {k: v[k] for k in ("median_hashes_per_second", "speedup_vs_gpu_only", "pairs_faster")}
                      for n, v in result["measurements"].items()}, indent=2))


if __name__ == "__main__":
    mp.freeze_support()
    main()
