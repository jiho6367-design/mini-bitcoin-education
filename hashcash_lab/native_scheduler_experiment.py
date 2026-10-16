"""Optional C scheduler shared by native experiments and the web's C mode.

The fixed-work loop, CPU worker threads, allocation, and CUDA Driver API calls
run inside one ctypes.CDLL call. Python prepares verified CuPy resources before
that call and independently verifies returned proofs afterwards. There are no
Python callbacks in the measured native CUDA path. ``run_with_callback`` exists
only for small scheduler correctness tests and must not be used for throughput.

Build outputs and compiler/library metadata stay in ignored work/native_scheduler.
No CuPy import, GPU initialization, compilation, or build happens on import.
"""
from __future__ import annotations

import argparse
import ctypes as ct
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any, Callable

from .cpu import MAX_NONCE, validate_difficulty, validate_nonce

ROOT = Path(__file__).resolve().parents[1]
SOURCES = tuple(ROOT / "experiments" / name for name in (
    "native_scheduler.c", "native_cuda_driver.c", "native_scheduler.h",
    "native_cuda_driver.h"))
DEFAULT_DLL = ROOT / "work" / "native_scheduler" / "native_scheduler.dll"
_LIBRARIES: dict[str, tuple[Any, list[Any], str]] = {}


class ScanResult(ct.Structure):
    _fields_ = [("checked", ct.c_uint64), ("nonce", ct.c_uint64),
                ("last_nonce", ct.c_uint64), ("found", ct.c_uint32),
                ("reserved", ct.c_uint32), ("hash", ct.c_ubyte * 32),
                ("last_hash", ct.c_ubyte * 32)]


class Trace(ct.Structure):
    _fields_ = [(name, ct.c_uint64) for name in ("start", "count", "checked")] + [
        (name, ct.c_double) for name in ("begin", "end", "kernel_seconds")] + [
        (name, ct.c_uint32) for name in ("worker", "kind", "status", "found")] + [
        ("nonce", ct.c_uint64), ("hash", ct.c_ubyte * 32),
        ("last_hash", ct.c_ubyte * 32)]


class Stats(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in (
        "abi_version", "status", "running", "cancelled", "found", "gpu_failed",
        "trace_count", "trace_dropped", "workers", "workers_ready", "search", "reserved")] + [
        (name, ct.c_uint64) for name in (
            "checked", "cpu_checked", "gpu_checked", "nonce", "last_nonce",
            "assigned", "range_start", "range_count", "cpu_batches", "gpu_batches", "retries")] + [
        (name, ct.c_double) for name in (
            "begin", "end", "elapsed", "first_proof_seconds", "cpu_seconds",
            "gpu_seconds", "gpu_kernel_seconds", "qpc_frequency")] + [
        ("hash", ct.c_ubyte * 32), ("last_hash", ct.c_ubyte * 32),
        ("error", ct.c_char * 192)]


GpuScan = ct.CFUNCTYPE(ct.c_int, ct.c_void_p, ct.c_uint64, ct.c_uint64,
                      ct.POINTER(ScanResult), ct.POINTER(ct.c_double))


class NativeRunError(RuntimeError):
    def __init__(self, message: str, stats: dict):
        super().__init__(message)
        self.stats = stats


def selected_dll(dll_path=None) -> Path:
    return Path(dll_path or os.environ.get("HASHCASH_SCHEDULER_DLL") or DEFAULT_DLL).resolve()


def _openssl_root(value=None) -> Path:
    if value or os.environ.get("HASHCASH_OPENSSL_ROOT"):
        return Path(value or os.environ["HASHCASH_OPENSSL_ROOT"]).resolve()
    compiler = shutil.which("gcc")
    if not compiler:
        raise RuntimeError("Pass --openssl-root or set HASHCASH_OPENSSL_ROOT")
    return Path(compiler).resolve().parent.parent


def _source_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)).replace("\\", "/"):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in SOURCES}


