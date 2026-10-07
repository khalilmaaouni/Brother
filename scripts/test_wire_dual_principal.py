"""test_wire_dual_principal.py: does the served HTTP boundary express a dual
(human + agent) principal? VAULT-HARDENING-SCOPE-2026-08-31.md, V3(b).

WHY THIS EXISTS. The security persona's finding: VB3-04's own dual-principal
intersection guarantee (bm_vault_policy.decide_dual: an agent acting for a
human is scoped to the INTERSECTION of both principals' access, never the
union) is proven at the CLI boundary (test_tenancy_isolation.py's own
run_agent_narrower_than_human_proof, reused here unchanged as this file's
contrast baseline) but the production HTTP wire is a DIFFERENT boundary
with its own contract, and nobody had checked whether that contract can
even carry the second principal. This file checks, mechanically, and
reports the answer honestly either way -- it never adds the missing field,
per the brief ("prove present or absent", never build).

TWO INDEPENDENT PIECES OF EVIDENCE, because a probe that trusts only one
reading of the source can be wrong about which field name to look for:

  STATIC:      every field bm_vault_serve.py's do_POST reads off the
               parsed request body (via req.get("...")), read directly out
               of its own source with a regex over the literal calls, not
               asserted from memory. None of them may be an agent-shaped
               name for this evidence to say CANNOT.
  BEHAVIORAL:  the SAME single-vault, single-policy fixture
               test_tenancy_isolation.py's dual-principal proof builds
               (default allow, one rule denying agent1 on every path),
               served live over bm_vault_serve.py. A human-alone recall
               must find the note (the CLI's own baseline). A recall that
               ALSO carries a second-principal field, tried under every
               plausible spelling, must be able to WITHHOLD the note the
               way the CLI's dual-principal recall does, or no live
               behavior distinguishes a single-principal request from a
               dual one -- the wire is carrying only one identity,
               whatever a client sends it.

The known missing field is an expected failure of the positive capability
assertion. Baseline and infrastructure failures remain outside that expected
failure, so a broken fixture cannot masquerade as the known gap. An unexpected
success exits 1 and requires review of this test and its check_all.sh
comment. A standing known gap should remain machine-checked without refusing
every push that touches its test.

Never edits scripts/fixtures/bmu_vault_seam/*.py (PROVENANCE.md forbids
that); reads bm_vault_serve.py's source and drives its real HTTP boundary.

Exit 0: the baselines hold and the documented gap remains an expected failure.
This is a PASS for the regression check, never a PASS for wire capability.
Exit 1: an unexpected success requires review of the gap declaration.
Exit 2 (NO-DATA): the fixture or its baselines could not be exercised.

Python 3, stdlib only. No network beyond the loopback server this file
starts and stops itself. No em or en dashes anywhere in this file.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SEAM_DIR = os.path.join(HERE, "fixtures", "bmu_vault_seam")
BM_VAULT = os.path.join(SEAM_DIR, "bm_vault.py")
BM_VAULT_SERVE = os.path.join(SEAM_DIR, "bm_vault_serve.py")

sys.path.insert(0, HERE)
import test_tenancy_isolation as _tti  # noqa: E402  reused: _seam_present,

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
# _free_port, _wait_health, _recall, and the CLI-boundary dual-principal
# proof itself, as this file's contrast baseline.

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

#: Every plausible spelling a client might reasonably try for a second
#: ("acting as this agent") principal on the wire. Checked exhaustively so
#: a CANNOT verdict is never a guess about which name might have worked.
CANDIDATE_AGENT_FIELDS = [
    "agent_identity", "agent-identity", "agentIdentity", "as_agent", "agent",
]

REQ_GET_RE = re.compile(r'req\.get\(\s*"([^"]+)"')


def _static_fields():
    """Every request-body field name bm_vault_serve.py's do_POST reads,
    straight out of its own source (never asserted from memory)."""
    with open(BM_VAULT_SERVE, encoding="utf-8") as fh:
        src = fh.read()
    return sorted(set(REQ_GET_RE.findall(src)))


def _static_agent_field():
    """The agent-shaped field name among what the source actually reads, or
    None. Static half of the two-piece proof."""
    for f in _static_fields():
        if "agent" in f.lower():
            return f
    return None


def _build_dual_principal_vault(tmp):
    """(vault, canary). The identical fixture test_tenancy_isolation.py's
    CLI-boundary dual-principal proof builds: one vault, one note, one
    policy denying agent1 on every path with default allow."""
    vault = os.path.join(tmp, "vault")
    state = os.path.join(tmp, ".claude")
    os.makedirs(vault)
    os.makedirs(state)
    os.makedirs(os.path.join(vault, "99-System"))
    canary = "CANARY-WIRE-DUAL-%s" % uuid.uuid4().hex[:12]
    with open(os.path.join(vault, "note.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\n---\n\nan ordinary lesson body, nothing special.\n"
                 % canary)
    rule = dict(identity="agent1", path="*", action="deny")
    policy = dict(default="allow", rules=[rule])
    with open(os.path.join(vault, "99-System", "access-policy.json"),
             "w", encoding="utf-8") as fh:
        json.dump(policy, fh)
    return vault, canary


def _start_server(env, port):
    argv = [sys.executable, BM_VAULT_SERVE, "--bind", "127.0.0.1", "--port", str(port)]
    return subprocess.Popen(argv, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)


def run_wire_probe():
    """(result, lines). result is a dict with keys human_alone_sees,
    static_field, behavioral_field, can_express -- or None on NO-DATA (the
    fixture could not be exercised at all: indexing failed, the server
    never became healthy)."""
    lines = []
    tmp = tempfile.mkdtemp(prefix="bm-wire-dual-principal-")
    server = None
    try:
        vault, canary = _build_dual_principal_vault(tmp)
        env = dict(os.environ, HOME=tmp, BM_VAULT_ROOT=vault)
        p = subprocess.run([sys.executable, BM_VAULT, "index"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env, timeout=60)
        if p.returncode != 0:
            return None, ["NO-DATA: indexing the fixture failed: %s"
                          % p.stdout.decode("utf-8", "replace")]

        port = _tti._free_port()
        server = _start_server(env, port)
        budget = _tti.health_timeout()
        if not _tti._wait_health(port, timeout=budget):
            out = _tti.server_output(server)
            return None, ["NO-DATA: server never became healthy within %d s: %s"
                          % (budget, out.strip())]

        baseline_body = dict(query=canary, identity="human1")
        status, raw, _parsed = _tti._recall(port, baseline_body)
        human_alone_sees = status == 200 and canary in raw
        lines.append("%s human alone recall over the wire finds the note (%d)"
                    % ("ok " if human_alone_sees else "FAIL", status))

        static_field = _static_agent_field()
        lines.append("static: request-body fields do_POST reads: %s"
                    % ", ".join(_static_fields()))
        lines.append("static: agent-shaped field found in source: %s"
                    % (static_field or "none"))

        behavioral_field = None
        for cand in CANDIDATE_AGENT_FIELDS:
            body = dict(query=canary, identity="human1")
            body[cand] = "agent1"
            status, raw, _parsed = _tti._recall(port, body)
            withheld = not (status == 200 and canary in raw)
            lines.append("%s field %r: note %s (%d)"
                        % ("ok " if withheld else "no effect", cand,
                           "withheld (intersection applied)" if withheld
                           else "still served (field ignored)", status))
            if withheld:
                behavioral_field = cand
                break

        can_express = bool(static_field) or bool(behavioral_field)
        result = dict(human_alone_sees=human_alone_sees, static_field=static_field,
                      behavioral_field=behavioral_field, can_express=can_express)
        return result, lines
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


class WireDualPrincipalProbe(unittest.TestCase):
    """This class proves the PROBE ITSELF is discriminating: it is not the
    registered battery finding (main()'s own expected failure
    below is). The CLI boundary is the contrast baseline; the wire is
    exercised the identical way over HTTP.

    Today's expected, CORRECT result is CANNOT: assertFalse(can_express)
    below therefore PASSES today. If the wire ever gains a real
    dual-principal field, this assertion starts FAILING, which is
    deliberate -- it forces whoever adds that field to update this file
    (and the expected failure in main(), and check_all.sh's comment)
    rather than letting the gap close
    silently underneath an unmaintained probe."""

    @unittest.skipUnless(_tti._seam_present(), "bmu_vault_seam fixture is absent")
    def test_cli_boundary_enforces_the_intersection(self):
        ok, lines = _tti.run_agent_narrower_than_human_proof()
        self.assertIsNotNone(ok, "\n".join(lines))
        self.assertTrue(ok, "the CLI's own dual-principal guarantee must hold "
                            "as this file's contrast baseline:\n" + "\n".join(lines))

    @unittest.skipUnless(_tti._seam_present(), "bmu_vault_seam fixture is absent")
    def test_wire_cannot_express_a_second_principal_today(self):
        result, lines = run_wire_probe()
        self.assertIsNotNone(result, "\n".join(lines))
        self.assertTrue(result["human_alone_sees"], "\n".join(lines))
        self.assertFalse(
            result["can_express"],
            "expected CANNOT today (VAULT-HARDENING-SCOPE-2026-08-31.md "
            "V3b); if this assertion FAILS, the wire has gained a "
            "dual-principal field and this file (the expected failure "
            "in main()) and check_all.sh's comment need updating:\n"
            + "\n".join(lines))


def main():
    if not _tti._seam_present():
        print("NO-DATA: %s is missing one or more of the seam files" % SEAM_DIR)
        return 2

    print("-- CLI boundary (contrast baseline) --")
    cli_ok, cli_lines = _tti.run_agent_narrower_than_human_proof()
    if cli_ok is None:
        print("\n".join(cli_lines))
        return 2
    for line in cli_lines:
        print(line)
    if not cli_ok:
        print("NO-DATA: the CLI's own dual-principal guarantee did not hold; "
              "this file's contrast baseline is broken, not just the wire -- "
              "investigate before trusting the wire verdict below")
        return 2

    print("-- served HTTP wire boundary --")
    try:
        result, lines = run_wire_probe()
    except OSError as exc:
        print("NO-DATA: wire fixture could not be exercised (%s)"
              % type(exc).__name__)
        return 2
    if result is None:
        print("\n".join(lines))
        return 2
    for line in lines:
        print(line)
    if not result["human_alone_sees"]:
        print("NO-DATA: the wire never served the human-alone baseline "
              "recall; cannot draw a dual-principal conclusion from a "
              "broken baseline")
        return 2

    class KnownWireGap(unittest.TestCase):
        @unittest.expectedFailure
        def test_wire_expresses_a_second_principal(self):
            self.assertTrue(result["can_express"], "\n".join(lines))

    print("KNOWN GAP: the wire cannot express a second principal, tracked as "
          "an expected failure; an unexpected success exits 1 and requires "
          "review of this test.", flush=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(KnownWireGap)
    checked = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if checked.wasSuccessful() else 1


if __name__ == "__main__":
    if "--unittest" in sys.argv[1:]:
        sys.argv.remove("--unittest")
        unittest.main()
    else:
        sys.exit(main())
