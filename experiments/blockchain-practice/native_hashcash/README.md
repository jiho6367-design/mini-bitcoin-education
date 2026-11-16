# Blockchain Practice · optional local Python and C engines

Ported from `codex/hashcash-only`, commit `c7f5aa72075310479eb954b4224a03cd0c0fa76a`

The standard-library CPU scanner, CuPy CUDA backend, disjoint hybrid coordinator, C scheduler wrapper and four C/header source files retain the original SHA-256 and uint64 validation rules

`hashcash_lab/search.py` contains the original SearchConfig/SearchJob controller without the standalone HTTP server or server-side history writer The Blockchain Practice bridge owns isolated leased browser jobs Results remain in the existing browser history; neither learning ledger is modified

The optional API is disabled by default and always disabled on Render Native use requires `TEAM_NATIVE_HASHCASH=1`, an actual loopback connection and a literal loopback Host CUDA imports and C library loading occur only when a user starts native work, never on import or capability inspection

GPU dependencies are in `requirements-gpu.txt` C mode additionally needs the optional Windows compiler/OpenSSL build described by the team README No package, compiler, DLL or GPU runtime is installed automatically at server startup

Optional build from `experiments/blockchain-practice/`:

```powershell
.\.venv\Scripts\python.exe -m native_hashcash.hashcash_lab.native_scheduler_experiment --build --compiler <gcc.exe> --openssl-root <ucrt64>
```

Build outputs and metadata stay under `native_hashcash/work/native_scheduler/`, ignored by the repository The source/DLL hashes must match the build metadata before a native library is loaded No binaries, private data or old standalone server were copied