def build_native(*, compiler=None, openssl_root=None, dll_path=None) -> dict:
    """Build the optional native path without installing packages."""
    compiler = str(Path(compiler or shutil.which("gcc") or "gcc").resolve())
    openssl_root = _openssl_root(openssl_root)
    if not (openssl_root / "include" / "openssl" / "sha.h").is_file():
        raise RuntimeError("OpenSSL headers not found under the selected root")
    source_hashes = _source_hashes()
    destination = selected_dll(dll_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [compiler, "-std=c11", "-O3", "-shared", "-static-libgcc", "-Wall", "-Wextra",
               "-Wno-deprecated-declarations", "-I", str(openssl_root / "include"),
               *(str(path) for path in SOURCES if path.suffix == ".c"),
               "-L", str(openssl_root / "lib"), "-lcrypto", "-lkernel32", "-o", str(destination)]
    compiled = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
    if source_hashes != _source_hashes():
        raise RuntimeError("Native source changed during compilation; rebuild before measuring")
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Experimental C allocation, worker/result delivery and CUDA host loop",
        "command": command, "source_sha256": source_hashes,
        "dll": str(destination), "dll_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "openssl_root": str(openssl_root), "platform": platform.platform(),
        "python": platform.python_version(), "compiler_output": compiled.stdout + compiled.stderr,
        "compiler_version": subprocess.run([compiler, "--version"], capture_output=True,
                                           text=True, check=True, timeout=10).stdout.strip(),
        "openssl_build": subprocess.run([str(openssl_root / "bin" / "openssl.exe"), "version", "-a"],
                                        capture_output=True, text=True, check=True, timeout=10).stdout.strip(),
        "limitations": ["Windows native threads and CUDA Driver API experiment",
                        "CuPy compilation/self-test and Python post-validation excluded from native-loop timing",
                        "Finite nonzero uint64 count; full 2^64 domain requires multiple runs"],
    }
    metadata_path = destination.with_suffix(".build.json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**metadata, "metadata_path": str(metadata_path)}


def _library(dll_path=None):
    path = selected_dll(dll_path)
    if not path.is_file():
        raise RuntimeError(f"Native scheduler not built: {path}")
    metadata_path = path.with_suffix(".build.json")
    if not metadata_path.is_file():
        raise RuntimeError("Native scheduler build metadata missing; rebuild before measuring")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    dll_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if metadata.get("source_sha256") != _source_hashes() or metadata.get("dll_sha256") != dll_hash:
        raise RuntimeError("Native source/DLL differs from build metadata; rebuild before measuring")
    key = str(path)
    if key in _LIBRARIES:
        if _LIBRARIES[key][2] != dll_hash:
            raise RuntimeError("Native DLL changed after loading; use a fresh Python process")
        return _LIBRARIES[key][0]
    directories = []
    if os.name == "nt":
        directories.append(os.add_dll_directory(str(path.parent)))
        directories.append(os.add_dll_directory(str(Path(metadata["openssl_root"]) / "bin")))
    library = ct.CDLL(str(path))
    signatures = {
        "hc_scheduler_abi": ([], ct.c_uint32),
        "hc_scheduler_openssl_version": ([], ct.c_char_p),
        "hc_scheduler_cpu_info": ([], ct.c_char_p),
        "hc_create": ([ct.c_char_p, ct.c_size_t, ct.c_uint32, ct.c_uint32,
                       ct.c_uint64, ct.c_uint64, ct.c_uint32], ct.c_void_p),
        "hc_reset": ([ct.c_void_p, ct.c_uint64, ct.c_uint64, ct.c_uint32, ct.c_uint64], ct.c_int),
        "hc_run": ([ct.c_void_p, GpuScan, ct.c_void_p], ct.c_int),
        "hc_cancel": ([ct.c_void_p], None),
        "hc_snapshot": ([ct.c_void_p, ct.POINTER(Stats)], ct.c_int),
        "hc_copy_traces": ([ct.c_void_p, ct.POINTER(Trace), ct.c_uint32], ct.c_uint32),
        "hc_destroy": ([ct.c_void_p], None),
        "nc_create": ([ct.c_void_p, ct.c_void_p, ct.POINTER(ct.c_void_p),
                       ct.c_uint64, ct.c_void_p, ct.c_void_p, ct.c_void_p,
                       ct.c_uint32, ct.c_uint32, ct.c_char_p, ct.c_size_t, ct.c_uint32], ct.c_void_p),
        "nc_scan": ([ct.c_void_p, ct.c_uint64, ct.c_uint64,
                     ct.POINTER(ScanResult), ct.POINTER(ct.c_double)], ct.c_int),
        "nc_destroy": ([ct.c_void_p], None),
        "nc_error_text": ([ct.c_void_p], ct.c_char_p),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    if library.hc_scheduler_abi() != 1 or tuple(map(ct.sizeof, (ScanResult, Trace, Stats))) != (96, 136, 456):
        raise RuntimeError("Native scheduler ABI differs from this wrapper")
    _LIBRARIES[key] = (library, directories, dll_hash)
    return library


def native_environment(dll_path=None) -> dict:
    library = _library(dll_path)
    path = selected_dll(dll_path)
    return {
        "dll": str(path), "dll_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_sha256": _source_hashes(),
        "openssl_version": library.hc_scheduler_openssl_version().decode("utf-8"),
        "openssl_cpu_info": library.hc_scheduler_cpu_info().decode("utf-8"),
        "build": json.loads(path.with_suffix(".build.json").read_text(encoding="utf-8")),
        "ctypes_gil": "CDLL releases the GIL; native CUDA runs without Python callbacks",
        "struct_bytes": {"scan": ct.sizeof(ScanResult), "trace": ct.sizeof(Trace), "stats": ct.sizeof(Stats)},
    }


def _bounded_int(value, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


class NativeScheduler:
    """Own persistent C CPU workers and optional borrowed verified CUDA objects.

    Control reset/attach/run/close from one exclusive owner at a time. Idle
    schedulers may transfer to a later owning thread after observers join.
    cancel() and stats() can be called while an owner's native run is active.
    Schedulers borrowing the
    same backend must run sequentially: its stream, events, result buffer and
    compiled functions must not be reused/compiled concurrently by Python.
    """
    def __init__(self, message: str, difficulty: int, workers: int = 1, *,
                 cpu_batch: int = 100_000, gpu_batch: int = 1 << 26,
                 trace_capacity: int = 65_536, dll_path=None):
        validate_difficulty(difficulty)
        if not isinstance(message, str):
            raise ValueError("Message must be a string")
        try:
            self.payload = message.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("Message contains invalid Unicode") from error
        _bounded_int(workers, 0, 64, "workers")
        _bounded_int(cpu_batch, 1, MAX_NONCE, "cpu_batch")
        _bounded_int(gpu_batch, 1, (1 << 32) - 1, "gpu_batch")
        _bounded_int(trace_capacity, 0, (1 << 32) - 1, "trace_capacity")
        self.message, self.difficulty, self.workers = message, difficulty, workers
        self.cpu_batch, self.gpu_batch = cpu_batch, gpu_batch
        self.trace_capacity = trace_capacity
        self._library = _library(dll_path)
        self._context = self._library.hc_create(self.payload, len(self.payload), difficulty,
                                              workers, cpu_batch, gpu_batch, trace_capacity)
        if not self._context:
            raise RuntimeError("Native scheduler creation failed")
        self._gpu_context = None
        self._gpu_refs: list[Any] = []
        self._running = False
        self._range: tuple[int, int, bool] | None = None

    def _require_open(self):
        if not self._context:
            raise RuntimeError("Native scheduler is closed")

    def attach_gpu(self, backend) -> "NativeScheduler":
        """Prepare all decimal specializations before timing and retain owners."""
        self._require_open()
        if self._running:
            raise RuntimeError("Cannot replace GPU resources while running")
        if not backend.self_test_passed or backend.message != self.payload or backend.difficulty != self.difficulty:
            raise ValueError("GPU backend must have matching input and pass its independent self-test")
        with backend._device:
            prepare = getattr(backend, "prepare_native_kernels", None)
            if prepare is not None:
                functions = tuple(prepare())
            else:
                # Compatibility for independently supplied experimental
                # backends. Production CudaBackend retains these owners and
                # compiles each specialization only once across web jobs.
                functions = []
                for digits in range(1, 21):
                    raw = backend._kernel(digits)
                    raw.compile()
                    functions.append(raw.kernel)
            if len(functions) != 20:
                raise ValueError("Native CUDA needs twenty prepared nonce-length functions")
            handles = (ct.c_void_p * 20)(*(function.ptr for function in functions))
            context = getattr(backend, "_cuda_context", None)
            if not context:
                from cupy_backends.cuda.api import driver
                context = driver.ctxGetCurrent()
            native = self._library.nc_create(context, backend._stream.ptr, handles,
                backend._result.data.ptr, backend._host_result.ctypes.data,
                backend._begin_event.ptr, backend._end_event.ptr,
                backend.threads, backend.per_thread, self.payload, len(self.payload), self.difficulty)
        if not native:
            raise RuntimeError("Native CUDA adapter creation failed")
        if self._gpu_context:
            self._library.nc_destroy(self._gpu_context)
        self._gpu_context = native
        self._gpu_refs = [backend, handles, *functions]
        return self

    def reset(self, start: int, count: int, *, search: bool = False,
              tail_guard: int = 0) -> "NativeScheduler":
        self._require_open()
        validate_nonce(start)
        _bounded_int(count, 1, MAX_NONCE, "count")
        validate_nonce(start + count - 1)
        _bounded_int(tail_guard, 0, MAX_NONCE, "tail_guard")
        if type(search) is not bool:
            raise ValueError("search must be bool")
        if self._running or self._library.hc_reset(self._context, start, count, int(search), tail_guard):
            raise RuntimeError("Native scheduler reset rejected (busy or invalid range)")
        self._range = start, count, search
        return self

    def _execute(self, callback, user) -> dict:
        self._require_open()
        if self._running or self._range is None:
            raise RuntimeError("Reset an idle native scheduler before running")
        self._running = True
        try:
            code = self._library.hc_run(self._context, callback, user)
        finally:
            self._running = False
        result = self.stats()
        self._validate(result, complete=True)
        if code:
            if result["found"]:
                result["cleanup_warning"] = result["error"] or f"Native cleanup status {code}"
            else:
                raise NativeRunError(result["error"] or f"Native run failed with status {code}", result)
        return result

    def run(self, backend=None) -> dict:
        """Run the full C coordinator; optional backend preparation is untimed."""
        if backend is not None:
            self.attach_gpu(backend)
        callback = ct.cast(self._library.nc_scan, GpuScan) if self._gpu_context else GpuScan()
        return self._execute(callback, self._gpu_context)

    def run_with_callback(self, callback: Callable[[int, int], dict | tuple[dict, float]] | None) -> dict:
        """Correctness-only fake GPU hook. Calls Python and is never a speed test."""
        if callback is None:
            result = self._execute(GpuScan(), None)
            result["native_cpu_only"] = True
            return result
        callback_errors = []

        @GpuScan
        def bridge(_user, start, count, output, kernel_seconds):
            try:
                value = callback(start, count)
                batch, duration = value if isinstance(value, tuple) else (value, 0.0)
                item = ScanResult()
                item.checked, item.last_nonce = int(batch["checked"]), int(batch["last_nonce"])
                item.found = int(batch.get("nonce") is not None)
                item.nonce = int(batch["nonce"]) if item.found else 0
                if item.found:
                    item.hash[:] = bytes.fromhex(batch["hash"])
                item.last_hash[:] = bytes.fromhex(batch["last_hash"])
                ct.memmove(output, ct.byref(item), ct.sizeof(item))
                kernel_seconds[0] = float(duration)
                return 0
            except BaseException as error:
                callback_errors.append(f"{type(error).__name__}: {error}")
                return 2

        result = self._execute(bridge, None)
        result["python_callback_correctness_only"] = True
        result["callback_errors"] = callback_errors
        return result

    def cancel(self):
        self._require_open()
        self._library.hc_cancel(self._context)

    def stats(self) -> dict:
        self._require_open()
        native = Stats()
        if self._library.hc_snapshot(self._context, ct.byref(native)):
            raise RuntimeError("Native scheduler snapshot failed")
        result = {name: getattr(native, name) for name, _ in Stats._fields_
                  if name not in ("reserved", "hash", "last_hash", "error")}
        result["nonce"] = str(native.nonce) if native.found else None
        result["last_nonce"] = str(native.last_nonce) if native.checked else None
        result["hash"] = bytes(native.hash).hex() if native.found else None
        result["last_hash"] = bytes(native.last_hash).hex() if native.checked else None
        result["error"] = bytes(native.error).decode("utf-8", errors="replace")
        if self._gpu_context and not self._running:
            error = self._library.nc_error_text(self._gpu_context)
            result["gpu_error"] = error.decode("utf-8", errors="replace") if error else ""
        else:
            result["gpu_error"] = ""
        self._validate(result, complete=False)
        return result

    def _validate(self, result: dict, *, complete: bool):
        if result["abi_version"] != 1 or result["found"] not in (0, 1):
            raise RuntimeError("Native scheduler returned invalid ABI/proof flag")
        if result["checked"] != result["cpu_checked"] + result["gpu_checked"]:
            raise RuntimeError("Native CPU/GPU counters disagree")
        if self._range is None:
            return
        start, count, search = self._range
        if not 0 <= result["checked"] <= count or not 0 <= result["assigned"] <= count:
            raise RuntimeError("Native completed/assigned count exceeds the requested range")
        if result["checked"]:
            last = int(result["last_nonce"])
            if not start <= last < start + count:
                raise RuntimeError("Native endpoint is outside the requested range")
            expected = hashlib.sha256(self.payload + str(last).encode("ascii")).hexdigest()
            if result["last_hash"] != expected:
                raise RuntimeError("Independent native endpoint SHA-256 verification failed")
        if result["found"]:
            nonce = int(result["nonce"])
            expected = hashlib.sha256(self.payload + str(nonce).encode("ascii")).hexdigest()
            if not start <= nonce < start + count or result["hash"] != expected or not expected.startswith("0" * self.difficulty):
                raise RuntimeError("Independent native proof SHA-256 verification failed")
        if complete and not result["status"] and not result["cancelled"] and not search and result["checked"] != count:
            raise RuntimeError("Native fixed-work run omitted candidates")

    def traces(self) -> list[dict]:
        self._require_open()
        capacity = self.stats()["trace_count"]
        if not capacity:
            return []
        output = (Trace * capacity)()
        length = self._library.hc_copy_traces(self._context, output, capacity)
        if length > capacity:
            raise RuntimeError("Native trace copy exceeded its capacity")
        traces = []
        for item in output[:length]:
            result = {name: getattr(item, name) for name, _ in Trace._fields_
                      if name not in ("hash", "last_hash")}
            result["start"] = str(item.start)
            result["nonce"] = str(item.nonce) if item.found else None
            result["hash"] = bytes(item.hash).hex() if item.found else None
            result["last_hash"] = bytes(item.last_hash).hex() if item.checked else None
            traces.append(result)
        return traces

    def close(self):
        if self._running:
            raise RuntimeError("Cancel and join the running caller before closing")
        if self._context:
            self._library.hc_destroy(self._context)
            self._context = None
        if self._gpu_context:
            self._library.nc_destroy(self._gpu_context)
            self._gpu_context = None
        self._gpu_refs = []

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, *_exc):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compiler")
    parser.add_argument("--openssl-root")
    parser.add_argument("--dll")
    args = parser.parse_args()
    if args.dll:
        os.environ["HASHCASH_SCHEDULER_DLL"] = str(selected_dll(args.dll))
    if args.openssl_root:
        os.environ["HASHCASH_OPENSSL_ROOT"] = str(Path(args.openssl_root).resolve())
    if args.build:
        print(json.dumps(build_native(compiler=args.compiler, openssl_root=args.openssl_root,
                                      dll_path=args.dll), ensure_ascii=False, indent=2))
    if args.self_test:
        import unittest
        suite = unittest.defaultTestLoader.loadTestsFromName("hashcash_lab.tests.test_native_scheduler")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
        print(json.dumps(native_environment(args.dll), ensure_ascii=False, indent=2))
    if not args.build and not args.self_test:
        parser.print_help()


if __name__ == "__main__":
    main()
