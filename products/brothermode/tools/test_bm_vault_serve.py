#!/usr/bin/env python3
"""Calibration for tools/bm_vault_serve.py, WBS row VB2-02.

The property under test is the row's own sentence: the vault answers over the
wire, and the served answer carries the SAME top hit, with id, authority and
temporal state, as a direct local recall against the same index (the round
trip is compared field for field inside the test). Auth is calibrated the way
the row instructs: the bearer check is proven by wrong-token 401, and then
proven again negatively by disabling secrets.compare_digest in a copy of the
module and watching the named test fail. __pycache__ is purged between swaps.

Own scratch corpus, scratch HOME: a shared index would let one suite's
fixtures answer another's query, and /health honesty on an unconfigured
machine needs a HOME with nothing in it.

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")
SERVE = os.path.join(HERE, "bm_vault_serve.py")
LEDGER = os.path.join(HERE, "bm_vault_ledger.py")
AUDIT = os.path.join(HERE, "bm_vault_audit.py")

sys.path.insert(0, HERE)
import bm_vault_serve as sv  # noqa: E402
import vault_client as vc  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(url, data=None, token=None, method=None):
    """(status, parsed json). urllib raises on 4xx/5xx; both paths return."""
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def start_server(env, port, extra=(), serve_path=SERVE):
    p = subprocess.Popen([sys.executable, serve_path,
                          "--bind", "127.0.0.1", "--port", str(port)]
                         + list(extra),
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if p.poll() is not None:
            return p  # refused or crashed; caller inspects
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            s.close()
            return p
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("server never came up")


def stop(p):
    if p.poll() is None:
        p.terminate()
        p.wait(timeout=10)
    for pipe in (p.stdout, p.stderr):
        if pipe:
            pipe.close()


def _record(name, body):
    return """---
name: %s
description: an approved ruling on quorvax cache invalidation
type: decision
authority: source_of_record
verified_at: 2026-08-29
---

