#!/usr/bin/env python3
"""Calibration for tools/bm_embed_warm.py, WBS row VB5-06, the warm embedder.

Driven backwards from the row's own done-check: a repeat-cold dense query
measures fast with the warm path and unchanged without it; the daemon
refuses a non-loopback bind without a token file, the same gate
bm_vault_serve.py and bm_vault_pane.py already carry; killing the daemon
mid-suite proves the fallback path, not a crash; warm and subprocess
vectors agree for identical input; the measure subcommand never invents a
number when the model is unavailable.

Model-dependent tests (the daemon actually answering an /embed call) are
skipped, named, on a machine where no interpreter can import
sentence_transformers -- NO-DATA, never a fake pass. The refusal-path tests
need no model at all: cmd_serve checks bind/token before it ever tries to
load one.

No em or en dashes anywhere in this file.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WARM = os.path.join(HERE, "bm_embed_warm.py")
sys.path.insert(0, HERE)
import bm_embed_warm as warm_mod  # noqa: E402
import bm_vault                   # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _importable(python, module):
    try:
        p = subprocess.run([python, "-c", "import %s" % module],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _find_model_python():
    """The first interpreter able to import sentence_transformers, checked
    in the same order tools/bm-embed-bge's own shim resolves one:
    BROTHERMODE_EMBED_PYTHON, the conventional .venv-embed sibling to this
    checkout's tools/ directory, then this interpreter itself. None when
    nobody has it."""
    candidates = []
    env_py = os.environ.get("BROTHERMODE_EMBED_PYTHON")
    if env_py:
        candidates.append(env_py)
    candidates.append(os.path.join(HERE, "..", ".venv-embed", "bin", "python"))
    candidates.append(sys.executable)
    for c in candidates:
        if c == sys.executable or os.path.exists(c):
            if _importable(c, "sentence_transformers"):
                return c if c == sys.executable else os.path.abspath(c)
    return None


MODEL_PY = _find_model_python()
MODEL_SKIP = ("NO-DATA: sentence_transformers is not importable by "
             "BROTHERMODE_EMBED_PYTHON, ../.venv-embed/bin/python, or "
             "sys.executable; the warm daemon cannot load a model here")


def _env_with_model_python():
    env = dict(os.environ)
    if MODEL_PY:
        env["BROTHERMODE_EMBED_PYTHON"] = MODEL_PY
    return env


def start_daemon(port, extra=()):
    python = MODEL_PY or sys.executable
    p = subprocess.Popen([python, WARM, "serve", "--bind", "127.0.0.1",
                          "--port", str(port)] + list(extra),
                         env=_env_with_model_python(),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p


def wait_alive(port, proc, deadline_s=90):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if warm_mod._daemon_alive("127.0.0.1", port):
            return True
        time.sleep(0.3)
    return False


def stop(p):
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    for pipe in (p.stdout, p.stderr):
        if pipe:
            pipe.close()


class TestBindRefusal(unittest.TestCase):
    """(a): the loopback gate, exercised without ever loading the model --
    cmd_serve checks bind/token BEFORE _load_model(), so this refuses in
    well under a second on any machine, model or no model."""

    def test_bind_beyond_loopback_without_token_refuses_to_start(self):
        p = subprocess.run([sys.executable, WARM, "serve", "--bind", "0.0.0.0",
                            "--port", str(free_port())],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=30)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"REFUSING", p.stderr)

    def test_default_bind_is_loopback(self):
        self.assertEqual(warm_mod.DEFAULT_HOST, "127.0.0.1")
        self.assertIn(warm_mod.DEFAULT_HOST, warm_mod.sv.LOOPBACK)


class TestFallbackNoException(unittest.TestCase):
    """(b1): with no daemon reachable at all, the seam must never raise --
    model present or not. A real vector or None are both correct answers
    here; an exception is the only failure this class checks for."""

    def test_embed_via_warm_returns_none_fast(self):
        port = free_port()  # nothing is listening there
        try:
            result = warm_mod.embed_via_warm([(0, "no daemon here")],
                                             host="127.0.0.1", port=port)
        except Exception as e:
            self.fail("embed_via_warm raised instead of returning None: %r" % e)
        # Promptness is the connect timeout's own contract (CONNECT_TIMEOUT in
        # bm_embed_warm.py); asserting a stopwatch here would measure the
        # machine, the exact pattern bm_lint_walltime.py refuses.
        self.assertIsNone(result)

    def test_bm_vault_embed_texts_seam_no_exception(self):
        port = free_port()
        old = os.environ.get("BM_EMBED_WARM_PORT")
        os.environ["BM_EMBED_WARM_PORT"] = str(port)
        try:
            vecs = bm_vault._embed_texts([(0, "brothermode vault text")], query=True)
        except Exception as e:
            self.fail("_embed_texts raised instead of falling back: %r" % e)
        finally:
            if old is None:
                del os.environ["BM_EMBED_WARM_PORT"]
            else:
                os.environ["BM_EMBED_WARM_PORT"] = old
        # The only claim under test is "no exception": the ORIGINAL
        # subprocess path's own result shape is unchanged by this row, and
        # on a checkout whose embed shim exists but cannot exec a real
        # interpreter (this one: no .venv-embed) that path already
        # returned an empty dict rather than None, before this row existed.
        self.assertIsInstance(vecs, (dict, type(None)))


@unittest.skipUnless(MODEL_PY, MODEL_SKIP)
class TestWarmDaemonBehaviour(unittest.TestCase):
    """(b2), (c), (d): needs a real, working embedder. Each test manages its
    own daemon on its own free port so test order never matters."""

    def test_default_bind_serves_on_loopback_only(self):
        port = free_port()
        proc = start_daemon(port)
        self.addCleanup(stop, proc)
        self.assertTrue(wait_alive(port, proc),
                        "warm daemon never came up (model load failed)")
        self.assertTrue(warm_mod._daemon_alive("127.0.0.1", port))

    def test_kill_mid_suite_fallback_agrees(self):
        port = free_port()
        proc = start_daemon(port)
        self.addCleanup(stop, proc)
        self.assertTrue(wait_alive(port, proc),
                        "warm daemon never came up (model load failed)")
        text = "the warm embedder holds the model between calls"
        warm_vec = warm_mod.embed_via_warm([(0, text)], host="127.0.0.1",
                                           port=port, query=True)
        self.assertIsNotNone(warm_vec, "warm daemon did not answer while alive")
        stop(proc)
        self.assertFalse(warm_mod._daemon_alive("127.0.0.1", port))
        old_bep = os.environ.get("BROTHERMODE_EMBED_PYTHON")
        if MODEL_PY:
            os.environ["BROTHERMODE_EMBED_PYTHON"] = MODEL_PY
        try:
            fallback_vec = bm_vault._embed_texts_subprocess([(0, text)], query=True)
        finally:
            if old_bep is None:
                os.environ.pop("BROTHERMODE_EMBED_PYTHON", None)
            else:
                os.environ["BROTHERMODE_EMBED_PYTHON"] = old_bep
        self.assertIsNotNone(fallback_vec,
                             "fallback subprocess path failed after daemon death")
        self.assertEqual(set(warm_vec), set(fallback_vec))
        for i in warm_vec:
            for a, b in zip(warm_vec[i], fallback_vec[i]):
                self.assertAlmostEqual(a, b, places=4)

    def test_vector_identity_within_tolerance(self):
        port = free_port()
        proc = start_daemon(port)
        self.addCleanup(stop, proc)
        self.assertTrue(wait_alive(port, proc),
                        "warm daemon never came up (model load failed)")
        pairs = [(0, "brothermode warm embedder identity check")]
        warm_vecs = warm_mod.embed_via_warm(pairs, host="127.0.0.1", port=port,
                                            query=False)
        old_bep = os.environ.get("BROTHERMODE_EMBED_PYTHON")
        if MODEL_PY:
            os.environ["BROTHERMODE_EMBED_PYTHON"] = MODEL_PY
        try:
            sub_vecs = bm_vault._embed_texts_subprocess(pairs, query=False)
        finally:
            if old_bep is None:
                os.environ.pop("BROTHERMODE_EMBED_PYTHON", None)
            else:
                os.environ["BROTHERMODE_EMBED_PYTHON"] = old_bep
        self.assertIsNotNone(warm_vecs, "warm daemon failed to embed")
        self.assertIsNotNone(sub_vecs, "subprocess path failed to embed")
        self.assertEqual(set(warm_vecs), set(sub_vecs))
        for i in warm_vecs:
            self.assertEqual(len(warm_vecs[i]), len(sub_vecs[i]))
            for a, b in zip(warm_vecs[i], sub_vecs[i]):
                self.assertAlmostEqual(a, b, places=4)


@unittest.skipIf(MODEL_PY == sys.executable,
                "sys.executable can already import sentence_transformers; the "
                "missing-model path is exercised only when it genuinely cannot")
class TestMeasureNoModel(unittest.TestCase):
    """(e): measure must never invent a number. On this checkout (no
    .venv-embed, see the read-first list) sys.executable cannot load the
    model, so a plain `measure` invocation with no override must name that
    rather than print fabricated timings."""

    def test_measure_prints_no_data_when_model_unavailable(self):
        env = dict(os.environ)
        env.pop("BROTHERMODE_EMBED_PYTHON", None)
        env["BM_EMBED_WARM_MEASURE_TIMEOUT"] = "8"
        p = subprocess.run([sys.executable, WARM, "measure"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, timeout=60)
        out = p.stdout.decode("utf-8")
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
