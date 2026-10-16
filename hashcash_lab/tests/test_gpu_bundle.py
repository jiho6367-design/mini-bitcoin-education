"""CPU models of staged CUDA compilation and Function ownership.

No test imports CuPy or creates a CUDA context. The fakes intentionally model
RawModule.get_function() returning RawKernel, whose .kernel owns the actual
driver Function; a RawKernel itself does not expose a driver function pointer.
Actual SHA-256 execution remains covered by the opt-in hardware tests.
"""

import hashlib
import re
import unittest
import weakref
from unittest.mock import patch

from hashcash_lab import gpu


class FakeFunction:
    def __init__(self, owner, pointer):
        self.owner = owner
        self.ptr = pointer


class FakeRawKernel:
    def __init__(self, provider, owner, name, *, prepared=False):
        self.provider = provider if isinstance(provider, weakref.ProxyTypes) else weakref.proxy(provider)
        self.owner, self.name = owner, name
        self.compile_calls = 0
        self.calls = []
        self.return_value = object()
        self._function = FakeFunction(owner, provider.pointer()) if prepared else None

    @property
    def kernel(self):
        if self._function is None:
            self.compile()
        return self._function

    def compile(self, log_stream=None):
        self.compile_calls += 1
        # CuPy recompilation can replace the Function. Preserve this behavior
        # in the fake so tests detect an accidentally invalidated C handle.
        self._function = FakeFunction(self.owner, self.provider.pointer())

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.return_value


class FakeRawModule:
    def __init__(self, provider, code, options):
        self.provider = weakref.proxy(provider)
        self.code, self.options = code, options
        self.names = re.findall(r'extern "C" __global__ void (\w+)\(', code)
        self.stage = int(re.search(r"hashcash_stage_(\d)_", self.names[0]).group(1))
        self.compile_calls = 0
        self.lookups = []
        self.compiled = False

    def compile(self):
        self.compile_calls += 1
        if self.provider.fail_compile_stage == self.stage:
            raise RuntimeError("fixture module compile failure")
        self.compiled = True

    def get_function(self, name):
        if not self.compiled:
            raise AssertionError("Function lookup preceded module compilation")
        if name not in self.names:
            raise AssertionError("Requested function is absent from module source")
        self.lookups.append(name)
        if name == self.provider.fail_lookup_name:
            raise RuntimeError("fixture function lookup failure")
        return FakeRawKernel(self.provider, self, name, prepared=True)


class FakeCupy:
    def __init__(self):
        self.modules = []
        self.raw_kernels = []
        self.fail_compile_stage = None
        self.fail_lookup_name = None
        self._next_pointer = 1000

    def pointer(self):
        self._next_pointer += 1
        return self._next_pointer

    def RawModule(self, *, code, options):
        module = FakeRawModule(self, code, options)
        self.modules.append(module)
        return module

    def RawKernel(self, source, name, *, options):
        # Keep lazy source/name inspectable without pretending it is a bundle.
        owner = type("LazyModuleOwner", (), {})()
        owner.source, owner.options = source, options
        raw = FakeRawKernel(self, owner, name)
        self.raw_kernels.append(raw)
        return raw


class FakeDevice:
    def __init__(self):
        self.use_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def use(self):
        self.use_calls += 1


class FakeStream:
    def __init__(self):
        self.synchronizations = 0

    def synchronize(self):
        self.synchronizations += 1


def model_backend(message=" exact 한글🙂\n ", difficulty=8):
    """Build only the Python ownership state, bypassing optional GPU imports."""
    backend = gpu.CudaBackend.__new__(gpu.CudaBackend)
    backend.message, backend.difficulty = message.encode("utf-8"), difficulty
    backend.threads, backend.per_thread = 256, 16
    backend._closed, backend._verified = False, True
    backend._kernels = {}
    backend._bundle_modules, backend._bundle_members = {}, {}
    backend.bundle_preparation_trace = []
    backend._search_functions, backend._native_functions = {}, None
    backend._search_prepared_digits = set()
    backend.search_preparation_seconds = 0.0
    backend.cp, backend._device, backend._stream = FakeCupy(), FakeDevice(), FakeStream()
    backend._found = backend._nonce = backend._digest = backend._result = object()
    backend._host_result = backend._pinned_result = object()
    backend._begin_event = backend._end_event = object()
    backend._cuda_context = 123
    return backend