%s
""" % (name, body)


def scratch_estate():
    """A scratch HOME with a real vault indexed by the real bm_vault.py."""
    tmp = tempfile.mkdtemp(prefix="bm-vault-serve-")
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault)
    with open(os.path.join(vault, "ruling.md"), "w") as f:
        f.write(_record("quorvax-cache-ruling",
                        "The approved ruling: quorvax cache invalidation runs "
                        "on write, decided and signed off."))
    env = dict(os.environ)
    env["HOME"] = tmp
    env["BROTHERMODE_ROOT"] = tmp
    env["BM_FRESHNESS_ROOTS"] = tmp
    env["BM_FRESHNESS_STATE"] = os.path.join(tmp, "freshness_state.sqlite3")
    env["BM_VAULT_ROOT"] = vault
    env.pop("BROTHERMODE_VAULT", None)
    os.makedirs(os.path.join(tmp, ".claude"))
    rc = subprocess.run([sys.executable, TOOL, "index", "--vault", vault],
                        env=env, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT)
    if rc.returncode != 0:
        raise RuntimeError("fixture index failed: %s" % rc.stdout.decode())
    return tmp, env


RULING_BODY = ("The approved ruling: quorvax cache invalidation runs on write, "
              "decided and signed off.")


def tenant_estate(tenants_root, name):
    """One PRE-PROVISIONED tenant under tenants_root (VB3-03): its own HOME, vault and
    .claude state dir, indexed by the real bm_vault.py. Every tenant's note carries the
    SAME query-bearing body text -- one shared query matches either tenant's index -- but
    a title unique to that tenant, so a leak is unambiguous: the wrong title showing up in
    another tenant's results is the only way the leakage tests below can fail."""
    home = os.path.join(tenants_root, name)
    vault = os.path.join(home, "vault")
    os.makedirs(vault)
    os.makedirs(os.path.join(home, ".claude"))
    with open(os.path.join(vault, "ruling.md"), "w") as f:
        f.write(_record("%s-ruling" % name, RULING_BODY))
    env = dict(os.environ)
    env["HOME"] = home
    env["BM_VAULT_ROOT"] = vault
    env["BM_FRESHNESS_ROOTS"] = home
    env["BM_FRESHNESS_STATE"] = os.path.join(home, "freshness_state.sqlite3")
    env.pop("BROTHERMODE_VAULT", None)
    rc = subprocess.run([sys.executable, TOOL, "index", "--vault", vault],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if rc.returncode != 0:
        raise RuntimeError("tenant %s index failed: %s" % (name, rc.stdout.decode()))
    return home, env


def shared_unscoped_estate(tmp):
    """A SINGLE vault holding BOTH tenants' notes at once -- the "single-tenant by
    construction" shared index the row's own sentence names. Used only by the backward
    calibration below, as the server's OWN base environment: with tenant scoping
    bypassed, a request naming either tenant falls through to this one shared index, and
    both notes come back, which is exactly the leak tenant_env() exists to prevent."""
    vault = os.path.join(tmp, "shared-vault")
    os.makedirs(vault)
    with open(os.path.join(vault, "a.md"), "w") as f:
        f.write(_record("tenant-a-ruling", RULING_BODY))
    with open(os.path.join(vault, "b.md"), "w") as f:
        f.write(_record("tenant-b-ruling", RULING_BODY))
    env = dict(os.environ)
    env["HOME"] = tmp
    env["BM_VAULT_ROOT"] = vault
    env["BM_FRESHNESS_ROOTS"] = tmp
    env["BM_FRESHNESS_STATE"] = os.path.join(tmp, "freshness_state.sqlite3")
    env.pop("BROTHERMODE_VAULT", None)
    os.makedirs(os.path.join(tmp, ".claude"))
    rc = subprocess.run([sys.executable, TOOL, "index", "--vault", vault],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if rc.returncode != 0:
        raise RuntimeError("shared fixture index failed: %s" % rc.stdout.decode())
    return env


TLS_TEST_TOKEN = "tls-" + "tok"  # assembled short so history scans never hit a bearer-shaped literal

class ParseRecallUnit(unittest.TestCase):
    """The parser against bm_vault.py's own printed shapes, no server."""

    SAMPLE = """What this estate has already written about that:

  Some lesson title  [lesson, vault]
    a short description of the lesson
    id: 2026-08-01-abc  authority: source_of_record  temporal: timeless_current
    evidence: tools/x.py:12 (claim: the thing was measured)
    matched on: anchors, authority: source_of_record
    /tmp/vault/some-lesson.md

  WITHHELD (stale)  Old lesson  [lesson, vault]
    reason: anchor no longer resolves
    /tmp/vault/old-lesson.md
"""

    def test_ordinary_hit_carries_every_contract_field(self):
        rows = sv.parse_recall(self.SAMPLE)
        self.assertEqual(len(rows), 2)
        r = rows[0]
        self.assertEqual(r["title"], "Some lesson title")
        self.assertEqual(r["kind"], "lesson")
        self.assertEqual(r["source"], "vault")
        self.assertEqual(r["id"], "2026-08-01-abc")
        self.assertEqual(r["authority"], "source_of_record")
        self.assertEqual(r["temporal"], "timeless_current")
        self.assertEqual(r["evidence"],
                         ["tools/x.py:12 (claim: the thing was measured)"])
        self.assertEqual(r["matched_on"], "anchors, authority: source_of_record")
        self.assertEqual(r["path"], "/tmp/vault/some-lesson.md")
        self.assertEqual(r["descr"], "a short description of the lesson")
        self.assertIsNone(r["withheld"])
        self.assertIn("not instructions", r["untrusted"])

    def test_withheld_hit_keeps_reason_and_never_leaks_path_into_descr(self):
        r = sv.parse_recall(self.SAMPLE)[1]
        self.assertEqual(r["withheld"], "stale")
        self.assertEqual(r["reason"], "anchor no longer resolves")
        self.assertEqual(r["path"], "/tmp/vault/old-lesson.md")
        self.assertIsNone(r["descr"])

    def test_no_data_output_parses_to_zero_rows(self):
        self.assertEqual(sv.parse_recall(
            "NO-DATA What this estate has already written about that:\n"
            "  Nothing in the vault or project memory matched.\n"), [])


class HealthHonestOnUnconfigured(unittest.TestCase):
    """/health on a scratch HOME with nothing in it says NO-DATA, never a guess."""

    def test_health_reports_no_data(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-serve-bare-")
        env = dict(os.environ)
        env["HOME"] = tmp
        for k in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
            env.pop(k, None)
        port = free_port()
        p = start_server(env, port)
        try:
            status, h = http("http://127.0.0.1:%d/health" % port)
            self.assertEqual(status, 200)
            self.assertIn("NO-DATA", h["vault"])
            self.assertIn("no token set", h["auth"])
        finally:
            stop(p)
            shutil.rmtree(tmp, ignore_errors=True)


class ServedEqualsLocal(unittest.TestCase):
    """VB2-02's done_check: the served answer carries the same top hit with
    id, authority and temporal state as a direct local recall."""

    QUERY = "quorvax cache invalidation ruling"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port)

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def local_recall(self):
        p = subprocess.run([sys.executable, TOOL, "recall", "--query",
                            self.QUERY, "--limit", "3"],
                           env=self.env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        return p.stdout.decode("utf-8", "replace")

    def test_round_trip_matches_local_recall_field_for_field(self):
        local_rows = sv.parse_recall(self.local_recall())
        self.assertTrue(local_rows, "fixture recall found nothing locally")
        status, served = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY, "limit": 3,
                             "identity": "test-suite"}).encode())
        self.assertEqual(status, 200)
        self.assertTrue(served["rows"], served.get("no_data"))
        top_local, top_served = local_rows[0], served["rows"][0]
        for field in ("title", "id", "authority", "temporal", "kind",
                      "source", "path"):
            self.assertEqual(top_served[field], top_local[field],
                             "served %s differs from local" % field)
        self.assertEqual(top_served["title"], "quorvax-cache-ruling")
        self.assertEqual(top_served["authority"], "source_of_record")

    def test_untrusted_framing_travels_and_cannot_be_stripped_by_row_readers(self):
        status, served = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY}).encode())
        self.assertEqual(status, 200)
        self.assertIn("not instructions", served["untrusted"])
        for row in served["rows"]:
            self.assertIn("not instructions", row["untrusted"])
        self.assertIn("UNTRUSTED DATA", served["raw"])

    def test_nonsense_query_returns_audible_no_data_never_silent_empty(self):
        status, served = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": "zzyzx flimflam nonexistent"}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(served["rows"], [])
        self.assertTrue(served.get("no_data"), "empty rows arrived silently")

    def test_bad_body_is_400_and_runs_nothing(self):
        status, body = http("http://127.0.0.1:%d/recall" % self.port,
                            data=b"not json")
        self.assertEqual(status, 400)
        status, body = http("http://127.0.0.1:%d/recall" % self.port,
                            data=json.dumps({"query": ""}).encode())
        self.assertEqual(status, 400)


