"""Server-owned, exclusive leases of verified CUDA resources.

Importing this module does not load CUDA. Each cache has a fixed device/kernel
configuration through its factory; keys preserve exact UTF-8 bytes and target.
Only idle resources are evicted. CuPy may separately memoize compiled modules.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import threading


@dataclass
class _Entry:
    message: str
    difficulty: int
    backend: object
    runner: object = None
    runner_key: tuple | None = None

    def close(self):
        error = None
        try:
            if self.runner is not None:
                self.runner.close()
        except Exception as exc:
            error = exc
        finally:
            self.runner = None
            self.runner_key = None
        try:
            if hasattr(self.backend, "close"):
                self.backend.close()
        except Exception as exc:
            error = error or exc
        if error is not None:
            raise error


class GpuLease:
    def __init__(self, entry, reused):
        self._entry = entry
        self.backend = entry.backend
        self.reused = reused
        self._invalid = False

    def invalidate(self):
        # Can also be signalled by the native observer. Disposal waits for the
        # owning job to leave the lease, after its C call and observer drain.
        self._invalid = True

    def native_runner(self, factory, *, workers, cpu_batch, gpu_batch, trace_capacity):
        entry = self._entry
        key = (factory, workers, cpu_batch, gpu_batch, trace_capacity)
        if entry.runner is not None and entry.runner_key == key:
            return entry.runner, True
        try:
            if entry.runner is not None:
                old, entry.runner = entry.runner, None
                entry.runner_key = None
                old.close()
            runner = factory(entry.message, entry.difficulty, workers=workers,
                             cpu_batch=cpu_batch, gpu_batch=gpu_batch,
                             trace_capacity=trace_capacity)
            try:
                runner.attach_gpu(entry.backend)
            except Exception:
                runner.close()
                raise
            entry.runner, entry.runner_key = runner, key
            return runner, False
        except Exception:
            self.invalidate()
            raise


class GpuReuseCache:
    """A small LRU with one active owner across all keys and modes."""

    def __init__(self, factory, capacity=2):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("GPU cache capacity must be a positive integer")
        self.factory, self.capacity = factory, capacity
        self._entries = OrderedDict()
        self._condition = threading.Condition()
        self._active = False
        self._closing = self._closed = False

    @contextmanager
    def acquire(self, message, difficulty):
        key = (message.encode("utf-8"), difficulty)
        with self._condition:
            while self._active and not self._closing:
                self._condition.wait()
            if self._closing:
                raise RuntimeError("GPU resource cache is closed")
            self._active = True
        entry = lease = None
        try:
            entry = self._entries.pop(key, None)
            reused = entry is not None
            if entry is None:
                entry = _Entry(message, difficulty, self.factory(message, difficulty))
            self._entries[key] = entry
            lease = GpuLease(entry, reused)
            if hasattr(entry.backend, "reset_search_metrics"):
                entry.backend.reset_search_metrics()
            while len(self._entries) > self.capacity:
                _, expired = self._entries.popitem(last=False)
                expired.close()
            yield lease
        except BaseException:
            if lease is not None:
                lease.invalidate()
            raise
        finally:
            try:
                if lease is not None and lease._invalid:
                    self._entries.pop(key, None)
                    entry.close()
            finally:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()

    def close(self):
        with self._condition:
            if self._closing:
                while not self._closed:
                    self._condition.wait()
                return
            self._closing = True
            self._condition.notify_all()
            while self._active:
                self._condition.wait()
            entries = list(self._entries.values())
            self._entries.clear()
        error = None
        try:
            for entry in entries:
                try:
                    entry.close()
                except Exception as exc:
                    error = error or exc
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()
        if error is not None:
            raise error
