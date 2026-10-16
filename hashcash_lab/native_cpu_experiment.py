"""Optional native CPU experiment; production CPU/search modes do not import it.

Build using the existing MSYS2 UCRT GCC/OpenSSL installation, without pip:

    .\\.venv\\Scripts\\python.exe -m hashcash_lab.native_cpu_experiment --build --self-test

The DLL and build metadata remain in ignored ``work/native_cpu/``. Override the
paths with ``--compiler``, ``--openssl-root``, ``--dll`` or environment variables
HASHCASH_OPENSSL_ROOT and HASHCASH_NATIVE_DLL. Set the latter before spawning
workers so each process loads/caches the same explicitly selected library.

The complete batch runs inside one ctypes.CDLL call (which releases the GIL).
There are no Python callbacks in that loop. OpenSSL's deprecated SHA256_CTX API
is confined to this experiment, with local compiler/library versions recorded.
No throughput measurement is performed by this module's command-line interface.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

from .cpu import MAX_NONCE, validate_difficulty, validate_nonce

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments" / "cpu_openssl.c"
DEFAULT_DLL = ROOT / "work" / "native_cpu" / "cpu_openssl.dll"
_LIBRARIES = {}


class _Result(ctypes.Structure):
    _fields_ = [("checked", ctypes.c_uint64), ("nonce", ctypes.c_uint64),
                ("last_nonce", ctypes.c_uint64), ("found", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32), ("hash", ctypes.c_ubyte * 32),
                ("last_hash", ctypes.c_ubyte * 32)]


def selected_dll(dll_path=None) -> Path:
    return Path(dll_path or os.environ.get("HASHCASH_NATIVE_DLL") or DEFAULT_DLL).resolve()


def _openssl_root(value=None) -> Path:
    if value or os.environ.get("HASHCASH_OPENSSL_ROOT"):
        return Path(value or os.environ["HASHCASH_OPENSSL_ROOT"]).resolve()
    compiler = shutil.which("gcc")
    if not compiler:
        raise RuntimeError("Pass --openssl-root or set HASHCASH_OPENSSL_ROOT")
    return Path(compiler).resolve().parent.parent


def build_native(*, compiler=None, openssl_root=None, dll_path=None) -> dict:
    """Build only this small experimental DLL; record the exact command/source."""
    compiler = str(Path(compiler or shutil.which("gcc") or "gcc").resolve())
    openssl_root = _openssl_root(openssl_root)
    if not (openssl_root / "include" / "openssl" / "sha.h").is_file():
        raise RuntimeError("OpenSSL headers were not found under the selected root")
    destination = selected_dll(dll_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    command = [compiler, "-std=c11", "-O3", "-shared", "-static-libgcc", "-Wall", "-Wextra",
               "-Wno-deprecated-declarations", f'-DHC_SOURCE_SHA256="{source_hash}"',
               "-I", str(openssl_root / "include"), str(SOURCE),
               "-L", str(openssl_root / "lib"), "-lcrypto", "-o", str(destination)]
    compiled = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Experimental native loop; production CPU backend unchanged",
        "command": command, "source": str(SOURCE), "source_sha256": source_hash,
        "dll": str(destination), "dll_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "openssl_root": str(openssl_root), "platform": platform.platform(),
        "python": platform.python_version(), "compiler_output": compiled.stdout + compiled.stderr,
        "compiler_version": subprocess.run([compiler, "--version"], capture_output=True,
                                           text=True, check=True, timeout=10).stdout.strip(),
        "openssl_build": subprocess.run([str(openssl_root / "bin" / "openssl.exe"), "version", "-a"],
                                        capture_output=True, text=True, check=True, timeout=10).stdout.strip(),
        "limitations": ["Uses locally available deprecated OpenSSL 3 low-level SHA256 API",
                        "No Python extension ABI, external package installation, or production selection",
                        "Native batch has a nonzero uint64 count; full 2^64 candidate domain needs multiple batches"],
    }
    metadata_path = destination.with_suffix(".build.json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**metadata, "metadata_path": str(metadata_path)}


def _library(dll_path=None):
    path = selected_dll(dll_path)
    key = str(path)
    if key in _LIBRARIES:
        return _LIBRARIES[key][0]
    if not path.is_file():
        raise RuntimeError(f"Native experiment is not built: {path}")
    directories = []
    if os.name == "nt":
        # Keep handles alive for future loads in this process. The build's root
        # is authoritative, avoiding dependence on the caller's working dir.
        metadata_path = path.with_suffix(".build.json")
        root = None
        if metadata_path.is_file():
            root = json.loads(metadata_path.read_text(encoding="utf-8")).get("openssl_root")
        directories.append(os.add_dll_directory(str(path.parent)))
        directories.append(os.add_dll_directory(str((Path(root) if root else _openssl_root()) / "bin")))
    library = ctypes.CDLL(str(path))
    library.hc_abi_version.argtypes = []
    library.hc_abi_version.restype = ctypes.c_uint
    for name in ("hc_source_sha256", "hc_openssl_version", "hc_cpu_info"):
        function = getattr(library, name)
        function.argtypes = []
        function.restype = ctypes.c_char_p
    if library.hc_abi_version() != 1 or ctypes.sizeof(_Result) != 96:
        raise RuntimeError("Native experiment ABI differs from this Python wrapper")
    if library.hc_source_sha256().decode("ascii") != hashlib.sha256(SOURCE.read_bytes()).hexdigest():
        raise RuntimeError("Native DLL was built from different source; rebuild before measuring")
    library.hc_scan.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint,
                               ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint,
                               ctypes.POINTER(_Result)]
    library.hc_scan.restype = ctypes.c_int
    _LIBRARIES[key] = (library, directories)
    return library


def native_environment(dll_path=None) -> dict:
    library = _library(dll_path)
    path = selected_dll(dll_path)
    metadata_path = path.with_suffix(".build.json")
    return {
        "dll": str(path), "dll_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_sha256": library.hc_source_sha256().decode("ascii"),
        "openssl_version": library.hc_openssl_version().decode("utf-8"),
        "openssl_cpu_info": library.hc_cpu_info().decode("utf-8"),
        "build": json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else None,
        "ctypes_gil": "CDLL call releases the GIL; no callbacks during a batch",
    }


def scan_native_batch(message: str, difficulty: int, start: int, count: int, *,
                      search: bool = True, dll_path=None) -> dict:
    validate_difficulty(difficulty)
    validate_nonce(start)
    if type(count) is not int or not 1 <= count <= MAX_NONCE:
        raise ValueError("Native batch count must be a nonzero uint64 integer")
    validate_nonce(start + count - 1)
    if type(search) is not bool:
        raise ValueError("search must be bool")
    if not isinstance(message, str):
        raise ValueError("Message must be a string")
    try:
        payload = message.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Message contains invalid Unicode") from error
    native = _Result()
    code = _library(dll_path).hc_scan(payload, len(payload), difficulty, start, count,
                                    int(search), ctypes.byref(native))
    if code:
        raise RuntimeError(f"Native OpenSSL scan failed with status {code}")
    batch = {"checked": native.checked, "nonce": native.nonce if native.found else None,
             "hash": bytes(native.hash).hex() if native.found else None,
             "last_nonce": native.last_nonce, "last_hash": bytes(native.last_hash).hex()}
    if not 1 <= batch["checked"] <= count or (batch["checked"] != count and not (search and native.found)):
        raise RuntimeError("Native scan returned an invalid completed count")
    if batch["last_nonce"] != start + batch["checked"] - 1:
        raise RuntimeError("Native scan returned an invalid range endpoint")
    expected_last = hashlib.sha256(payload + str(batch["last_nonce"]).encode("ascii")).hexdigest()
    if batch["last_hash"] != expected_last:
        raise RuntimeError("Independent last hash verification failed")
    if native.found:
        if native.found != 1 or not start <= batch["nonce"] <= batch["last_nonce"]:
            raise RuntimeError("Native scan returned an invalid proof location")
        reference = hashlib.sha256(payload + str(batch["nonce"]).encode("ascii")).hexdigest()
        if batch["hash"] != reference or not reference.startswith("0" * difficulty):
            raise RuntimeError("Independent proof verification failed")
    return batch


def timed_native_scan(message: str, difficulty: int, start: int, count: int, search: bool):
    """Spawn-safe entry point. Warm each worker once before throughput timing."""
    began = time.perf_counter()
    batch = scan_native_batch(message, difficulty, start, count, search=search)
    return batch, began, time.perf_counter()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="Run only the small independent vector suite")
    parser.add_argument("--compiler")
    parser.add_argument("--openssl-root")
    parser.add_argument("--dll")
    args = parser.parse_args()
    if args.dll:
        os.environ["HASHCASH_NATIVE_DLL"] = str(selected_dll(args.dll))
    if args.openssl_root:
        os.environ["HASHCASH_OPENSSL_ROOT"] = str(Path(args.openssl_root).resolve())
    if args.build:
        print(json.dumps(build_native(compiler=args.compiler, openssl_root=args.openssl_root,
                                      dll_path=args.dll), ensure_ascii=False, indent=2))
    if args.self_test:
        import unittest
        suite = unittest.defaultTestLoader.loadTestsFromName("hashcash_lab.tests.test_native_cpu_experiment")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
        print(json.dumps(native_environment(args.dll), ensure_ascii=False, indent=2))
    if not args.build and not args.self_test:
        parser.print_help()


if __name__ == "__main__":
    main()