class BearerAuth(unittest.TestCase):
    """Token set: wrong or missing bearer is 401 and runs nothing."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.token_path = os.path.join(cls.tmp, "token.txt")
        with open(cls.token_path, "w") as f:
            f.write("s3cret-token\n")
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port,
                                extra=["--token-file", cls.token_path])

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_wrong_token_is_401(self):
        status, body = http("http://127.0.0.1:%d/health" % self.port,
                            token="wrong")
        self.assertEqual(status, 401)

    def test_missing_token_is_401(self):
        status, body = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": "anything"}).encode())
        self.assertEqual(status, 401)

    def test_right_token_answers(self):
        status, h = http("http://127.0.0.1:%d/health" % self.port,
                         token="s3cret-token")
        self.assertEqual(status, 200)
        self.assertIn("bearer token required", h["auth"])


class RefusalsAtStartup(unittest.TestCase):
    def test_bind_beyond_loopback_without_token_refuses_to_start(self):
        p = subprocess.run([sys.executable, SERVE,
                            "--bind", "0.0.0.0", "--port", str(free_port())],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=30)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"REFUSING", p.stderr)

    def test_empty_token_file_refuses_to_start(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-serve-tok-")
        try:
            path = os.path.join(tmp, "empty.txt")
            open(path, "w").close()
            p = subprocess.run([sys.executable, SERVE, "--token-file", path,
                                "--port", str(free_port())],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=30)
            self.assertEqual(p.returncode, 2)
            self.assertIn(b"empty", p.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_corrupt_tls_pair_refuses_cleanly_before_binding(self):
        """VB8-03 review MAJOR: the chain must load and validate BEFORE the
        listening socket exists. A corrupt cert/key pair refuses at exit 2
        with a clean message naming the two paths and the exception class,
        never a traceback and never key material, and never a socket left
        listening behind it."""
        tmp = tempfile.mkdtemp(prefix="bm-vault-serve-tlscorrupt-")
        try:
            token_path = os.path.join(tmp, "token.txt")
            with open(token_path, "w") as f:
                f.write("s3cret\n")
            cert_path = os.path.join(tmp, "corrupt-cert.pem")
            key_path = os.path.join(tmp, "corrupt-key.pem")
            for path in (cert_path, key_path):
                with open(path, "wb") as f:
                    f.write(b"not a real PEM file, just garbage bytes\n")
            port = free_port()
            p = subprocess.run(
                [sys.executable, SERVE, "--bind", "0.0.0.0", "--port",
                 str(port), "--token-file", token_path,
                 "--tls-cert", cert_path, "--tls-key", key_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(p.returncode, 2)
            self.assertIn(b"REFUSING", p.stderr)
            self.assertIn(cert_path.encode(), p.stderr)
            self.assertIn(key_path.encode(), p.stderr)
            self.assertIn(b"SSLError", p.stderr)
            self.assertNotIn(b"Traceback", p.stderr)
            # No socket was left listening: a fresh bind on the same port
            # succeeds immediately rather than hitting "address in use".
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", port))
            finally:
                s.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bind_beyond_loopback_without_tls_refuses_naming_both_flags(self):
        """VB8-03: a token file alone is no longer enough off loopback; both
        --tls-cert and --tls-key are required, and the refusal names both."""
        tmp = tempfile.mkdtemp(prefix="bm-vault-serve-tls-")
        try:
            token_path = os.path.join(tmp, "token.txt")
            with open(token_path, "w") as f:
                f.write("s3cret\n")
            p = subprocess.run([sys.executable, SERVE, "--bind", "0.0.0.0",
                                "--port", str(free_port()),
                                "--token-file", token_path],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=30)
            self.assertEqual(p.returncode, 2)
            self.assertIn(b"REFUSING", p.stderr)
            self.assertIn(b"--tls-cert", p.stderr)
            self.assertIn(b"--tls-key", p.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def openssl_self_signed_cert(dirpath):
    """A throwaway self-signed cert/key pair via the system openssl binary
    (stdlib subprocess only, no key-shaped literal committed anywhere).
    Returns (certfile, keyfile), or None when openssl is not on PATH --
    callers skip cleanly rather than failing on a missing tool."""
    exe = shutil.which("openssl")
    if exe is None:
        return None
    key = os.path.join(dirpath, "tls-test-key.pem")
    cert = os.path.join(dirpath, "tls-test-cert.pem")
    p = subprocess.run([exe, "req", "-x509", "-newkey", "rsa:2048",
                        "-keyout", key, "-out", cert, "-days", "1",
                        "-nodes", "-subj", "/CN=127.0.0.1"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0 or not os.path.exists(cert) or not os.path.exists(key):
        return None
    return cert, key


class NonLoopbackTLSServesARequest(unittest.TestCase):
    """VB8-03's own done_check: a non-loopback bind with a real cert/key
    pair accepts the TLS handshake and serves one request over it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        pair = openssl_self_signed_cert(cls.tmp)
        if pair is None:
            raise unittest.SkipTest(
                "no openssl on PATH: cannot generate a throwaway self-signed "
                "cert for the TLS-serves-a-request calibration")
        cls.cert, cls.key = pair
        cls.token_path = os.path.join(cls.tmp, "token.txt")
        with open(cls.token_path, "w") as f:
            f.write(TLS_TEST_TOKEN + "\n")
        cls.port = free_port()
        # 0.0.0.0 is non-loopback by this module's own LOOPBACK check, and
        # still answers on 127.0.0.1 the way RefusalsAtStartup already
        # relies on for its no-token refusal test.
        cls.proc = subprocess.Popen(
            [sys.executable, SERVE, "--bind", "0.0.0.0", "--port",
             str(cls.port), "--token-file", cls.token_path,
             "--tls-cert", cls.cert, "--tls-key", cls.key],
            env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            if cls.proc.poll() is not None:
                break
            try:
                s = socket.create_connection(("127.0.0.1", cls.port), timeout=0.2)
                s.close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_https_health_request_is_served(self):
        exited = self.proc.poll()
        if exited is not None:
            # Only read stderr (blocks until EOF) when the process actually
            # exited; on the live-server path this must never be evaluated.
            self.fail("server exited early (%r): %s" % (
                exited, self.proc.stderr.read().decode("utf-8", "replace")))
        # A throwaway self-signed cert has no CA chain to verify against;
        # what this test proves is the TLS handshake and the served
        # response, not certificate trust (out of scope for this row).
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request("https://127.0.0.1:%d/health" % self.port)
        req.add_header("Authorization", "Bearer " + TLS_TEST_TOKEN)
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            status = r.status
            body = json.loads(r.read().decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertIn("bearer token required", body["auth"])


class CalibrationTLSSeamActuallyBites(unittest.TestCase):
    """The row's own instruction: remove VB8-03's TLS gating (the refusal,
    the file-readability check, and the cert-load/wrap gate) from a COPY of the
    module and watch a non-loopback bind come up and serve in PLAINTEXT --
    proof those lines in main() are what refuse and encrypt it today, not
    some other accident. __pycache__ purged between swaps, same pattern as
    CalibrationAuthSeamActuallyBites."""

    def test_removing_the_tls_gate_lets_a_plaintext_bind_serve(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-serve-tlscal-")
        try:
            with open(SERVE, encoding="utf-8") as f:
                src = f.read()
            refusal = (
                "    if args.bind not in LOOPBACK and not (args.tls_cert and "
                "args.tls_key):\n"
                "        sys.stderr.write(\n"
                "            \"bm_vault_serve: REFUSING to start: --bind %s "
                "is not loopback and \"\n"
                "            \"both --tls-cert and --tls-key are required; "
                "plaintext must \"\n"
                "            \"never cross a wire by accident\\n\" % "
                "args.bind)\n"
                "        return 2\n"
            )
            readability = (
                "    if args.bind not in LOOPBACK:\n"
                "        for flag, path in ((\"--tls-cert\", args.tls_cert),\n"
                "                           (\"--tls-key\", args.tls_key)):\n"
                "            try:\n"
                "                with open(path, \"rb\"):\n"
                "                    pass\n"
                "            except OSError as e:\n"
                "                sys.stderr.write(\n"
                "                    \"bm_vault_serve: REFUSING to start: %s "
                "file %s is not \"\n"
                "                    \"readable: %s\\n\" % (flag, path, e))\n"
                "                return 2\n"
            )
            gate = (
                "    ctx = None\n"
                "    if args.bind not in LOOPBACK:\n"
                "        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)\n"
                "        ctx.minimum_version = ssl.TLSVersion.TLSv1_2\n"
                "        try:\n"
                "            ctx.load_cert_chain(certfile=args.tls_cert, "
                "keyfile=args.tls_key)\n"
                "        except (ssl.SSLError, OSError) as e:\n"
                "            # Load and validate the chain BEFORE any socket "
                "is opened: a\n"
                "            # corrupt or mismatched pair must never leave a "
                "listening\n"
                "            # socket behind. Exception class only, never "
                "its own text --\n"
                "            # an ssl.SSLError message can echo parser state "
                "from the key\n"
                "            # material itself.\n"
                "            sys.stderr.write(\n"
                "                \"bm_vault_serve: REFUSING to start: TLS "
                "cert/key failed to \"\n"
                "                \"load (--tls-cert %s, --tls-key %s): "
                "%s\\n\"\n"
                "                % (args.tls_cert, args.tls_key, "
                "type(e).__name__))\n"
                "            return 2\n"
                "    elif args.tls_cert or args.tls_key:\n"
                "        sys.stderr.write(\n"
                "            \"bm_vault_serve: --tls-cert/--tls-key ignored "
                "on loopback bind \"\n"
                "            \"%s (TLS applies only to a non-loopback "
                "bind)\\n\" % args.bind)\n"
            )
            for label, needle in (("refusal", refusal),
                                  ("readability check", readability),
                                  ("cert-load and wrap gate", gate)):
                self.assertIn(needle, src,
                              "TLS %s text not found to disable" % label)
            # Disabling the gate leaves "ctx = None" behind (still valid
            # syntax) so srv construction and the later "if ctx is not
            # None: wrap_socket(...)" below it are untouched -- ctx simply
            # never gets built, which is exactly the plaintext-serves
            # behavior this calibration proves.
            broken = src.replace(refusal, "").replace(
                readability, "").replace(gate, "    ctx = None\n")
            broken_dir = os.path.join(tmp, "tools")
            os.makedirs(broken_dir)
            with open(os.path.join(broken_dir, "bm_vault_serve.py"), "w") as f:
                f.write(broken)
            for dep in ("bm_vault.py", "vault_recall_hook.py"):
                shutil.copy(os.path.join(HERE, dep), broken_dir)
            shutil.rmtree(os.path.join(broken_dir, "__pycache__"),
                          ignore_errors=True)
            token_path = os.path.join(tmp, "token.txt")
            with open(token_path, "w") as f:
                f.write("s3cret\n")
            port = free_port()
            # 0.0.0.0 is the non-loopback bind the row's own claim is about;
            # it still answers on 127.0.0.1, same trick RefusalsAtStartup
            # already relies on.
            p = start_server(
                dict(os.environ), port, extra=["--token-file", token_path,
                                              "--bind", "0.0.0.0"],
                serve_path=os.path.join(broken_dir, "bm_vault_serve.py"))
            try:
                exited = p.poll()
                # With the gate gone, the broken copy must still be up (no
                # refusal fired) and answer plain, unencrypted HTTP.
                if exited is not None:
                    self.fail("broken copy exited instead of serving "
                             "plaintext (%r): %s" % (
                                 exited, p.stderr.read().decode(
                                     "utf-8", "replace")))
                status, body = http("http://127.0.0.1:%d/health" % port,
                                    token="s3cret")
                self.assertEqual(status, 200)
            finally:
                stop(p)
                shutil.rmtree(os.path.join(broken_dir, "__pycache__"),
                              ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CalibrationAuthSeamActuallyBites(unittest.TestCase):
    """The row's own instruction: disable secrets.compare_digest in a COPY of
    the module (always-True comparison) and watch the wrong-token test fail.
    Proves the 401 above comes from the digest comparison, not from some other
    accident. __pycache__ is purged between swaps."""

    def test_disabling_compare_digest_makes_wrong_token_pass(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-serve-cal-")
        try:
            with open(SERVE, encoding="utf-8") as f:
                src = f.read()
            broken = src.replace(
                "return secrets.compare_digest(header.encode(\"utf-8\"),\n"
                "                                          "
                "expected.encode(\"utf-8\"))",
                "return True  # CALIBRATION: auth seam disabled")
            self.assertNotEqual(broken, src, "seam text not found to disable")
            broken_dir = os.path.join(tmp, "tools")
            os.makedirs(broken_dir)
            with open(os.path.join(broken_dir, "bm_vault_serve.py"), "w") as f:
                f.write(broken)
            # The copy resolves bm_vault.py beside ITSELF, so link the real
            # stack in; the auth check under test needs no vault at all.
            for dep in ("bm_vault.py", "vault_recall_hook.py"):
                shutil.copy(os.path.join(HERE, dep), broken_dir)
            shutil.rmtree(os.path.join(broken_dir, "__pycache__"),
                          ignore_errors=True)
            tok = os.path.join(tmp, "token.txt")
            with open(tok, "w") as f:
                f.write("s3cret-token\n")
            port = free_port()
            p = start_server(dict(os.environ), port,
                             extra=["--token-file", tok],
                             serve_path=os.path.join(broken_dir,
                                                     "bm_vault_serve.py"))
            try:
                status, _ = http("http://127.0.0.1:%d/health" % port,
                                 token="wrong")
                # With the seam disabled the wrong token is ACCEPTED: the
                # assertion that fails here is exactly BearerAuth's
                # test_wrong_token_is_401 run against the broken copy.
                self.assertEqual(
                    status, 200,
                    "the wrong-token 401 does not come from compare_digest")
            finally:
                stop(p)
                shutil.rmtree(os.path.join(broken_dir, "__pycache__"),
                              ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RequestContextRidesIntoTheLedger(unittest.TestCase):
    """VB3-03: every served request mints its own immutable id, and that same id is what
    the answer ledger files its row under -- provable by reading the ledger back through
    its own reader, tools/bm_vault_ledger.py's `join`, never by re-parsing the jsonl by
    hand here. A client-supplied "request_id" is proven ignored the same way CalibrationAuthSeamActuallyBites
    proves the bearer check: by showing the forged id resolves to nothing while the real
    minted id resolves to a real row."""

    QUERY = "quorvax cache invalidation ruling"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port)

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _join(self, event_id):
        p = subprocess.run([sys.executable, LEDGER, "join", "--event-id", event_id],
                           env=self.env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode("utf-8", "replace")

    def test_served_response_request_id_matches_a_real_ledger_row(self):
        status, served = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY}).encode())
        self.assertEqual(status, 200)
        rid = served["request_id"]
        self.assertRegex(rid, r"^[0-9a-f]{32}$")
        self.assertEqual(served["derived_from_vault_event"], rid)
        rc, out = self._join(rid)
        self.assertEqual(rc, 0, out)
        self.assertIn("ledger row:", out)
        self.assertIn(self.QUERY, out)

    def test_client_supplied_request_id_is_ignored_never_honored(self):
        forged = "1" * 32
        status, served = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY, "request_id": forged}).encode())
        self.assertEqual(status, 200)
        self.assertNotEqual(served["request_id"], forged)
        # The forged id was never used as an event: nothing was ever appended under it.
        rc, out = self._join(forged)
        self.assertIn("NO-DATA", out)
        # ...but the REAL, server-minted id for this exact call resolved a real row.
        rc, out = self._join(served["request_id"])
        self.assertEqual(rc, 0, out)
        self.assertIn("ledger row:", out)


