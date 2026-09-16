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
import threading
import time
import unittest
import uuid

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

try:  # noqa: E402
    from export_public import gate_timeout as _gate_timeout
except ImportError:
    # Same reasoning as the tmp_sandbox guard above: a packager can copy this
    # test without export_public.py beside it. Fall back to the unscaled floor
    # rather than dying; the budget is plumbing here, never the subject.
    def _gate_timeout(load15=None, cores=None, floor=None, cap=None):
        return HEALTH_FLOOR_SECONDS if floor is None else floor

HERE = os.path.dirname(os.path.abspath(__file__))
SEAM_DIR = os.path.join(HERE, "fixtures", "bmu_vault_seam")
BM_VAULT = os.path.join(SEAM_DIR, "bm_vault.py")
BM_VAULT_SERVE = os.path.join(SEAM_DIR, "bm_vault_serve.py")

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

#: Seconds the served boundary gets to answer GET /health on an UNLOADED
#: machine. Measured: this whole file printed PASS exit 0 in under 100 s on an
#: idle host, health inside a second. The floor is scaled up by 15 minute load
#: over core count (_gate_timeout, reused from export_public.py) because the
#: machine this suite runs on routinely carries other lanes: on 2026-09-15 the
#: readiness gate sat here 49 minutes at load 400, and a fixed 10 seconds is a
#: budget for an idle host quoted at a busy one.
HEALTH_FLOOR_SECONDS = 10
HEALTH_CAP_SECONDS = 300
#: Seconds to collect a failed server's own output once it has been asked to
#: die. Only the report path uses it; nothing waits on it to decide a verdict.
SERVER_OUTPUT_SECONDS = 10


def health_timeout():
    """The seconds _wait_health gets, read at CALL TIME (never bound as a
    default argument, which would freeze the load at import time)."""
    return _gate_timeout(floor=HEALTH_FLOOR_SECONDS, cap=HEALTH_CAP_SECONDS)


def server_output(server, timeout=SERVER_OUTPUT_SECONDS):
    """Whatever the server printed, read under a deadline.

    THE DEADLOCK THIS REPLACES: the caller reaches here holding a child that
    is ALIVE (it just missed its health deadline) and still holds the write
    end of this pipe open. A bare server.stdout.read() waits for EOF, and EOF
    on that pipe means "the server exited", which a merely slow server never
    does. So the read blocks forever, burning no CPU, and the suite reads as a
    hang instead of the NO-DATA it was trying to report: exactly the 49 minute
    wedge observed on 2026-09-15 at load 400. Ask the child to die FIRST, then
    let communicate() enforce a bound on the read that follows."""
    if server.stdout is None:
        return ""
    server.terminate()
    for _ in range(2):  # terminate, then kill
        try:
            out, _unused = server.communicate(timeout=timeout)
            return out.decode("utf-8", "replace") if out else ""
        except subprocess.TimeoutExpired:
            server.kill()
    return "(server output unreadable: the process outlived both terminate and kill)"


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


def _wait_health(port, timeout=None):
    """True once GET /health answers 200, False once the budget is spent.
    `timeout` is read at call time when left None so the budget reflects the
    load at the moment the server is actually started."""
    if timeout is None:
        timeout = health_timeout()
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
        budget = health_timeout()
        if not _wait_health(port, timeout=budget):
            out = server_output(server)
            return None, ["NO-DATA: server never became healthy within %d s: %s"
                          % (budget, out.strip())]

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


def _slow_server_stub():
    """A stand-in for a server whose startup was starved of CPU: alive, holding
    the write end of its stdout open, and never answering health. No fixture
    needed, so this proof runs even where the vendored seam is absent."""
    return subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.stdout.write('starting\\n'); "
         "sys.stdout.flush(); time.sleep(600)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _read_returns_within(reader, server, seconds):
    """(returned, elapsed) for `reader(server)` run on a side thread that is
    joined with a deadline. A thread, not a signal or a subprocess: the whole
    question is whether the CALLING thread can be pinned by a blocking read,
    and only a second thread can observe that from outside."""
    box = {}

    def run():
        try:
            box["out"] = reader(server)
        except Exception as exc:  # noqa: BLE001
            box["out"] = "raised: %s" % exc

    t = threading.Thread(target=run, daemon=True)
    started = time.time()
    t.start()
    t.join(seconds)
    return not t.is_alive(), time.time() - started


def run_slow_server_report_is_bounded_proof():
    """(ok, lines). The 2026-09-15 wedge as a check.

    Driven backwards first, exactly as TheProofCatchesACollapsedSeam does for
    leakage: the OLD shape (a bare .read() on a live child's stdout) must be
    shown to still be pinned after the bound, or this proof is asserting
    nothing and would stay green if server_output were reverted. Then the
    shipped server_output must come back inside that same bound."""
    lines = []
    bound = 8.0
    old = _slow_server_stub()
    try:
        returned, elapsed = _read_returns_within(
            lambda s: s.stdout.read().decode("utf-8", "replace"), old, bound)
        old_hangs = not returned
        lines.append("%s the old bare stdout.read() is still pinned after %.0f s "
                     "on a live, slow server (the 2026-09-15 wedge reproduced)"
                     % ("ok " if old_hangs else "FAIL", bound))
    finally:
        old.kill()
        old.wait(timeout=5)
        if old.stdout is not None:
            old.stdout.close()

    new = _slow_server_stub()
    try:
        returned, elapsed = _read_returns_within(server_output, new, bound + 20)
        new_bounded = returned and elapsed < bound + 20
        lines.append("%s server_output returns in %.1f s instead of hanging, so the "
                     "health failure reports NO-DATA rather than wedging"
                     % ("ok " if new_bounded else "FAIL", elapsed))
    finally:
        new.kill()
        new.wait(timeout=5)
        if new.stdout is not None:
            new.stdout.close()

    return (old_hangs and new_bounded), lines


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

    def test_a_starved_server_is_reported_not_waited_on_forever(self):
        """No skipUnless: the stub is this file's own, so the wedge stays
        covered even where the vendored seam is absent."""
        ok, lines = run_slow_server_report_is_bounded_proof()
        self.assertTrue(ok, "\n".join(lines))

    @unittest.skipUnless(_seam_present(), "bmu_vault_seam fixture is absent")
    def test_collapsed_isolation_is_caught_as_a_leak(self):
        ok, lines = run_leakage_proof(collapse=True)
        self.assertIsNotNone(ok, "\n".join(lines))
        self.assertFalse(ok, "collapsing tenant-b onto tenant-a's directory must be "
                            "caught as a leak, not pass:\n" + "\n".join(lines))


def main():
    # Runs before the seam check: it needs no fixture, and a suite that can
    # wedge is worth knowing about even when the seam is missing.
    ok, lines = run_slow_server_report_is_bounded_proof()
    for line in lines:
        print(line)
    wedge_ok = ok

    if not _seam_present():
        print("NO-DATA: %s is missing one or more of bm_vault.py, "
              "bm_vault_serve.py, bm_vault_context.py, bm_vault_policy.py; "
              "see PROVENANCE.md in that directory" % SEAM_DIR)
        return 2
    overall_ok = wedge_ok
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
