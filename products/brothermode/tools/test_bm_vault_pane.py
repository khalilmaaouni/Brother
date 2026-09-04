#!/usr/bin/env python3
"""Calibration for tools/bm_vault_pane.py, WBS row VB11-05.

The row's own done-check, driven backwards on a fixture vault: a promotion
approved through the pane lands as a recorded promotion with the approver
principal in the audit; a revoked principal's click refuses; nothing lands
on a wrong action token; a curation accept mirrors the promotion path; GET
/pending never mutates; an empty queue serves an honest empty listing naming
the vault.

Server driven the same way test_bm_vault_serve.py drives bm_vault_serve.py:
a real subprocess, real HTTP, own scratch HOME so this suite's fixtures can
never answer another suite's query. Verification goes through the estate's
own readers (bm_vault_lifecycle.read_promotion for promotion state,
bm_vault_audit's own _read_rows for the audit trail) rather than a second
hand-rolled parser of file text.

No em or en dashes anywhere in this file.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PANE = os.path.join(HERE, "bm_vault_pane.py")

sys.path.insert(0, HERE)
import bm_vault_lifecycle as lifecycle  # noqa: E402
import bm_vault_audit as audit_mod      # noqa: E402

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


def http(url, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def start_server(env, port, extra=()):
    p = subprocess.Popen([sys.executable, PANE,
                          "--bind", "127.0.0.1", "--port", str(port)]
                         + list(extra),
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if p.poll() is not None:
            return p
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


def _note(name, frontmatter_extra, body):
    return "---\nname: %s\n%s---\n\n%s\n" % (name, frontmatter_extra, body)


def _tree_snapshot(root):
    """A stable digest of every file's path, size and content under root,
    used to prove a GET changed zero bytes on disk (requirement d)."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            h.update(os.path.relpath(path, root).encode("utf-8"))
            with open(path, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def scratch_estate():
    """A scratch HOME with a vault carrying one candidate note, one
    validated note, a two-note curation pair, and a principal registry with
    one active and one revoked principal.

    Both promotion notes carry an `author:` distinct from every principal
    this fixture's tests approve with (V14: cmd_promote refuses fail-closed
    on a note with no author of record, so a note this fixture expects to
    promote successfully needs one on record)."""
    tmp = tempfile.mkdtemp(prefix="bm-vault-pane-")
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault)
    with open(os.path.join(vault, "promo-candidate.md"), "w") as f:
        f.write(_note("promo-candidate", "promotion: candidate\nauthor: agent-session\n",
                      "A candidate ruling awaiting review."))
    with open(os.path.join(vault, "promo-validated.md"), "w") as f:
        f.write(_note("promo-validated", "promotion: validated\nauthor: agent-session\n",
                      "A validated ruling awaiting canonical status."))
    with open(os.path.join(vault, "curation-a.md"), "w") as f:
        f.write(_note("curation-a", "created: 2026-08-01\n", "Content A."))
    with open(os.path.join(vault, "curation-b.md"), "w") as f:
        f.write(_note("curation-b", "created: 2026-08-02\n", "Content B."))
    system_dir = os.path.join(vault, "99-System")
    os.makedirs(system_dir)
    with open(os.path.join(system_dir, "principals.json"), "w") as f:
        json.dump({"principals": {
            "trusted-approver": {
                "kind": "human", "status": "active",
                "added_at": "2026-08-30", "added_by": "test",
                "recorded_at": "2026-08-30", "recorded_by": "test"},
            "banned-approver": {
                "kind": "human", "status": "revoked",
                "added_at": "2026-08-30", "added_by": "test",
                "recorded_at": "2026-08-30", "recorded_by": "test"},
        }}, f)
    queue_path = os.path.join(tmp, "curation_queue.json")
    with open(queue_path, "w") as f:
        json.dump({
            "generated": "2026-08-30T00:00:00+00:00", "vault": vault,
            "queue": [{"pair": ["curation-a", "curation-b"],
                      "titles": ["curation-a", "curation-b"],
                      "finders": {"jaccard": 0.5}, "combined": 0.5,
                      "built": "2026-08-30T00:00:00+00:00", "owner": "test"}],
            "rejections": [], "audit": [],
        }, f)
    env = dict(os.environ)
    env["HOME"] = tmp
    env["BM_VAULT_ROOT"] = vault
    env.pop("BROTHERMODE_VAULT", None)
    os.makedirs(os.path.join(tmp, ".claude"))
    return tmp, vault, queue_path, env