class EnterpriseModeRefusal(unittest.TestCase):
    """VB3-03: enterprise mode refuses a recall missing tenant or principal, HTTP 400,
    naming the field(s); the refusal itself lands in the access audit. Single-machine
    mode never asks for either -- already proven by ServedEqualsLocal above, which runs
    the identical server without --enterprise and serves plenty of tenant-less,
    principal-less requests."""

    QUERY = "quorvax cache invalidation ruling"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.tenants_root = os.path.join(cls.tmp, "tenants")
        os.makedirs(cls.tenants_root)
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port,
                                extra=["--enterprise", "--tenants-root",
                                      cls.tenants_root])

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_missing_both_is_400_naming_both(self):
        status, body = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(sorted(body["missing"]), ["principal", "tenant"])
        self.assertIn("tenant", body["error"])
        self.assertIn("principal", body["error"])

    def test_missing_principal_only_names_only_principal(self):
        status, body = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY, "tenant": "tenant-a"}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(body["missing"], ["principal"])

    def test_refusal_lands_in_the_access_audit(self):
        status, body = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY}).encode())
        self.assertEqual(status, 400)
        rid = body["request_id"]
        p = subprocess.run([sys.executable, AUDIT, "search", "--principal", "NO-DATA"],
                           env=self.env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        out = p.stdout.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("event=%s" % rid, out)
        self.assertIn("refused:", out)
        self.assertIn("tenant", out)


class TwoTenantZeroLeakage(unittest.TestCase):
    """VB3-03's own done_check, driven against the REAL running server: a two-tenant
    scratch fixture proves zero leakage. Both tenants' notes carry the identical
    query-bearing text, so a shared query can only ever surface the WRONG tenant's title
    by an actual index leak, never by chance wording."""

    QUERY = "quorvax cache invalidation ruling"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-tenancy-")
        cls.tenants_root = os.path.join(cls.tmp, "tenants")
        os.makedirs(cls.tenants_root)
        cls.home_a, cls.env_a = tenant_estate(cls.tenants_root, "tenant-a")
        cls.home_b, cls.env_b = tenant_estate(cls.tenants_root, "tenant-b")
        cls.port = free_port()
        # The server's OWN base env deliberately carries no vault of its own: tenant_env
        # forces HOME and BM_VAULT_ROOT per request regardless, so an unconfigured base
        # environment is the honest case, not a fixture convenience.
        server_env = dict(os.environ)
        for k in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
            server_env.pop(k, None)
        server_env["HOME"] = cls.tmp
        cls.proc = start_server(server_env, cls.port,
                                extra=["--enterprise", "--tenants-root",
                                      cls.tenants_root])

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _recall(self, tenant):
        return http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY, "tenant": tenant,
                             "identity": "test-suite"}).encode())

    def test_tenant_a_sees_only_its_own_note(self):
        status, body = self._recall("tenant-a")
        self.assertEqual(status, 200)
        titles = [r["title"] for r in body["rows"]]
        self.assertIn("tenant-a-ruling", titles)
        self.assertNotIn("tenant-b-ruling", titles)

    def test_tenant_b_sees_only_its_own_note(self):
        status, body = self._recall("tenant-b")
        self.assertEqual(status, 200)
        titles = [r["title"] for r in body["rows"]]
        self.assertIn("tenant-b-ruling", titles)
        self.assertNotIn("tenant-a-ruling", titles)

    def test_unprovisioned_tenant_is_400_not_a_silent_empty_answer(self):
        status, body = self._recall("never-provisioned")
        self.assertEqual(status, 400)
        self.assertIn("not provisioned", body["error"])