class GpuBundleTests(unittest.TestCase):
    def test_stage_keys_cover_mandatory_vectors_and_twenty_native_lengths_without_duplicates(self):
        backend = model_backend()
        first, second = backend._stage_keys(1), backend._stage_keys(2)
        self.assertEqual(len(first), 15)
        self.assertEqual(len(second), 18)
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(len(second), len(set(second)))
        self.assertFalse(set(first) & set(second))
        mandatory_dumps = {(backend.message, 8, digits, True)
                           for digits in (1, 2, 3, 4, 5, 7, 10, 20)}
        mandatory_dumps |= {(payload, 8, 20, True) for payload in
                            (b"x" * 33, b"x" * 34, ("가" * 11 + "ab").encode("utf-8"))}
        self.assertEqual({key for key in first if key[3]}, mandatory_dumps)
        self.assertIn((b"CUDA proof self-test", 1, 4, False), first)
        self.assertIn((b"CUDA proof self-test", 64, 4, False), first)
        for message, difficulty in (("x" * 33, 8), ("CUDA proof self-test", 1),
                                    ("CUDA proof self-test", 64)):
            with self.subTest(message=message, difficulty=difficulty):
                special = model_backend(message, difficulty)
                first, second = special._stage_keys(1), special._stage_keys(2)
                self.assertEqual(len(first), len(set(first)))
                self.assertFalse(set(first) & set(second))
                actual = {key for key in first + second if key[:2] == (special.message, difficulty)
                          and not key[3]}
                expected = {(special.message, difficulty, digits, False) for digits in range(1, 21)}
                self.assertEqual(actual, expected)

    def test_bundled_sources_preserve_every_original_body_and_exact_key(self):
        backend = model_backend()
        for stage in (1, 2):
            backend._ensure_bundle(stage)
            module = backend._bundle_modules[stage]
            keys = backend._stage_keys(stage)
            self.assertEqual(len(module.names), len(keys))
            self.assertEqual(len(module.names), len(set(module.names)))
            self.assertEqual(module.code.count("#define RR("), 1)
            bodies = module.code.split('extern "C"')[1:]
            self.assertEqual(len(bodies), len(keys))
            for key, name, body in zip(keys, module.names, bodies):
                payload, difficulty, digits, dump = key
                original = gpu._build_cuda(payload, difficulty, digits, backend.per_thread, dump=dump)
                original_name = "hashcash_dump" if dump else "hashcash_candidates"
                normalized = ('extern "C"' + body).replace(f"void {name}(", f"void {original_name}(", 1)
                self.assertEqual(normalized.strip(), ('extern "C"' + original.split('extern "C"', 1)[1]).strip())
                member = backend._bundle_members[key]
                self.assertEqual(member.name, name)
                self.assertIs(member.module, module)
                self.assertIsInstance(member.raw, FakeRawKernel)
                self.assertIsInstance(member.kernel, FakeFunction)
                self.assertIs(member.kernel, member.raw.kernel)
                self.assertIs(member.kernel.owner, module)

    def test_rawmodule_wrapper_forwards_calls_without_recompiling_rawkernel(self):
        backend = model_backend()
        member = backend._kernel(1)
        function = member.kernel
        member.compile()
        member.compile(log_stream=object())
        args = ((2,), (256,), (11, 19, "buffers"))
        result = member(*args, stream="fixture stream")
        self.assertIs(result, member.raw.return_value)
        self.assertEqual(member.raw.calls, [(args, {"stream": "fixture stream"})])
        self.assertEqual(member.raw.compile_calls, 0)
        self.assertIs(member.kernel, function)
        self.assertFalse(hasattr(member.raw, "ptr"))
        self.assertGreater(member.kernel.ptr, 0)
        self.assertEqual(member.module.compile_calls, 1)

    def test_actual_selftest_control_flow_uses_only_first_bundle_and_can_repeat(self):
        backend = model_backend()

        def dump(start, count, *, message=None):
            payload = backend.message if message is None else message
            current, remaining = start, count
            while remaining:
                checked = gpu.batch_count(current, remaining)
                backend._kernel(len(str(current)), dump=True, message=payload)
                current, remaining = current + checked, remaining - checked
            return [hashlib.sha256(payload + str(nonce).encode("ascii")).hexdigest()
                    for nonce in range(start, start + count)]

        def predicate(difficulty):
            backend._kernel(4, message=b"CUDA proof self-test", difficulty=difficulty)

        with patch.object(backend, "_dump_range", side_effect=dump), \
                patch.object(backend, "_test_search_predicate", side_effect=predicate):
            backend.self_test()
            first_functions = dict(backend._search_functions)
            backend.self_test()
        self.assertTrue(backend.self_test_passed)
        self.assertEqual(set(backend._bundle_modules), {1})
        self.assertEqual(len(backend._bundle_members), 15)
        self.assertEqual(backend._search_functions, first_functions)
        self.assertEqual(set(first_functions), {1, 20})
        self.assertIsNone(backend._native_functions)
        self.assertEqual(backend.cp.raw_kernels, [])
        self.assertEqual(len(backend.cp.modules), 1)
        self.assertEqual(backend.cp.modules[0].compile_calls, 1)

    def test_gpu_only_stays_lazy_and_native_transition_retains_existing_function(self):
        backend = model_backend()
        backend._prepare_search_kernel(1)
        backend._prepare_search_kernel(20)
        raw = backend._prepare_search_kernel(10)
        old_function = raw.kernel
        backend._prepare_search_kernel(10)
        self.assertEqual(set(backend._bundle_modules), {1})
        self.assertEqual(len(backend.cp.raw_kernels), 1)
        self.assertEqual(raw.compile_calls, 1)
        self.assertEqual(old_function.owner.source,
                         gpu._build_cuda(backend.message, backend.difficulty, 10, backend.per_thread))
        functions = backend.prepare_native_kernels()
        self.assertEqual(len(functions), 20)
        self.assertTrue(all(isinstance(function, FakeFunction) for function in functions))
        self.assertIs(functions[9], old_function)
        self.assertIs(backend._kernel(10), raw)
        self.assertEqual(raw.compile_calls, 1)
        self.assertIs(backend.prepare_native_kernels(), functions)
        self.assertEqual([module.compile_calls for module in backend.cp.modules], [1, 1])
        self.assertEqual([trace["entry_count"] for trace in backend.bundle_preparation_trace], [15, 18])

    def test_unprepared_lazy_rawkernel_is_replaced_by_native_bundle(self):
        backend = model_backend()
        backend._kernel(1)
        unused = backend._kernel(12)
        self.assertEqual(unused.compile_calls, 0)
        functions = backend.prepare_native_kernels()
        replacement = backend._kernel(12)
        self.assertIsNot(replacement, unused)
        self.assertIs(functions[11], replacement.kernel)
        self.assertIs(functions[11].owner, backend._bundle_modules[2])
        self.assertEqual(unused.compile_calls, 0)

    def test_failed_first_compile_publishes_no_partial_bundle_and_can_retry(self):
        backend = model_backend()
        backend.cp.fail_compile_stage = 1
        with self.assertRaisesRegex(RuntimeError, "module compile failure"):
            backend._kernel(1)
        self.assertEqual(backend._bundle_modules, {})
        self.assertEqual(backend._bundle_members, {})
        self.assertEqual(backend._kernels, {})
        self.assertEqual(backend.bundle_preparation_trace, [])
        backend.cp.fail_compile_stage = None
        backend._kernel(1)
        backend._kernel(20)
        self.assertEqual(set(backend._bundle_modules), {1})
        self.assertEqual(len(backend.cp.modules), 2)
        self.assertEqual([module.compile_calls for module in backend.cp.modules], [1, 1])
        self.assertEqual(len(backend.bundle_preparation_trace), 1)

    def test_failed_second_lookup_keeps_previous_owners_and_publishes_no_native_tuple(self):
        backend = model_backend()
        backend._prepare_search_kernel(1)
        backend._prepare_search_kernel(20)
        lazy = backend._prepare_search_kernel(10)
        before_members, before_kernels = dict(backend._bundle_members), dict(backend._kernels)
        before_functions = dict(backend._search_functions)
        backend.cp.fail_lookup_name = "hashcash_stage_2_02"
        with self.assertRaisesRegex(RuntimeError, "function lookup failure"):
            backend.prepare_native_kernels()
        self.assertEqual(set(backend._bundle_modules), {1})
        self.assertEqual(backend._bundle_members, before_members)
        self.assertEqual(backend._kernels, before_kernels)
        self.assertEqual(backend._search_functions, before_functions)
        self.assertIsNone(backend._native_functions)
        self.assertEqual(len(backend.bundle_preparation_trace), 1)
        backend.cp.fail_lookup_name = None
        functions = backend.prepare_native_kernels()
        self.assertIs(functions[9], lazy.kernel)
        self.assertEqual([module.stage for module in backend.cp.modules], [1, 2, 2])
        self.assertEqual([module.compile_calls for module in backend.cp.modules], [1, 1, 1])
        self.assertEqual(len(backend.bundle_preparation_trace), 2)

    def test_close_drains_and_releases_module_owners_then_rejects_reuse(self):
        backend = model_backend()
        backend._kernel(1)
        backend.prepare_native_kernels()
        module_refs = [weakref.ref(module) for module in backend.cp.modules]
        # Remove only the fake provider's diagnostic ownership. The backend,
        # wrappers and actual Functions must still own both prepared modules.
        backend.cp.modules.clear()
        self.assertTrue(all(reference() is not None for reference in module_refs))
        stream, device = backend._stream, backend._device
        backend.close()
        self.assertEqual(stream.synchronizations, 1)
        self.assertEqual(device.use_calls, 1)
        self.assertTrue(all(reference() is None for reference in module_refs))
        self.assertEqual(backend._bundle_modules, {})
        self.assertEqual(backend._bundle_members, {})
        self.assertEqual(backend._kernels, {})
        self.assertEqual(backend._search_functions, {})
        self.assertIsNone(backend._native_functions)
        self.assertFalse(backend.self_test_passed)
        backend.close()
        self.assertEqual(stream.synchronizations, 1)
        with self.assertRaises(gpu.GpuUnavailable):
            backend._kernel(1)
        with self.assertRaises(gpu.GpuUnavailable):
            backend.prepare_native_kernels()


if __name__ == "__main__":
    unittest.main()