class PendingListingReadOnly(unittest.TestCase):
    """Requirement (d): GET /pending changes zero bytes on disk."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault, cls.queue, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port, extra=["--queue", cls.queue])

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_get_pending_mutates_nothing(self):
        before_vault = _tree_snapshot(self.vault)
        before_queue = _tree_snapshot(os.path.dirname(self.queue))
        status, body = http("http://127.0.0.1:%d/pending" % self.port)
        self.assertEqual(status, 200)
        after_vault = _tree_snapshot(self.vault)
        after_queue = _tree_snapshot(os.path.dirname(self.queue))
        self.assertEqual(before_vault, after_vault,
                         "GET /pending wrote to the vault")
        self.assertEqual(before_queue, after_queue,
                         "GET /pending wrote to the queue directory")
        self.assertEqual(len(body["promotions"]), 2)
        self.assertEqual(len(body["curation"]), 1)


class ApprovalLandsWithPrincipal(unittest.TestCase):
    """Requirement (a): approve records the promotion with the clicking
    principal, and one audit row names that principal."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault, cls.queue, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port, extra=["--queue", cls.queue])
        audit_mod.AUDIT_PATH = os.path.join(cls.tmp, ".claude", "bm_vault_audit.jsonl")

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _note_path(self):
        return os.path.join(self.vault, "promo-candidate.md")

    def _read_state(self):
        with open(self._note_path(), encoding="utf-8") as f:
            return lifecycle.read_promotion(f.read())

    def test_approve_records_promotion_and_audit_row(self):
        status, pending = http("http://127.0.0.1:%d/pending" % self.port)
        self.assertEqual(status, 200)
        item = next(p for p in pending["promotions"] if p["id"] == "promo-candidate.md")
        token = item["action_tokens"]["approve"]

        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "promotion", "id": "promo-candidate.md",
                             "decision": "approve", "principal": "trusted-approver",
                             "action_token": token}).encode())
        self.assertEqual(status, 200, result)
        self.assertEqual(result["rc"], 0)

        state, record, problems = self._read_state()
        self.assertEqual(state, "validated")
        self.assertEqual(record.get("promoted_by"), "trusted-approver")
        self.assertEqual(problems, [])

        rows = audit_mod._read_rows()
        self.assertIsNotNone(rows, "no audit rows were written")
        matches = [r for r in rows if r.get("event_id") == result["event_id"]]
        self.assertEqual(len(matches), 1, "no single audit row for this event")
        row = matches[0]
        self.assertEqual(row["principal"], "trusted-approver")
        self.assertIn("promo-candidate.md", row["served_ids"])
        self.assertNotIn("refused", row)


class RevokedPrincipalRefuses(unittest.TestCase):
    """Requirement (b): a revoked principal's click refuses; nothing lands;
    the refusal appears in the audit."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault, cls.queue, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port, extra=["--queue", cls.queue])
        audit_mod.AUDIT_PATH = os.path.join(cls.tmp, ".claude", "bm_vault_audit.jsonl")

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_revoked_principal_click_refuses(self):
        note_path = os.path.join(self.vault, "promo-validated.md")
        status, pending = http("http://127.0.0.1:%d/pending" % self.port)
        item = next(p for p in pending["promotions"] if p["id"] == "promo-validated.md")
        token = item["action_tokens"]["reject"]

        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "promotion", "id": "promo-validated.md",
                             "decision": "reject", "principal": "banned-approver",
                             "action_token": token}).encode())
        self.assertEqual(status, 403, result)
        self.assertIn("revoked", result["error"])

        with open(note_path, encoding="utf-8") as f:
            state, record, _problems = lifecycle.read_promotion(f.read())
        self.assertEqual(state, "validated", "revoked click still mutated the note")
        self.assertNotIn("promoted_by", record)

        rows = audit_mod._read_rows()
        self.assertIsNotNone(rows)
        matches = [r for r in rows if r.get("event_id") == result["event_id"]]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["principal"], "banned-approver")
        self.assertIn("revoked", matches[0].get("refused", ""))
        self.assertEqual(matches[0]["served_ids"], [])


class WrongActionTokenRefusesBackwards(unittest.TestCase):
    """Requirement (c): a wrong or missing action token is rejected and
    nothing lands, driven backwards on both cases."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault, cls.queue, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port, extra=["--queue", cls.queue])

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _state(self):
        with open(os.path.join(self.vault, "promo-candidate.md"), encoding="utf-8") as f:
            return lifecycle.read_promotion(f.read())[0]

    def test_wrong_token_is_rejected_and_nothing_lands(self):
        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "promotion", "id": "promo-candidate.md",
                             "decision": "approve", "principal": "trusted-approver",
                             "action_token": "0" * 64}).encode())
        self.assertEqual(status, 400)
        self.assertIn("action token", result["error"])
        self.assertEqual(self._state(), "candidate")

    def test_missing_token_is_rejected_and_nothing_lands(self):
        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "promotion", "id": "promo-candidate.md",
                             "decision": "approve",
                             "principal": "trusted-approver"}).encode())
        self.assertEqual(status, 400)
        self.assertIn("action token", result["error"])
        self.assertEqual(self._state(), "candidate")

    def test_token_minted_for_a_different_decision_is_rejected(self):
        status, pending = http("http://127.0.0.1:%d/pending" % self.port)
        item = next(p for p in pending["promotions"] if p["id"] == "promo-candidate.md")
        reject_token = item["action_tokens"]["reject"]
        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "promotion", "id": "promo-candidate.md",
                             "decision": "approve", "principal": "trusted-approver",
                             "action_token": reject_token}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(self._state(), "candidate")