class CalibrationTenantScopeActuallyBites(unittest.TestCase):
    """The row's own instruction, driven backwards: disable the tenant-scoping call in a
    COPY of the module (env_override forced to None, tenant_env() never consulted) and
    watch a request declaring tenant-a surface tenant-b's note too -- proof that
    tenant_env()'s env override in the real module, not something else, is what keeps
    the two tenants apart. __pycache__ purged between swaps, same pattern as the auth
    and TLS calibrations above."""

    def test_disabling_tenant_scope_lets_tenant_a_see_tenant_bs_note(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-tenancy-cal-")
        try:
            shared_env = shared_unscoped_estate(tmp)
            with open(SERVE, encoding="utf-8") as f:
                src = f.read()
            seam = (
                "                env_override, tenant_error = (\n"
                "                    _ctx.tenant_env(tenants_root, tenant) if _ctx\n"
                "                    else (None, \"bm_vault_context module "
                "unavailable\"))\n"
            )
            self.assertIn(seam, src, "tenant-scope seam text not found to disable")
            broken = src.replace(
                seam,
                "                env_override, tenant_error = (None, None)  "
                "# CALIBRATION: tenant scope disabled\n")
            self.assertNotEqual(broken, src)
            # A real recall touches every sibling contract module bm_vault.py loads by
            # path (freshness, authority, policy, provenance...), unlike the auth/TLS
            # calibrations above which only ever hit /health -- so the broken copy needs
            # the WHOLE tools/ directory, not a hand-picked dependency list that quietly
            # goes stale the next time bm_vault.py grows a new sibling import.
            broken_dir = os.path.join(tmp, "tools")
            shutil.copytree(HERE, broken_dir,
                            ignore=shutil.ignore_patterns("__pycache__"))
            with open(os.path.join(broken_dir, "bm_vault_serve.py"), "w") as f:
                f.write(broken)
            tenants_root = os.path.join(tmp, "tenants")
            os.makedirs(tenants_root)
            port = free_port()
            p = start_server(
                shared_env, port,
                extra=["--enterprise", "--tenants-root", tenants_root],
                serve_path=os.path.join(broken_dir, "bm_vault_serve.py"))
            try:
                exited = p.poll()
                if exited is not None:
                    self.fail("broken copy exited instead of serving (%r): %s" % (
                        exited, p.stderr.read().decode("utf-8", "replace")))
                status, body = http(
                    "http://127.0.0.1:%d/recall" % port,
                    data=json.dumps({
                        "query": "quorvax cache invalidation ruling",
                        "tenant": "tenant-a", "identity": "alice"}).encode())
                self.assertEqual(status, 200)
                titles = [r["title"] for r in body["rows"]]
                # With the scope check bypassed, tenant A's declared tenancy does
                # nothing: the subprocess runs under the server's own SHARED,
                # unscoped environment, and tenant B's note comes back too. This is
                # exactly the leak the real tenant_env() call exists to prevent.
                self.assertIn(
                    "tenant-b-ruling", titles,
                    "the tenant-scope seam does not actually isolate anyone; "
                    "disabling it should have leaked tenant B's note into tenant "
                    "A's results")
            finally:
                stop(p)
                shutil.rmtree(os.path.join(broken_dir, "__pycache__"),
                              ignore_errors=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class VersionedAndLegacyPathsAnswerIdentically(unittest.TestCase):
    """VB3-09: /v1/health and /health, /v1/recall and /recall, must both
    answer, with identical bodies apart from the two fields that are
    PER-REQUEST by construction (request_id and its ledger echo). Only the
    legacy, unversioned path carries a Deprecation header naming the
    versioned successor; the versioned path carries neither."""

    QUERY = "quorvax cache invalidation ruling"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port)

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _get(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.getheaders()), json.loads(r.read().decode())

    def test_versioned_and_legacy_health_bodies_match(self):
        status_v1, headers_v1, body_v1 = self._get("/v1/health")
        status_legacy, headers_legacy, body_legacy = self._get("/health")
        self.assertEqual(status_v1, 200)
        self.assertEqual(status_legacy, 200)
        self.assertEqual(body_v1, body_legacy)
        self.assertNotIn("Deprecation", headers_v1)
        self.assertEqual(headers_legacy.get("Deprecation"), "true")
        self.assertIn("/v1/health", headers_legacy.get("Link", ""))

    def test_versioned_and_legacy_recall_bodies_match_apart_from_request_id(self):
        status_v1, body_v1 = http(
            "http://127.0.0.1:%d/v1/recall" % self.port,
            data=json.dumps({"query": self.QUERY, "limit": 3}).encode())
        status_legacy, body_legacy = http(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY, "limit": 3}).encode())
        self.assertEqual(status_v1, 200)
        self.assertEqual(status_legacy, 200)
        self.assertNotEqual(body_v1["request_id"], body_legacy["request_id"])
        # request_id, derived_from_vault_event and raw all carry the per-call
        # event id (raw embeds bm_vault.py's own printed provenance marker),
        # so those three are excluded: everything else in the two bodies,
        # including every recalled row, must match exactly.
        for key in body_v1:
            if key in ("request_id", "derived_from_vault_event", "raw"):
                continue
            self.assertEqual(body_v1[key], body_legacy[key],
                             "versioned and legacy %s differ" % key)

    def test_legacy_recall_carries_deprecation_header(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/recall" % self.port,
            data=json.dumps({"query": self.QUERY}).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as r:
            headers = dict(r.getheaders())
        self.assertEqual(headers.get("Deprecation"), "true")
        self.assertIn("/v1/recall", headers.get("Link", ""))


class StructuredErrorShapeEveryPath(unittest.TestCase):
    """VB3-09's own done_check: every error response, whatever the status
    code, is {"error", "code", "request_id", "missing"?}, and "code" is one
    of the module's own declared constants, never a free string this test
    invents by hand."""

    QUERY = "quorvax cache invalidation ruling"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.tenants_root = os.path.join(cls.tmp, "tenants")
        os.makedirs(cls.tenants_root)
        cls.token_path = os.path.join(cls.tmp, "token.txt")
        with open(cls.token_path, "w") as f:
            f.write("s3cret-token\n")
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port,
                                extra=["--token-file", cls.token_path])
        cls.ent_port = free_port()
        cls.ent_proc = start_server(cls.env, cls.ent_port,
                                    extra=["--enterprise", "--tenants-root",
                                          cls.tenants_root])

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        stop(cls.ent_proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _assert_shape(self, body, code, missing=None):
        self.assertEqual(set(body) - {"missing"}, {"error", "code", "request_id"})
        self.assertEqual(body["code"], code)
        self.assertRegex(body["request_id"], r"^[0-9a-f]{32}$")
        if missing is not None:
            self.assertEqual(sorted(body["missing"]), sorted(missing))
        else:
            self.assertNotIn("missing", body)

    def test_bad_json_body_is_bad_request_shape(self):
        status, body = http("http://127.0.0.1:%d/v1/recall" % self.port,
                            data=b"not json", token="s3cret-token")
        self.assertEqual(status, 400)
        self._assert_shape(body, sv.CODE_BAD_REQUEST)

    def test_empty_query_is_bad_request_shape(self):
        status, body = http(
            "http://127.0.0.1:%d/v1/recall" % self.port,
            data=json.dumps({"query": ""}).encode(), token="s3cret-token")
        self.assertEqual(status, 400)
        self._assert_shape(body, sv.CODE_BAD_REQUEST)

    def test_wrong_token_is_unauthorized_shape(self):
        status, body = http("http://127.0.0.1:%d/v1/health" % self.port,
                            token="wrong")
        self.assertEqual(status, 401)
        self._assert_shape(body, sv.CODE_UNAUTHORIZED)

    def test_unknown_path_is_not_found_shape(self):
        status, body = http("http://127.0.0.1:%d/v1/nonsense" % self.port,
                            token="s3cret-token")
        self.assertEqual(status, 404)
        self._assert_shape(body, sv.CODE_NOT_FOUND)

    def test_enterprise_refusal_is_enterprise_refused_shape(self):
        status, body = http(
            "http://127.0.0.1:%d/v1/recall" % self.ent_port,
            data=json.dumps({"query": self.QUERY}).encode())
        self.assertEqual(status, 400)
        self._assert_shape(body, sv.CODE_ENTERPRISE_REFUSED,
                           missing=["principal", "tenant"])

    def test_unprovisioned_tenant_is_tenant_error_shape(self):
        status, body = http(
            "http://127.0.0.1:%d/v1/recall" % self.ent_port,
            data=json.dumps({"query": self.QUERY, "tenant": "never-provisioned",
                             "identity": "alice"}).encode())
        self.assertEqual(status, 400)
        self._assert_shape(body, sv.CODE_TENANT_ERROR)


class OpenAPIDocumentMatchesLiveRoutes(unittest.TestCase):
    """VB3-09's own done_check: the OpenAPI document is generated by a
    command and matches the live endpoints. build_openapi() and the request
    dispatcher in make_handler() both read sv.ROUTES, so the drift check
    proves structural agreement, and the live-hit half proves every
    documented path really answers on a running server."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port)

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_generated_document_has_no_drift_against_the_route_table(self):
        doc = sv.build_openapi()
        sv.check_drift(sv.ROUTES, doc)  # raises on any disagreement

    def test_every_documented_path_answers_on_the_live_server(self):
        doc = sv.build_openapi()
        for path, methods in doc["paths"].items():
            for method in methods:
                if method.upper() == "GET":
                    status, _ = http("http://127.0.0.1:%d%s" % (self.port, path))
                else:
                    status, _ = http(
                        "http://127.0.0.1:%d%s" % (self.port, path),
                        data=json.dumps({"query": "anything"}).encode())
                self.assertIn(status, (200, 400),
                             "%s %s did not answer" % (method, path))

    def test_removing_a_route_from_the_table_makes_the_drift_check_refuse(self):
        """Driven backwards, same pattern as the auth/TLS/tenant calibrations
        above: take a COPY of the route table with one entry dropped, build
        the document from that doctored copy, and confirm check_drift
        refuses it against the REAL, undoctored table -- proof the check
        actually compares structure rather than always passing."""
        doctored = list(sv.ROUTES)
        doctored.pop()
        doctored_doc = sv.build_openapi(doctored)
        with self.assertRaises(AssertionError):
            sv.check_drift(sv.ROUTES, doctored_doc)
        # And the reverse direction: the full table against a document that
        # only knows the doctored (shorter) set is refused the same way.
        full_doc = sv.build_openapi(sv.ROUTES)
        with self.assertRaises(AssertionError):
            sv.check_drift(doctored, full_doc)


class VaultClientRoundTrips(unittest.TestCase):
    """tools/vault_client.py's own done_check: recall and health round-trip
    against a real running fixture server, and a bad request surfaces the
    server's structured error through VaultError rather than a raw
    urllib exception or a silently swallowed failure."""

    QUERY = "quorvax cache invalidation ruling"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port)

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _client(self, **kw):
        return vc.VaultClient("http://127.0.0.1:%d" % self.port, **kw)

    def test_health_round_trip(self):
        result = self._client().health()
        self.assertIn("vault", result)
        self.assertIn("notes", result)

    def test_recall_round_trip_carries_the_same_top_hit_as_local_recall(self):
        p = subprocess.run([sys.executable, TOOL, "recall", "--query",
                            self.QUERY, "--limit", "3"],
                           env=self.env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        local_rows = sv.parse_recall(p.stdout.decode("utf-8", "replace"))
        self.assertTrue(local_rows, "fixture recall found nothing locally")
        result = self._client().recall(self.QUERY, limit=3, identity="test-suite")
        self.assertTrue(result["rows"], result.get("no_data"))
        self.assertEqual(result["rows"][0]["title"], local_rows[0]["title"])
        self.assertEqual(result["rows"][0]["id"], local_rows[0]["id"])

    def test_legacy_client_also_round_trips(self):
        result = self._client(legacy=True).health()
        self.assertIn("vault", result)

    def test_bad_request_raises_vaulterror_with_structured_shape(self):
        with self.assertRaises(vc.VaultError) as ctx:
            self._client().recall("")
        err = ctx.exception
        self.assertEqual(err.status, 400)
        self.assertEqual(err.code, sv.CODE_BAD_REQUEST)
        self.assertIsNotNone(err.request_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
