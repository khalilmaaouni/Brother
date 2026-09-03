"""Black-box proof: two tenants never mix, VB3-03 (WBS row, docs/plan/VAULT-WBS-V2-2026-08-29.json).

WHY THIS EXISTS. scripts/readiness_gate.py's "tenancy-leakage-zero" row reads
NO-DATA today because "VB3-03 landed in BrotherModeUp (PR 159); the
Brother-side evidence suite that proves it from this repository is queued."
This file is that suite. It does NOT re-run BrotherModeUp's own unit tests
(six hostile acceptance personas already refused that shape as vendor
marking vendor's own homework, docs/plan/TRIAL-CHALLENGE-BOARD-2026-08-31.md
row 4); it drives the vendored, frozen product boundary from OUTSIDE, the
way a buyer would: real HTTP requests against a real bm_vault_serve.py
process, real subprocess CLI recalls, real files on disk.

BOUNDARY USED: the SERVED HTTP boundary (bm_vault_serve.py's POST /recall
and GET /health) for the two cross-tenant leakage checks and the missing-
context refusal, because VB3-03's own title is "the served endpoint is
where the context boundary enters" -- that IS the surface under test. The
agent-narrower-than-human check uses the CLI boundary (bm_vault.py recall
--identity/--agent-identity) instead, because the wire protocol
(bm_vault_serve.py, read start to finish before this was written) only
forwards a single "identity" field and never carries a second agent
principal -- dual principals are a VB3-04 CLI capability the wire has not
yet exposed. Both are the real product's own entry points, never a helper
this file invents.

THE FIXTURE, "two vault roots" isolation exactly as bm_vault_context.py's
own docstring describes it (scripts/fixtures/bmu_vault_seam/bm_vault_context.py):
one tenants-root directory holding <tenant>/vault and <tenant>/.claude per
tenant, pre-provisioned before the server ever starts. Each tenant's vault
gets one note carrying a canary string unique to that tenant; the proof
never asserts on parsed rows alone, always ALSO greps the full raw response
text (JSON body, not just the "rows" list) for the other tenant's canary,
because a leak through some field this parser does not know about would
still be a leak.

DRIVEN BACKWARDS (mandatory per the B-wave brief): TenancyIsolationHolds
proves the intact seam is clean; TheProofCatchesACollapsedSeam then
provisions "tenant-b" as a symlink onto tenant-a's own directory (a fixture-
level collapse of the two-root isolation, not a patch to the vendored
product code -- the product is never edited to make a test pass, see
scripts/fixtures/bmu_vault_seam/PROVENANCE.md) and asserts the SAME leak
check that just passed now correctly reports the leak and returns not-ok.
A check that cannot fail on a genuinely mixed tenant is not proving
anything; this class exists so removing or weakening a leak assertion here
regresses visibly.

Exit contract for scripts/readiness_gate.py and scripts/check_all.sh, same
shape as every other suite either already registers: 0 all assertions pass,
1 an assertion failed, 2 NO-DATA (the fixture itself could not be exercised
-- a port never became free, a subprocess never started -- never guessed as
a pass or a product failure).

Python 3, stdlib only. No network beyond the loopback server this file
starts and stops itself. No em or en dashes anywhere in this file.
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SEAM_DIR = os.path.join(HERE, "fixtures", "bmu_vault_seam")
BM_VAULT = os.path.join(SEAM_DIR, "bm_vault.py")
BM_VAULT_SERVE = os.path.join(SEAM_DIR, "bm_vault_serve.py")

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"


def _seam_present():
    return all(os.path.isfile(p) for p in (
        BM_VAULT, BM_VAULT_SERVE,
        os.path.join(SEAM_DIR, "bm_vault_context.py"),
        os.path.join(SEAM_DIR, "bm_vault_policy.py")))


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _provision_tenant(tenants_root, name, canary=None, alias_of=None):
    """Create <tenants_root>/<name>/{vault,.claude}, index one note carrying
    `canary` into it, and return its real directory -- OR, when alias_of is
    given, make `name` a symlink onto that OTHER tenant's real directory and
    index nothing new (the deliberate backwards collapse: two tenant NAMES,
    one actual root, exactly what VB3-03's isolation exists to prevent)."""
    if alias_of is not None:
        link = os.path.join(tenants_root, name)
        os.symlink(alias_of, link)
        return alias_of
    home = os.path.join(tenants_root, name)
    vault = os.path.join(home, "vault")
    state = os.path.join(home, ".claude")
    os.makedirs(vault)
    os.makedirs(state)
    # The canary rides in the note's `name:` frontmatter, not the body: bm_vault.py's
    # own _print_hits prints a hit's title and path, never its body text (confirmed by
    # reading the recall output produced against this exact fixture), so a canary
    # planted only in the body would never appear in anything this proof can observe
    # and every leak check below would be checking nothing.
    with open(os.path.join(vault, "note.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\n---\n\nan ordinary lesson body, nothing special.\n"
                  % canary)
    env = dict(os.environ, HOME=home, BM_VAULT_ROOT=vault)
    p = subprocess.run([sys.executable, BM_VAULT, "index"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env=env, timeout=60)
    if p.returncode != 0:
        raise RuntimeError("indexing tenant %r failed (exit %d): %s"
                           % (name, p.returncode, p.stdout.decode("utf-8", "replace")))
    return home


def _wait_health(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            con = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            con.request("GET", "/health")
            resp = con.getresponse()
            resp.read()
            con.close()
            if resp.status == 200:
                return True
        except (ConnectionRefusedError, OSError, http.client.HTTPException):
            pass
        time.sleep(0.1)
    return False


def _recall(port, body, timeout=10):
    con = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    payload = json.dumps(body).encode("utf-8")
    con.request("POST", "/recall", body=payload,
               headers={"Content-Type": "application/json"})
    resp = con.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    con.close()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    return resp.status, raw, parsed


def _start_server(tenants_root, port):
    return subprocess.Popen(
        [sys.executable, BM_VAULT_SERVE, "--bind", "127.0.0.1", "--port", str(port),
         "--enterprise", "--tenants-root", tenants_root],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def run_leakage_proof(collapse=False):
    """(ok, lines). Full setup, exercise, teardown, one call. collapse=True
    is the backwards mode: tenant-b aliases tenant-a's real directory
    instead of getting its own, so a genuine cross-tenant leak occurs and
    this same check must report it."""
    lines = []
    ok = True
    tmp = tempfile.mkdtemp(prefix="bm-tenancy-proof-")
    server = None
    try:
        tenants_root = os.path.join(tmp, "tenants")
        os.makedirs(tenants_root)
        canary_a = "CANARY-A-%s" % uuid.uuid4().hex[:12]
        canary_b = "CANARY-B-%s" % uuid.uuid4().hex[:12]
        dir_a = _provision_tenant(tenants_root, "tenant-a", canary=canary_a)
        if collapse:
            _provision_tenant(tenants_root, "tenant-b", alias_of=dir_a)
        else:
            _provision_tenant(tenants_root, "tenant-b", canary=canary_b)

        port = _free_port()
        server = _start_server(tenants_root, port)
        if not _wait_health(port):
            out = server.stdout.read().decode("utf-8", "replace") if server.stdout else ""
            return None, ["NO-DATA: server never became healthy: %s" % out.strip()]

        status, raw, parsed = _recall(port, {"query": canary_a, "tenant": "tenant-a",
                                             "identity": "human1", "limit": 10})
        a_sees_a = status == 200 and canary_a in raw
        lines.append("%s tenant-a recalls its own canary (%d, %s)"
                    % ("ok " if a_sees_a else "FAIL", status,
                       "found" if canary_a in raw else "absent"))
        ok = ok and a_sees_a

        status, raw, parsed = _recall(port, {"query": canary_a, "tenant": "tenant-b",
                                             "identity": "human1", "limit": 10})
        b_leaks_a = canary_a in raw
        lines.append("%s tenant-b never sees tenant-a's canary (%d, %s)"
                    % ("FAIL" if b_leaks_a else "ok ", status,
                       "LEAKED" if b_leaks_a else "absent, as required"))
        ok = ok and not b_leaks_a

        if not collapse:
            status, raw, parsed = _recall(port, {"query": canary_b, "tenant": "tenant-b",
                                                 "identity": "human1", "limit": 10})
            b_sees_b = status == 200 and canary_b in raw
            lines.append("%s tenant-b recalls its own canary (%d, %s)"
                        % ("ok " if b_sees_b else "FAIL", status,
                           "found" if canary_b in raw else "absent"))
            ok = ok and b_sees_b

            status, raw, parsed = _recall(port, {"query": canary_b, "tenant": "tenant-a",
                                                 "identity": "human1", "limit": 10})
            a_leaks_b = canary_b in raw
            lines.append("%s tenant-a never sees tenant-b's canary (%d, %s)"
                        % ("FAIL" if a_leaks_b else "ok ", status,
                           "LEAKED" if a_leaks_b else "absent, as required"))
            ok = ok and not a_leaks_b

        # Enterprise mode refuses a recall missing tenant or principal, rather
        # than silently answering broad.
        status, raw, parsed = _recall(port, {"query": canary_a, "identity": "human1"})
        missing_tenant_refused = status == 400 and isinstance(parsed, dict) \
            and "tenant" in (parsed.get("missing") or [])
        lines.append("%s missing-tenant recall is refused (400), not silently served (%d)"
                    % ("ok " if missing_tenant_refused else "FAIL", status))
        ok = ok and missing_tenant_refused

        status, raw, parsed = _recall(port, {"query": canary_a, "tenant": "tenant-a"})
        missing_principal_refused = status == 400 and isinstance(parsed, dict) \
            and "principal" in (parsed.get("missing") or [])
        lines.append("%s missing-principal recall is refused (400), not silently served (%d)"
                    % ("ok " if missing_principal_refused else "FAIL", status))
        ok = ok and missing_principal_refused

        return ok, lines
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            if server.stdout is not None:
                server.stdout.close()
        shutil.rmtree(tmp, ignore_errors=True)


def run_agent_narrower_than_human_proof():
    """(ok, lines) over the CLI recall boundary: a dual-principal recall
    (human + agent) is scoped to the INTERSECTION of both, so an agent
    denied by a policy rule the human is not subject to withholds the note
    even though the human alone would have seen it. bm_vault_policy.decide_dual
    (scripts/fixtures/bmu_vault_seam/bm_vault_policy.py) is the real, merged
    VB3-04 code; this test never calls it directly, only through
    bm_vault.py recall, the CLI boundary a caller actually uses."""
    lines = []
    tmp = tempfile.mkdtemp(prefix="bm-tenancy-agent-proof-")
    try:
        vault = os.path.join(tmp, "vault")
        state = os.path.join(tmp, ".claude")
        os.makedirs(vault)
        os.makedirs(state)
        os.makedirs(os.path.join(vault, "99-System"))
        canary = "CANARY-SCOPED-%s" % uuid.uuid4().hex[:12]
        with open(os.path.join(vault, "note.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\n---\n\nan ordinary lesson body, nothing special.\n"
                     % canary)
        policy = {"default": "allow",
                  "rules": [{"identity": "agent1", "path": "*", "action": "deny"}]}
        with open(os.path.join(vault, "99-System", "access-policy.json"),
                 "w", encoding="utf-8") as fh:
            json.dump(policy, fh)
        env = dict(os.environ, HOME=tmp, BM_VAULT_ROOT=vault)
        p = subprocess.run([sys.executable, BM_VAULT, "index"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env, timeout=60)
        if p.returncode != 0:
            return None, ["NO-DATA: indexing the scoped fixture failed: %s"
                          % p.stdout.decode("utf-8", "replace")]

        def recall_cli(identity, agent_identity=None):
            argv = [sys.executable, BM_VAULT, "recall", "--query", canary,
                   "--identity", identity]
            if agent_identity:
                argv += ["--agent-identity", agent_identity]
            r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=env, timeout=30)
            return r.stdout.decode("utf-8", "replace")

        human_only = recall_cli("human1")
        human_sees = canary in human_only
        lines.append("%s human alone recalls the note (allowed by default)"
                    % ("ok " if human_sees else "FAIL"))

        dual = recall_cli("human1", agent_identity="agent1")
        dual_withholds = canary not in dual
        lines.append("%s human+agent recall withholds the note (agent scoped narrower "
                    "than its human, intersection wins)"
                    % ("ok " if dual_withholds else "FAIL"))

        ok = human_sees and dual_withholds
        return ok, lines
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class TenancyIsolationHolds(unittest.TestCase):
    """The intact seam: two tenants, zero leakage in either direction, an
    agent scoped narrower than its human cannot widen the human's own
    access, and enterprise mode refuses a recall it cannot scope."""

    @unittest.skipUnless(_seam_present(), "bmu_vault_seam fixture is absent")
    def test_no_cross_tenant_leakage_and_context_is_required(self):
        ok, lines = run_leakage_proof(collapse=False)
        self.assertIsNotNone(ok, "\n".join(lines))
        self.assertTrue(ok, "\n".join(lines))

    @unittest.skipUnless(_seam_present(), "bmu_vault_seam fixture is absent")
    def test_agent_narrower_than_its_human_cannot_widen_access(self):
        ok, lines = run_agent_narrower_than_human_proof()
        self.assertIsNotNone(ok, "\n".join(lines))
        self.assertTrue(ok, "\n".join(lines))


class TheProofCatchesACollapsedSeam(unittest.TestCase):
    """Driven backwards: the fixture collapses tenant-b onto tenant-a's own
    directory (never a hand edit to the vendored product code), and the
    SAME leakage check above must now report the leak, not stay green."""

    @unittest.skipUnless(_seam_present(), "bmu_vault_seam fixture is absent")
    def test_collapsed_isolation_is_caught_as_a_leak(self):
        ok, lines = run_leakage_proof(collapse=True)
        self.assertIsNotNone(ok, "\n".join(lines))
        self.assertFalse(ok, "collapsing tenant-b onto tenant-a's directory must be "
                            "caught as a leak, not pass:\n" + "\n".join(lines))


def main():
    if not _seam_present():
        print("NO-DATA: %s is missing one or more of bm_vault.py, "
              "bm_vault_serve.py, bm_vault_context.py, bm_vault_policy.py; "
              "see PROVENANCE.md in that directory" % SEAM_DIR)
        return 2
    overall_ok = True
    ok, lines = run_leakage_proof(collapse=False)
    if ok is None:
        print("\n".join(lines))
        return 2
    for line in lines:
        print(line)
    overall_ok = overall_ok and ok

    ok, lines = run_agent_narrower_than_human_proof()
    if ok is None:
        print("\n".join(lines))
        return 2
    for line in lines:
        print(line)
    overall_ok = overall_ok and ok

    if overall_ok:
        print(PASS + " exit 0 test_tenancy_isolation")
        return 0
    print(FAIL + " exit 1 test_tenancy_isolation")
    return 1


if __name__ == "__main__":
    if "--unittest" in sys.argv[1:]:
        sys.argv.remove("--unittest")
        unittest.main()
    else:
        sys.exit(main())
