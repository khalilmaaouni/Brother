"""Mutation test: is the cross-tenant ROUTING seam actually guarded by any
check, or does it only look guarded? VAULT-HARDENING-SCOPE-2026-08-31.md,
V3(a).

WHY THIS EXISTS. test_tenancy_isolation.py's own backwards class
(TheProofCatchesACollapsedSeam) drives the FIXTURE backwards: it aliases
tenant-b's directory onto tenant-a's on disk (a symlink), which proves the
isolation CHECK notices two tenant names sharing one real directory. It
never touches the ROUTING CODE itself -- bm_vault_context.tenant_env, the
function that turns a request's "tenant" string into the subprocess
environment (HOME, BM_VAULT_ROOT) a recall actually runs under (see that
module's own docstring for why this indirection exists: bm_vault.py's
index, ledger and audit paths are all hardcoded off HOME, not off
BM_VAULT_ROOT alone). A bug IN that function -- resolving the wrong
tenant, or silently ignoring the requested one -- was never proven to turn
anything red. This file closes that gap: it mutates the routing function
itself, in a throwaway copy, and demands the SAME isolation assertion
catch it.

BOUNDARY USED: the served HTTP boundary (bm_vault_serve.py POST /recall),
identical to test_tenancy_isolation.py's own leakage proof, because a
routing bug that never reaches a served request is not a routing bug this
estate's product actually has.

THE MUTATION: bm_vault_context.py's tenant_env resolves
`home = os.path.join(tenants_root, tenant)` -- the one line that reads the
REQUESTED tenant string at all. Mutated to
`os.path.join(tenants_root, "tenant-a")`, ignoring the request and always
resolving tenant-a: a router that "resolves the wrong tenant" in the
plainest possible sense (VAULT-HARDENING-SCOPE-2026-08-31.md's own
phrasing for this class of bug). Applied to a scratch COPY of
scripts/fixtures/bmu_vault_seam/, never to the checked-in fixture itself
(PROVENANCE.md forbids editing it to make a test pass); if the target line
is not found exactly once, the mutation refuses to apply rather than
silently mutating nothing and reporting a false pass.

DRIVEN BACKWARDS, the whole point of this file: the SAME leak assertion
(tenant-b must never see tenant-a's canary) runs first against the intact
seam (must hold) and then against the mutated seam (must NOT hold -- a
genuine cross-tenant leak occurs, and the check must report it). If the
mutated run stays green, the routing seam is not actually guarded by any
test that exists, and this file's own main() says so as the finding
instead of a false PASS.

Exit contract, same shape as every check_all.sh suite: 0 both runs behaved
as required (intact holds, mutated leaks and is caught), 1 either run
misbehaved (including "the mutation did not turn the check red", the
central finding this file exists to catch), 2 NO-DATA (the fixture or the
mutation build could not be exercised at all).

Python 3, stdlib only. No network beyond the loopback servers this file
starts and stops itself. No em or en dashes anywhere in this file.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SEAM_DIR = os.path.join(HERE, "fixtures", "bmu_vault_seam")

sys.path.insert(0, HERE)
import test_tenancy_isolation as _tti  # noqa: E402  reused: _seam_present,
# _provision_tenant, _free_port, _wait_health, _recall -- generic helpers not
# bound to which seam copy is being served, so reusing them here can never
# drift from the sibling suite's own idea of how a tenant is provisioned.

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

#: The one line in bm_vault_context.py that reads the REQUESTED tenant
#: string. Matched literally (not a regex) so an unrelated refactor that
#: changes this line's exact text is caught as "mutation target not found"
#: rather than silently mutating something else.
TARGET_LINE = "    home = os.path.join(tenants_root, tenant)\n"
MUTATED_LINE = (
    "    home = os.path.join(tenants_root, \"tenant-a\")  "
    "# MUTATED by test_tenancy_routing_mutation.py: router ignores the "
    "requested tenant and always resolves tenant-a\n")


def _build_mutated_seam(tmp):
    """(seam_dir, error). A scratch copy of the seam with bm_vault_context.py's
    tenant_env forced to ignore the requested tenant and always resolve
    "tenant-a" -- the routing mutation this file exists to prove is caught.
    error is None on success, or a NO-DATA reason naming why the mutation
    could not be applied (the target line is not there, or is not unique)
    -- never a silent no-op mutation that would make the backwards run
    meaningless."""
    dest = os.path.join(tmp, "mutated_seam")
    shutil.copytree(SEAM_DIR, dest)
    ctx_path = os.path.join(dest, "bm_vault_context.py")
    try:
        with open(ctx_path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as e:
        return None, "NO-DATA: could not read %s to mutate it: %s" % (ctx_path, e)
    count = src.count(TARGET_LINE)
    if count != 1:
        return None, (
            "NO-DATA: mutation target line not found exactly once in "
            "bm_vault_context.py (found %d); the routing seam has changed "
            "shape and this file's mutation needs updating to match, never "
            "silently skipped" % count)
    with open(ctx_path, "w", encoding="utf-8") as fh:
        fh.write(src.replace(TARGET_LINE, MUTATED_LINE))
    return dest, None


def _start_server(bm_vault_serve, tenants_root, port):
    argv = [sys.executable, bm_vault_serve, "--bind", "127.0.0.1",
            "--port", str(port), "--enterprise", "--tenants-root", tenants_root]
    return subprocess.Popen(argv, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)


def run_routing_probe(seam_dir):
    """(ok, lines). ok True: the seam at seam_dir isolated the two tenants
    (no cross-tenant leak observed). ok False: a leak occurred. None means
    NO-DATA (the server or fixture could not be exercised). Mirrors
    test_tenancy_isolation.py's own run_leakage_proof, parametrized on WHICH
    seam directory provisions and serves the tenants, so the identical leak
    assertion runs against both the intact fixture and a mutated copy."""
    bm_vault_serve = os.path.join(seam_dir, "bm_vault_serve.py")
    lines = []
    tmp = tempfile.mkdtemp(prefix="bm-routing-mutation-run-")
    server = None
    try:
        tenants_root = os.path.join(tmp, "tenants")
        os.makedirs(tenants_root)
        canary_a = "CANARY-A-%s" % uuid.uuid4().hex[:12]
        canary_b = "CANARY-B-%s" % uuid.uuid4().hex[:12]
        # Indexing never touches bm_vault_context.py (only the served path
        # does), so provisioning through the sibling suite's own bm_vault.py
        # copy is equivalent regardless of which seam serves this run.
        _tti._provision_tenant(tenants_root, "tenant-a", canary=canary_a)
        _tti._provision_tenant(tenants_root, "tenant-b", canary=canary_b)

        port = _tti._free_port()
        server = _start_server(bm_vault_serve, tenants_root, port)
        if not _tti._wait_health(port):
            out = server.stdout.read().decode("utf-8", "replace") if server.stdout else ""
            return None, ["NO-DATA: server at %s never became healthy: %s"
                          % (bm_vault_serve, out.strip())]

        body_a = dict(query=canary_a, tenant="tenant-a", identity="human1", limit=10)
        status, raw, _parsed = _tti._recall(port, body_a)
        a_sees_a = status == 200 and canary_a in raw
        lines.append("%s tenant-a recalls its own canary (%d, %s)"
                    % ("ok " if a_sees_a else "FAIL", status,
                       "found" if canary_a in raw else "absent"))

        body_b_asks_a = dict(query=canary_a, tenant="tenant-b", identity="human1", limit=10)
        status, raw, _parsed = _tti._recall(port, body_b_asks_a)
        b_leaks_a = canary_a in raw
        lines.append("%s tenant-b never sees tenant-a's canary (%d, %s)"
                    % ("FAIL" if b_leaks_a else "ok ", status,
                       "LEAKED" if b_leaks_a else "absent, as required"))

        ok = a_sees_a and not b_leaks_a
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


class RoutingSeamHoldsIntact(unittest.TestCase):
    """The unmutated seam: the routing function reads the request's own
    tenant field, so no leak occurs. Same assertion the mutated case below
    must FAIL to satisfy."""

    @unittest.skipUnless(_tti._seam_present(), "bmu_vault_seam fixture is absent")
    def test_intact_routing_holds(self):
        ok, lines = run_routing_probe(SEAM_DIR)
        self.assertIsNotNone(ok, "\n".join(lines))
        self.assertTrue(ok, "\n".join(lines))


class TheMutationProvesTheSeamIsGuarded(unittest.TestCase):
    """Driven backwards, the central claim of this file: force the routing
    function to ignore the requested tenant, and the identical leak
    assertion above must now report the leak. If it does not, the routing
    seam is not actually guarded by any check that exists."""

    @unittest.skipUnless(_tti._seam_present(), "bmu_vault_seam fixture is absent")
    def test_mutated_routing_is_caught_as_a_leak(self):
        tmp = tempfile.mkdtemp(prefix="bm-routing-mutation-build-")
        try:
            seam_dir, error = _build_mutated_seam(tmp)
            if seam_dir is None:
                self.fail(error)
            ok, lines = run_routing_probe(seam_dir)
            self.assertIsNotNone(ok, "\n".join(lines))
            self.assertFalse(
                ok, "mutated routing (ignoring the requested tenant) must be "
                    "caught as a leak, not pass:\n" + "\n".join(lines))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    if not _tti._seam_present():
        print("NO-DATA: %s is missing one or more of the seam files" % SEAM_DIR)
        return 2

    print("-- intact routing (scripts/fixtures/bmu_vault_seam) --")
    ok, lines = run_routing_probe(SEAM_DIR)
    if ok is None:
        print("\n".join(lines))
        return 2
    for line in lines:
        print(line)
    if not ok:
        print("NO-DATA: the INTACT seam itself leaked; that is a fixture or "
              "provisioning problem, not evidence about the mutation below")
        return 2

    build_tmp = tempfile.mkdtemp(prefix="bm-routing-mutation-build-")
    try:
        seam_dir, error = _build_mutated_seam(build_tmp)
        if seam_dir is None:
            print(error)
            return 2

        print("-- mutated routing (tenant_env forced to always resolve "
              "tenant-a) --")
        ok2, lines2 = run_routing_probe(seam_dir)
        if ok2 is None:
            print("\n".join(lines2))
            return 2
        for line in lines2:
            print(line)

        if ok2:
            print("FINDING: the routing mutation did NOT turn the isolation "
                  "check red. The cross-tenant routing seam is not actually "
                  "guarded by any check that exists.")
            print(FAIL + " exit 1 test_tenancy_routing_mutation")
            return 1

        print("the mutation correctly turned the isolation check red: the "
              "cross-tenant routing seam IS guarded by this proof")
        print(PASS + " exit 0 test_tenancy_routing_mutation")
        return 0
    finally:
        shutil.rmtree(build_tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--unittest" in sys.argv[1:]:
        sys.argv.remove("--unittest")
        unittest.main()
    else:
        sys.exit(main())
