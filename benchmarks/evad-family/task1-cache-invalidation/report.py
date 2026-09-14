"""The report path. NOT named in TASK.md's prompt to the competitor.

This is a second, independent caller of store.py: it updates a value and
immediately rereads it through the store's cache, the way a real caller
trusts a cache it just warmed rather than pay for a second backend round
trip. hidden_tests/ exercises this on a SUCCESSFUL write, which a fix that
only stops evicting early (without also refreshing the snapshot after a
real write) would still fail.
"""
from store import Store


def update_and_reread(store: Store, key, value):
    """Write value for key, then return whatever the store now has
    cached for it."""
    store.update(key, value)
    return store.get(key)
