"""Independent optional CUDA SHA-256 Hashcash backend.

No CuPy/NumPy import happens until an instance is created. The coordinator
falls back to CPU on initialization, compilation, self-test, or scan failure.
CUDA execution, boundary tests, and fixed-work performance were verified on
an RTX 5070 Laptop GPU with CuPy 14.2 and its CUDA 12.9 runtime wheels. See
docs/hashcash-report.md for the environment and measured scope.

The kernel specializes the fixed message, decimal nonce length, and target.
Each thread hashes a contiguous range. Decimal division happens once at the
start of that range; subsequent trials increment ASCII bytes inside packed
input words. SHA-256 uses sixteen named schedule registers and sixty-four
generated rounds. SHA-256 constants are the standardized FIPS 180-4 values.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

UINT64_MAX = (1 << 64) - 1
MAX_MESSAGE_BYTES = 35

_INITIAL = (
    0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
    0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
)
_ROUND = (
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5,
    0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
    0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
    0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
    0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC,
    0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7,
    0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
    0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
    0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
    0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3,
    0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5,
    0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
    0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
    0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
)


class GpuUnavailable(RuntimeError):
    """A CUDA path could not be initialized or used reliably."""


class GpuVerificationError(GpuUnavailable):
    """CUDA returned a digest that failed an independent hashlib check."""


@dataclass(frozen=True)
class BatchResult:
    start: int
    count: int
    nonce: int | None
    digest: str | None
    seconds: float
    kernel_seconds: float

    @property
    def hashes_per_second(self) -> float:
        return self.count / self.seconds if self.seconds > 0 else 0.0


def _word_layout(message: bytes, nonce_digits: int) -> tuple[list[int], list[tuple[int, int]]]:
    """Return padded constants and the (word, bit shift) of each nonce byte."""
    if not 1 <= nonce_digits <= 20:
        raise ValueError("decimal nonce length must be 1..20")
    total_bytes = len(message) + nonce_digits
    if total_bytes > 55:
        raise ValueError("the optional CUDA path requires a single SHA-256 block")
    block = bytearray(64)
    block[:len(message)] = message
    block[total_bytes] = 0x80
    block[-8:] = (total_bytes * 8).to_bytes(8, "big")
    words = [int.from_bytes(block[i:i + 4], "big") for i in range(0, 64, 4)]
    slots = [((len(message) + i) // 4, 8 * (3 - ((len(message) + i) % 4)))
             for i in range(nonce_digits)]
    return words, slots


def _u32_literal(value: int) -> str:
    return f"0x{value:08x}u"


def _increment_code(slots: list[tuple[int, int]], indent: str = "        ") -> list[str]:
    """Increment the packed ASCII decimal suffix without any division."""
    # This increment also runs after the last trial. The caller never launches
    # a range across a power of ten, so an overflowed suffix is never hashed.
    lines: list[str] = []
    for depth, (word, shift) in enumerate(reversed(slots)):
        padding = indent + "    " * depth
        mask = _u32_literal(0xFF << shift)
        nine = _u32_literal(ord("9") << shift)
        step = _u32_literal(1 << shift)
        reset = _u32_literal(9 << shift)
        lines.append(f"{padding}if ((seed{word} & {mask}) != {nine}) {{")
        lines.append(f"{padding}    seed{word} += {step};")
        lines.append(f"{padding}}} else {{")
        lines.append(f"{padding}    seed{word} -= {reset};")
    for depth in range(len(slots) - 1, -1, -1):
        lines.append(indent + "    " * depth + "}")
    return lines


def _build_cuda(message: bytes, difficulty: int, nonce_digits: int,
                per_thread: int, *, dump: bool = False) -> str:
    if not 1 <= difficulty <= 64:
        raise ValueError("difficulty must be 1..64 leading hexadecimal zero digits")
    if not 1 <= per_thread <= 65536:
        raise ValueError("per_thread must be 1..65536")
    constants, slots = _word_layout(message, nonce_digits)
    mutable = {word for word, _ in slots}
    name = "hashcash_dump" if dump else "hashcash_candidates"
    args = ("unsigned long long first_nonce, unsigned long long count, "
            "unsigned int* digests" if dump else
            "unsigned long long first_nonce, unsigned long long count, "
            "unsigned int* found, unsigned long long* found_nonce, unsigned int* found_digest")
    lines = [
        "// Independently generated SHA-256 kernel. One block per candidate.",
        "#define RR(x,n) (((x) >> (n)) | ((x) << (32u - (n))))",
        "#define B0(x) (RR((x),2u) ^ RR((x),13u) ^ RR((x),22u))",
        "#define B1(x) (RR((x),6u) ^ RR((x),11u) ^ RR((x),25u))",
        "#define S0(x) (RR((x),7u) ^ RR((x),18u) ^ ((x) >> 3u))",
        "#define S1(x) (RR((x),17u) ^ RR((x),19u) ^ ((x) >> 10u))",
        "#define CH(x,y,z) (((x) & (y)) ^ (~(x) & (z)))",
        "#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))",
        f'extern "C" __global__ void {name}({args}) {{',
        "    const unsigned long long thread_number =",
        "        (unsigned long long)blockIdx.x * blockDim.x + threadIdx.x;",
        f"    const unsigned long long offset = thread_number * {per_thread}ull;",
        "    if (offset >= count) return;",
        "    const unsigned long long thread_first = first_nonce + offset;",
        f"    const unsigned int trials = (unsigned int)((count - offset) < {per_thread}ull",
        f"        ? (count - offset) : {per_thread}ull);",
        "    unsigned long long decimal = thread_first;",
    ]
    for word in sorted(mutable):
        lines.append(f"    unsigned int seed{word} = {_u32_literal(constants[word])};")
    for word, shift in reversed(slots):
        lines.append(f"    seed{word} |= (unsigned int)((decimal % 10ull) + 48ull) << {shift}u;")
        lines.append("    decimal /= 10ull;")
    lines += ["    #pragma unroll 1", "    for (unsigned int trial = 0; trial < trials; ++trial) {"]
    for word in range(16):
        initial = f"seed{word}" if word in mutable else _u32_literal(constants[word])
        lines.append(f"        unsigned int w{word} = {initial};")
    state = list("abcdefgh")
    for register, initial in zip(state, _INITIAL):
        lines.append(f"        unsigned int {register} = {_u32_literal(initial)};")
    # Rotate register names in the generator rather than move eight registers
    # in device code. After 64 rounds the names return to their original order.
    for round_number, constant in enumerate(_ROUND):
        slot = round_number % 16
        if round_number >= 16:
            lines.append(f"        w{slot} += S1(w{(round_number - 2) % 16})"
                         f" + w{(round_number - 7) % 16} + S0(w{(round_number - 15) % 16});")
        a, b, c, d, e, f, g, h = state
        lines += [
            "        {",
            f"            const unsigned int t = {h} + B1({e}) + CH({e},{f},{g})"
            f" + {_u32_literal(constant)} + w{slot};",
            f"            {d} += t;",
            f"            {h} = t + B0({a}) + MAJ({a},{b},{c});",
            "        }",
        ]
        state = [h, a, b, c, d, e, f, g]
    hashes = [f"({register} + {_u32_literal(initial)})"
              for register, initial in zip(state, _INITIAL)]
    if dump:
        for i, digest_word in enumerate(hashes):
            lines.append(f"        digests[(offset + trial) * 8ull + {i}ull] = {digest_word};")
    else:
        whole_words, leftover = divmod(difficulty, 8)
        conditions = [f"{hashes[i]} == 0u" for i in range(whole_words)]
        if leftover:
            mask = ((1 << (leftover * 4)) - 1) << (32 - leftover * 4)
            conditions.append(f"({hashes[whole_words]} & {_u32_literal(mask)}) == 0u")
        lines.append("        if (" + " && ".join(conditions) + ") {")
        lines.append("            if (atomicCAS(found, 0u, 1u) == 0u) {")
        lines.append("                *found_nonce = thread_first + trial;")
        for i, digest_word in enumerate(hashes):
            lines.append(f"                found_digest[{i}] = {digest_word};")
        lines += ["            }", "        }"]
    lines.extend(_increment_code(slots))
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n"


def batch_count(start: int, requested: int) -> int:
    """Keep one launch inside one decimal length and the unsigned 64-bit range."""
    if not isinstance(start, int) or isinstance(start, bool) or not 0 <= start <= UINT64_MAX:
        raise ValueError("start must be an integer in the unsigned 64-bit nonce range")
    if not isinstance(requested, int) or isinstance(requested, bool) or not 1 <= requested <= (1 << 32) - 1:
        raise ValueError("requested count must be an integer in 1..2^32-1")
    boundary = 10 ** len(str(start))
    return min(requested, boundary - start, UINT64_MAX - start + 1)


class CudaBackend:
    """CUDA backend with mandatory shader self-test and hashlib result checks.

    Failures raise GpuUnavailable. The application's coordinator should resume
    the last unconfirmed batch on CPU. Cancellation is checked between launches;
    this class does not skip part of a submitted batch.
    """

    def __init__(self, message: str, difficulty: int, *, threads: int = 256,
                 per_thread: int = 16, device_id: int = 0):
        self.message = message.encode("utf-8")
        if len(self.message) > MAX_MESSAGE_BYTES:
            raise GpuUnavailable("CUDA 경로는 UTF-8 메시지 35바이트까지 지원합니다. CPU로 계산합니다.")
        if not 1 <= difficulty <= 64:
            raise ValueError("difficulty must be 1..64")
        if threads not in (64, 128, 256):
            raise ValueError("threads must be 64, 128, or 256")
        if not 1 <= per_thread <= 65536:
            raise ValueError("per_thread must be 1..65536")
        self.difficulty = difficulty
        self.threads = threads
        self.per_thread = per_thread
        self._kernels: dict[tuple[bytes, int, int, bool], Any] = {}
        self._verified = False
        try:
            import cupy as cp
            import numpy as np
            if cp.cuda.runtime.getDeviceCount() <= device_id:
                raise GpuUnavailable("사용할 CUDA 장치를 찾지 못했습니다.")
            self.cp = cp
            self._device = cp.cuda.Device(device_id)
            with self._device:
                self._stream = cp.cuda.Stream(non_blocking=True)
                # Keep the existing CUDA argument types, but share one aligned
                # allocation so each completed batch needs one host readback.
                # Word 1 is padding; the uint64 nonce begins at byte offset 8.
                self._result = cp.zeros(12, dtype=cp.uint32)
                self._found = self._result[:1]
                self._nonce = self._result[2:4].view(cp.uint64)
                self._digest = self._result[4:12]
                self._pinned_result = cp.cuda.alloc_pinned_memory(self._result.nbytes)
                self._host_result = np.frombuffer(self._pinned_result, dtype=np.uint32, count=12)
                # A backend already owns mutable result buffers and processes
                # one launch at a time. Its event pair can be reused as well.
                self._begin_event, self._end_event = cp.cuda.Event(), cp.cuda.Event()
                props = cp.cuda.runtime.getDeviceProperties(device_id)
                label = props.get("name", "CUDA GPU")
                self.name = label.decode("utf-8", errors="replace") if isinstance(label, bytes) else str(label)
            self.self_test()
        except GpuUnavailable:
            raise
        except Exception as exc:
            raise GpuUnavailable(f"CUDA 초기화/자체 검증 실패: {exc}") from exc

    @property
    def self_test_passed(self) -> bool:
        return self._verified

    def _kernel(self, digits: int, *, dump: bool = False, message: bytes | None = None,
                difficulty: int | None = None) -> Any:
        payload = self.message if message is None else message
        threshold = self.difficulty if difficulty is None else difficulty
        key = (payload, threshold, digits, dump)
        if key not in self._kernels:
            source = _build_cuda(payload, threshold, digits, self.per_thread, dump=dump)
            self._kernels[key] = self.cp.RawKernel(source, "hashcash_dump" if dump else "hashcash_candidates", options=())
        return self._kernels[key]

    def _dump_range(self, start: int, count: int, *, message: bytes | None = None) -> list[str]:
        cp = self.cp
        digests: list[str] = []
        current = start
        remaining = count
        while remaining:
            length = batch_count(current, remaining)
            with self._device, self._stream:
                output = cp.empty(length * 8, dtype=cp.uint32)
                kernel = self._kernel(len(str(current)), dump=True, message=message)
                blocks = (length + self.threads * self.per_thread - 1) // (self.threads * self.per_thread)
                kernel((blocks,), (self.threads,), (cp.uint64(current), cp.uint64(length), output))
                self._stream.synchronize()
                words = cp.asnumpy(output).reshape((length, 8))
                digests.extend("".join(f"{int(word):08x}" for word in row) for row in words)
            current += length
            remaining -= length
        return digests

    def _test_search_predicate(self, difficulty: int) -> None:
        """Execute the real search path against positive/negative CPU vectors."""
        cp = self.cp
        payload = b"CUDA proof self-test"
        start, count = 1000, 512
        references = {
            nonce: hashlib.sha256(payload + str(nonce).encode("ascii")).hexdigest()
            for nonce in range(start, start + count)
        }
        matches = {nonce: digest for nonce, digest in references.items()
                   if digest.startswith("0" * difficulty)}
        if (difficulty == 1 and not matches) or (difficulty == 64 and matches):
            raise GpuVerificationError("CUDA 탐색 자체 검증 기준값이 올바르지 않습니다.")
        with self._device, self._stream:
            found = cp.zeros(1, dtype=cp.uint32)
            found_nonce = cp.zeros(1, dtype=cp.uint64)
            found_digest = cp.zeros(8, dtype=cp.uint32)
            kernel = self._kernel(4, message=payload, difficulty=difficulty)
            blocks = (count + self.threads * self.per_thread - 1) // (self.threads * self.per_thread)
            kernel((blocks,), (self.threads,),
                   (cp.uint64(start), cp.uint64(count), found, found_nonce, found_digest))
            self._stream.synchronize()
            flag = int(cp.asnumpy(found)[0])
            if flag not in (0, 1) or bool(flag) != bool(matches):
                raise GpuVerificationError(f"CUDA 난이도 {difficulty} 탐색 판정 자체 검증 실패")
            if flag:
                nonce = int(cp.asnumpy(found_nonce)[0])
                digest = "".join(f"{int(word):08x}" for word in cp.asnumpy(found_digest))
                if nonce not in matches or digest != matches[nonce]:
                    raise GpuVerificationError("CUDA 발견 nonce/해시 전달 자체 검증 실패")

    def self_test(self) -> None:
        self._verified = False
        # These spans exercise 0, ASCII carries, decimal-length transitions,
        # a non-divisible thread tail, values above u32, and the u64 limit.
        ranges = (
            (0, 37), (90, 21), (9990, 21),
            (1_000_000, self.per_thread + 17),
            ((1 << 32) - 6, 19), (UINT64_MAX - 16, 17),
        )
        for start, count in ranges:
            actual = self._dump_range(start, count)
            for offset, digest in enumerate(actual):
                nonce = start + offset
                expected = hashlib.sha256(self.message + str(nonce).encode("ascii")).hexdigest()
                if digest != expected:
                    raise GpuVerificationError(f"CUDA SHA-256 자체 검증 실패: nonce={nonce}")
        # The previous source's message buffer could overrun above 32 bytes.
        # Verify all three affected byte lengths, including multibyte UTF-8,
        # at the longest supported suffix and one-block padding boundary.
        for payload in (b"x" * 33, b"x" * 34, ("가" * 11 + "ab").encode("utf-8")):
            actual = self._dump_range(UINT64_MAX, 1, message=payload)[0]
            expected = hashlib.sha256(payload + str(UINT64_MAX).encode("ascii")).hexdigest()
            if actual != expected:
                raise GpuVerificationError(f"CUDA SHA-256 {len(payload)}바이트 자체 검증 실패")
        # Compile the search entry point as well as the digest-test entry point
        # before advertising the backend as available.
        with self._device:
            self._kernel(1).compile()
            self._kernel(20).compile()
        # Validate target decisions, the atomic winner, and digest transfer by
        # executing both a known positive and a known negative search span.
        self._test_search_predicate(1)
        self._test_search_predicate(64)
        self._verified = True

    def _search_batch(self, start: int, requested: int) -> BatchResult:
        if not self._verified:
            raise GpuUnavailable("CUDA SHA-256 자체 검증을 통과하지 못했습니다.")
        count = batch_count(start, requested)
        cp = self.cp
        try:
            with self._device, self._stream:
                kernel = self._kernel(len(str(start)))
                # Compile before the timer; report initialization separately.
                kernel.compile()
                blocks = (count + self.threads * self.per_thread - 1) // (self.threads * self.per_thread)
                begin, end = self._begin_event, self._end_event
                started = time.perf_counter()
                self._found.fill(0)
                begin.record(self._stream)
                kernel((blocks,), (self.threads,),
                       (cp.uint64(start), cp.uint64(count), self._found, self._nonce, self._digest))
                end.record(self._stream)
                # The blocking copy on the same stream waits for the recorded
                # end event as well. A preceding event synchronization would
                # add a second host/driver round trip to every small launch.
                self._result.get(out=self._host_result, stream=self._stream, blocking=True)
                host = self._host_result
                flag = int(host[0])
                if flag not in (0, 1):
                    raise GpuVerificationError("CUDA 발견 플래그가 올바르지 않습니다.")
                found = bool(flag)
                nonce = int(host[2]) | (int(host[3]) << 32) if found else None
                digest = "".join(f"{int(word):08x}" for word in host[4:12]) if found else None
                seconds = time.perf_counter() - started
                kernel_seconds = cp.cuda.get_elapsed_time(begin, end) / 1000
            if found:
                assert nonce is not None and digest is not None
                reference = hashlib.sha256(self.message + str(nonce).encode("ascii")).hexdigest()
                if not start <= nonce < start + count or digest != reference or not digest.startswith("0" * self.difficulty):
                    raise GpuVerificationError("CUDA 발견 결과가 독립 SHA-256 검증에 실패했습니다.")
            return BatchResult(start, count, nonce, digest, seconds, kernel_seconds)
        except GpuUnavailable:
            raise
        except Exception as exc:
            raise GpuUnavailable(f"CUDA 탐색 실패: {exc}") from exc

    def scan_batch(self, start: int, count: int) -> dict[str, Any]:
        """Hash one bounded launch and report its fully completed range.

        `checked` may be less than the requested count at a decimal-length or
        uint64 boundary. Advance by checked, and retry this start on CPU if the
        method raises. A parallel CUDA winner need not be the minimum nonce.
        """
        batch = self._search_batch(start, count)
        last_nonce = start + batch.count - 1
        last_hash = hashlib.sha256(self.message + str(last_nonce).encode("ascii")).hexdigest()
        return {
            "checked": batch.count,
            "nonce": batch.nonce,
            "hash": batch.digest,
            "last_nonce": last_nonce,
            "last_hash": last_hash,
            "seconds": batch.seconds,
            "kernel_seconds": batch.kernel_seconds,
        }
