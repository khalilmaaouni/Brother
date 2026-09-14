"""Options with nested defaults.

BUG: Options.__init__ shallow-copies DEFAULTS with dict(...), so every
instance's top-level dict is new, but everything nested inside it (the
"settings" dict, and the "tags" list and "limits" dict nested inside
that) is the SAME object shared with DEFAULTS and every other instance.
Mutating a nested structure on one instance leaks into every other one.
"""

DEFAULTS = {
    "verbosity": 1,
    "settings": {
        "tags": ["default"],
        "limits": {"max_retries": 3},
    },
}


class Options:
    def __init__(self):
        # BUG: dict(DEFAULTS) only copies the top level. "settings" (and
        # everything nested inside it) is still the same object as
        # DEFAULTS["settings"].
        self._data = dict(DEFAULTS)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    @property
    def settings(self):
        return self._data["settings"]


def default_options():
    return Options()
