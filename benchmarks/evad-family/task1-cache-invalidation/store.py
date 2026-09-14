"""A tiny key-value store wrapping a fake backend with an in-memory cache.

The store's cache is a write-through snapshot: load(key) warms it from the
backend, get(key) serves whatever is cached (no backend fallback, so a
missing entry means "nothing cached right now"), and update(key, value)
writes to the backend and then refreshes the snapshot.

BUG: update() evicts the cached snapshot for a key BEFORE attempting the
backend write, not after a successful write. So a write that raises leaves
the entry evicted even though the backend's old value is still perfectly
valid and nothing new was ever written.
"""


class FakeBackend:
    """A simple dict-based fake standing in for a real database."""

    def __init__(self):
        self._data = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value


def make_failing_writer(backend, fail_keys):
    """Return a writer function that raises for any key in fail_keys and
    otherwise behaves exactly like backend.set. Injecting this into Store
    makes a write failure deterministic and reproducible in a test,
    without touching backend or Store internals."""

    def writer(key, value):
        if key in fail_keys:
            raise RuntimeError("simulated backend failure for %r" % (key,))
        backend.set(key, value)

    return writer


class Store:
    def __init__(self, backend=None, writer=None):
        self.backend = backend if backend is not None else FakeBackend()
        # writer defaults to the backend's own set, but can be swapped for
        # a deterministic failing one in tests.
        self._writer = writer if writer is not None else self.backend.set
        self._cache = {}

    def load(self, key):
        """Warm the cache for key from the backend."""
        self._cache[key] = self.backend.get(key)
        return self._cache[key]

    def get(self, key):
        """Return the cached snapshot for key. Raises KeyError if the
        entry was never loaded, or was evicted with nothing to replace
        it -- a cache miss with nothing left to serve."""
        if key not in self._cache:
            raise KeyError(key)
        return self._cache[key]

    def update(self, key, value):
        # BUG: the cache entry is evicted before the write is attempted,
        # not after it succeeds.
        self._cache.pop(key, None)
        self._writer(key, value)
        self._cache[key] = value