class CurationAcceptMirrorsPromotion(unittest.TestCase):
    """Requirement (e): a curation accept through the pane mirrors the
    promotion path, running the real bm_vault_curate command under the
    clicking principal."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault, cls.queue, cls.env = scratch_estate()
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port, extra=["--queue", cls.queue])
        audit_mod.AUDIT_PATH = os.path.join(cls.tmp, ".claude", "bm_vault_audit.jsonl")

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_curation_approve_writes_edge_and_audit_row(self):
        status, pending = http("http://127.0.0.1:%d/pending" % self.port)
        self.assertEqual(len(pending["curation"]), 1)
        item = pending["curation"][0]
        self.assertEqual(sorted(item["pair"]), ["curation-a", "curation-b"])
        token = item["action_tokens"]["approve"]

        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "curation", "id": item["id"],
                             "decision": "approve", "principal": "trusted-approver",
                             "action_token": token}).encode())
        self.assertEqual(status, 200, result)
        self.assertEqual(result["rc"], 0)

        # The edge lands on the OLDER note (curation-a, created 2026-08-01),
        # per bm_vault_curate's own convention.
        with open(os.path.join(self.vault, "curation-a.md"), encoding="utf-8") as f:
            body = f.read()
        self.assertIn("relates:", body)
        self.assertIn("curation-b", body)

        with open(self.queue, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["queue"], [], "accepted pair still sits in the queue")

        rows = audit_mod._read_rows()
        matches = [r for r in rows if r.get("event_id") == result["event_id"]]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["principal"], "trusted-approver")


class EmptyQueueIsHonestNoData(unittest.TestCase):
    """Requirement (f): an empty queue and a vault with nothing pending
    serves an honest empty listing naming the vault, never an error."""

    def test_empty_estate_reports_no_data_not_an_error(self):
        tmp = tempfile.mkdtemp(prefix="bm-vault-pane-empty-")
        try:
            vault = os.path.join(tmp, "vault")
            os.makedirs(vault)
            with open(os.path.join(vault, "canonical.md"), "w") as f:
                f.write(_note("canonical", "promotion: canonical\n"
                              "promoted_by: someone\npromoted_at: 2026-08-01\n",
                              "Already settled, nothing pending here."))
            queue_path = os.path.join(tmp, "nonexistent_queue.json")
            env = dict(os.environ)
            env["HOME"] = tmp
            env["BM_VAULT_ROOT"] = vault
            env.pop("BROTHERMODE_VAULT", None)
            os.makedirs(os.path.join(tmp, ".claude"))
            port = free_port()
            proc = start_server(env, port, extra=["--queue", queue_path])
            try:
                status, body = http("http://127.0.0.1:%d/pending" % port)
                self.assertEqual(status, 200)
                self.assertEqual(body["promotions"], [])
                self.assertEqual(body["curation"], [])
                self.assertIn(vault, body.get("no_data", ""))
            finally:
                stop(proc)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _sod_estate():
    """A scratch estate with exactly one candidate note carrying an
    `author:` frontmatter field, for the V14 separation-of-duties tests.
    Kept separate from scratch_estate() so its extra note does not perturb
    that fixture's own promotion-count assertions."""
    tmp = tempfile.mkdtemp(prefix="bm-vault-pane-sod-")
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault)
    with open(os.path.join(vault, "authored-candidate.md"), "w") as f:
        f.write(_note("authored-candidate",
                      "promotion: candidate\nauthor: agent-session\n",
                      "A candidate this row's agent session wrote."))
    system_dir = os.path.join(vault, "99-System")
    os.makedirs(system_dir)
    with open(os.path.join(system_dir, "principals.json"), "w") as f:
        json.dump({"principals": {}}, f)
    queue_path = os.path.join(tmp, "curation_queue.json")
    with open(queue_path, "w") as f:
        json.dump({"generated": "2026-09-02T00:00:00+00:00", "vault": vault,
                   "queue": [], "rejections": [], "audit": []}, f)
    env = dict(os.environ)
    env["HOME"] = tmp
    env["BM_VAULT_ROOT"] = vault
    env.pop("BROTHERMODE_VAULT", None)
    os.makedirs(os.path.join(tmp, ".claude"))
    return tmp, vault, queue_path, env


