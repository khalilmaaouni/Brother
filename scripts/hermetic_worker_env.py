"""Test-owned disk premise and disposable worker coordination state.

The site customization follows PYTHONPATH into Python descendants. Product
admission still runs normally; no product module knows about this fixture.
"""
import contextlib
import os
import shutil
import tempfile
from unittest import mock


_DISK_SOURCE = '''import shutil
_real_disk_usage = shutil.disk_usage

def _disk_usage(path):
    usage = _real_disk_usage(path)
    free = 500 * 1024 ** 3
    return type(usage)(usage.used + free, usage.used, free)
'''


@contextlib.contextmanager
def worker_environment():
    """Yield a child environment and restore the current process on exit."""
    with tempfile.TemporaryDirectory(prefix="worker-premise-") as root:
        with open(os.path.join(root, "sitecustomize.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(_DISK_SOURCE + "\nshutil.disk_usage = _disk_usage\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"]
                                    if env.get("PYTHONPATH") else "")
        env["BROTHER_LOAD_REFUSALS_LOG"] = os.path.join(root, "refusals.jsonl")
        env["BROTHER_MACHINE_RESERVATION_PATH"] = os.path.join(root, "reservation.json")
        namespace = {}
        exec(_DISK_SOURCE, namespace)
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(shutil, "disk_usage", namespace["_disk_usage"]):
            yield env