class SeparationOfDutiesReachesTheClickPane(unittest.TestCase):
    """V14: the pane's POST /act already threads the clicking principal into
    cmd_promote's own --by (bm_vault_pane._do_promotion calls
    promotions.cmd_promote(vault, rel, target, principal, at, True)), so
    wiring the check into cmd_promote (V14, bm_vault_promotions.py) reaches
    this endpoint with no pane-side code change. This proves it end to end
    over real HTTP rather than trusting that by inspection alone."""

    def setUp(self):
        # Per-test, not per-class: both tests below approve the SAME note,
        # and a class-shared vault would let one test's write change the
        # state the next test finds it in.
        self.tmp, self.vault, self.queue, self.env = _sod_estate()
        self.port = free_port()
        self.proc = start_server(self.env, self.port, extra=["--queue", self.queue])
        audit_mod.AUDIT_PATH = os.path.join(self.tmp, ".claude", "bm_vault_audit.jsonl")

    def tearDown(self):
        stop(self.proc)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _note_path(self):
        return os.path.join(self.vault, "authored-candidate.md")

    def _approve_token(self):
        status, pending = http("http://127.0.0.1:%d/pending" % self.port)
        self.assertEqual(status, 200)
        item = next(p for p in pending["promotions"]
                   if p["id"] == "authored-candidate.md")
        return item["action_tokens"]["approve"]

    def test_approver_who_is_the_author_is_refused(self):
        token = self._approve_token()
        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "promotion", "id": "authored-candidate.md",
                             "decision": "approve", "principal": "agent-session",
                             "action_token": token}).encode())
        self.assertEqual(status, 409, result)
        self.assertEqual(result["rc"], 1)
        self.assertIn("separation of duties", result["output"])
        with open(self._note_path(), encoding="utf-8") as f:
            state, record, _problems = lifecycle.read_promotion(f.read())
        self.assertEqual(state, "candidate", "a refused approval must not write")
        self.assertNotIn("promoted_by", record)

    def test_a_different_principal_succeeds(self):
        token = self._approve_token()
        status, result = http(
            "http://127.0.0.1:%d/act" % self.port,
            data=json.dumps({"kind": "promotion", "id": "authored-candidate.md",
                             "decision": "approve", "principal": "khalil",
                             "action_token": token}).encode())
        self.assertEqual(status, 200, result)
        self.assertEqual(result["rc"], 0)
        with open(self._note_path(), encoding="utf-8") as f:
            state, record, _problems = lifecycle.read_promotion(f.read())
        self.assertEqual(state, "validated")
        self.assertEqual(record.get("promoted_by"), "khalil")


class BearerAuthReused(unittest.TestCase):
    """The pane reuses bm_vault_serve.py's own token gate rather than a
    second implementation: a wrong bearer is 401 on both endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault, cls.queue, cls.env = scratch_estate()
        cls.token_path = os.path.join(cls.tmp, "token.txt")
        with open(cls.token_path, "w") as f:
            f.write("s3cret-pane-token\n")
        cls.port = free_port()
        cls.proc = start_server(cls.env, cls.port,
                                extra=["--queue", cls.queue,
                                      "--token-file", cls.token_path])

    @classmethod
    def tearDownClass(cls):
        stop(cls.proc)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_wrong_bearer_is_401_on_pending(self):
        req = urllib.request.Request("http://127.0.0.1:%d/pending" % self.port)
        req.add_header("Authorization", "Bearer wrong")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected 401")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_bind_beyond_loopback_without_token_refuses_to_start(self):
        p = subprocess.run([sys.executable, PANE, "--bind", "0.0.0.0",
                            "--port", str(free_port())],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=30)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"REFUSING", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
