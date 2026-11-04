"""Black-box proof: a broken access policy fails CLOSED, VB3-04 (WBS row,
docs/plan/VAULT-WBS-V2-2026-08-29.json).

WHY THIS EXISTS. scripts/readiness_gate.py's "fail-closed-policy" row reads
NO-DATA today because "VB3-04 landed in BrotherModeUp (PR 160); the
Brother-side evidence suite that proves it from this repository is queued."
This file is that suite, and like its sibling test_tenancy_isolation.py it
never re-runs BrotherModeUp's own unit tests -- it drives the vendored,
frozen product boundary from outside.

BOUNDARY USED: the CLI recall boundary (scripts/fixtures/bmu_vault_seam/
bm_vault.py recall), not the served HTTP endpoint. Read start to finish
before this was written: bm_vault_serve.py's wire protocol forwards only a
single "identity" field into --as/--identity and carries no switch for
BROTHERMODE_ENTERPRISE or for pointing --vault at a policy-module variant,
so the served boundary cannot exercise "module missing" or "module
crashing" without inventing wire fields the real product does not expose.
The CLI is bm_vault.py's own real, documented entry point (`bm_vault.py
recall --identity ... --query ...`) and is exactly what bm_vault_serve.py
itself shells out to for every request; using it directly is still a
product-boundary proof, not a call into a private function.

THE MECHANISM UNDER TEST, read directly from bm_vault.py's own
_policy_deny (scripts/fixtures/bmu_vault_seam/bm_vault.py): outside
enterprise mode (no BROTHERMODE_ENTERPRISE=1) a policy module that cannot
be imported degrades to "not trimmed" -- todays fail-OPEN-with-a-warning
behavior, unchanged on purpose. IN enterprise mode, the same missing module
instead falls back to _is_restricted(): a note whose frontmatter carries
`restricted: true` is withheld, an ordinary note still serves. This is the
fail-closed rule in a single sentence, and it is a real branch in the
vendored code (grepped and read at the line, not inferred from the WBS
prose).

TWO POLICY-MODULE VARIANTS this file builds itself (never a hand edit to
the vendored fixture -- PROVENANCE.md forbids that): a scratch copy of
scripts/fixtures/bmu_vault_seam/ with bm_vault_policy.py DELETED (the
"module missing" case named in the B-wave brief), and a second scratch
copy with bm_vault_policy.py REPLACED by a two-line stub whose decide_dual
always raises (the "module crashing" case). Both scratch copies are
temporary directories this file creates and removes; the checked-in
fixture is read, never written.

DRIVEN BACKWARDS (mandatory per the brief): the missing-module case is run
BOTH with BROTHERMODE_ENTERPRISE=1 (must withhold the restricted note) and
without it (single-machine mode, today's documented fail-open-with-a-
warning behavior) -- the second run is expected, by the vendored code's own
stated contract, to LEAK the restricted note. That is not a bug this test
is discovering; it is the existing single-machine default, used here as
the backwards case that proves this proof has teeth: disable enterprise
mode (the fail-closed branch's own on-switch) and the same restricted
canary that was withheld a moment ago must now appear, or this check was
never actually discriminating between the two behaviors.

Exit contract, same shape as every check_all.sh suite: 0 all assertions
pass, 1 an assertion failed, 2 NO-DATA (the fixture could not be built or
exercised at all).

Python 3, stdlib only. No network. No em or en dashes anywhere in this file.
"""
import os
import shutil
import subprocess
import sys
import tempfile
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

HERE = os.path.dirname(os.path.abspath(__file__))
SEAM_DIR = os.path.join(HERE, "fixtures", "bmu_vault_seam")

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

_CRASHING_STUB = '''
"""A deliberately broken stand-in for bm_vault_policy.py, built by
test_policy_fail_closed.py to prove the fail-closed branch. load() always
returns a policy (never the "no policy file" fast path), so cmd_recall's
per-note deny() closure runs and calls decide_dual(), which always raises.
"""
import os


def policy_path(vault, override=None):
    return override or (os.path.join(vault, "99-System", "access-policy.json")
                        if vault else None)


def load(path):
    return {"default": "allow", "rules": []}, []


def decide_dual(policy, human, agent, purpose, relpath):
    raise RuntimeError("stub policy: decide_dual intentionally raises, "
                       "planted by test_policy_fail_closed.py")
'''


def _seam_present():
    return all(os.path.isfile(os.path.join(SEAM_DIR, f)) for f in (
        "bm_vault.py", "bm_vault_context.py", "bm_vault_serve.py",
        "bm_vault_policy.py", "bm_freshness.py"))


def _make_variant(scratch, kind):
    """A scratch copy of the seam directory with bm_vault_policy.py either
    "missing" (deleted) or "crashing" (replaced by the raising stub).
    Returns the scratch tools directory's bm_vault.py path."""
    tools = os.path.join(scratch, "tools-%s" % kind)
    shutil.copytree(SEAM_DIR, tools)
    policy_path = os.path.join(tools, "bm_vault_policy.py")
    if kind == "missing":
        os.remove(policy_path)
    elif kind == "crashing":
        with open(policy_path, "w", encoding="utf-8") as fh:
            fh.write(_CRASHING_STUB)
    else:
        raise ValueError("unknown variant kind %r" % kind)
    return os.path.join(tools, "bm_vault.py")


def _make_home(scratch):
    """One fixture home: a restricted note and an ordinary note, both
    carrying their canary in the `name:` frontmatter field, the only place
    bm_vault.py's own _print_hits actually surfaces a hit's identity into
    its printed output (confirmed against this exact fixture before this
    was written; the body text never appears in recall's stdout).
    """
    home = os.path.join(scratch, "home")
    vault = os.path.join(home, "vault")
    os.makedirs(vault)
    os.makedirs(os.path.join(home, ".claude"))
    restricted_canary = "RESTRICTED-CANARY-%s" % uuid.uuid4().hex[:12]
    open_canary = "OPEN-CANARY-%s" % uuid.uuid4().hex[:12]
    with open(os.path.join(vault, "restricted.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\nrestricted: true\n---\n\nan ordinary body.\n"
                 % restricted_canary)
    with open(os.path.join(vault, "open.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: %s\n---\n\nan ordinary body.\n" % open_canary)
    return home, vault, restricted_canary, open_canary


def _index(bm_vault_path, home, vault):
    env = dict(os.environ, HOME=home, BM_VAULT_ROOT=vault)
    p = subprocess.run([sys.executable, bm_vault_path, "index"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env=env, timeout=60)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def _recall(bm_vault_path, home, vault, query, enterprise):
    env = dict(os.environ, HOME=home, BM_VAULT_ROOT=vault)
    if enterprise:
        env["BROTHERMODE_ENTERPRISE"] = "1"
    else:
        env.pop("BROTHERMODE_ENTERPRISE", None)
    p = subprocess.run([sys.executable, bm_vault_path, "recall",
                       "--query", query, "--limit", "10"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env=env, timeout=30)
    return p.stdout.decode("utf-8", "replace")


def run_fail_closed_proof():
    """(ok, lines). Both variants (missing, crashing), enterprise mode on:
    restricted content withheld, unrestricted content still served. Then
    the backwards case: the missing-module variant with enterprise mode
    OFF, where the restricted note is expected, by the vendored code's own
    documented default, to leak."""
    lines = []
    ok = True
    scratch = tempfile.mkdtemp(prefix="bm-policy-proof-")
    try:
        home, vault, restricted_canary, open_canary = _make_home(scratch)
        missing_bm_vault = _make_variant(scratch, "missing")
        crashing_bm_vault = _make_variant(scratch, "crashing")

        rc, out = _index(missing_bm_vault, home, vault)
        if rc != 0:
            return None, ["NO-DATA: indexing the fixture failed (exit %d): %s"
                          % (rc, out)]

        for kind, bm_vault_path in (("missing", missing_bm_vault),
                                    ("crashing", crashing_bm_vault)):
            out = _recall(bm_vault_path, home, vault, restricted_canary, enterprise=True)
            withheld = restricted_canary not in out
            lines.append("%s enterprise mode withholds restricted content "
                        "when the policy module is %s"
                        % ("ok " if withheld else "FAIL", kind))
            ok = ok and withheld

            out = _recall(bm_vault_path, home, vault, open_canary, enterprise=True)
            served = open_canary in out
            lines.append("%s enterprise mode still serves unrestricted content "
                        "when the policy module is %s"
                        % ("ok " if served else "FAIL", kind))
            ok = ok and served

        # Backwards: the same missing-module variant, enterprise mode OFF. The
        # vendored code's own documented default (bm_vault.py's _policy_deny
        # docstring) is fail-OPEN outside enterprise mode; the restricted
        # canary must now leak, or this check was never discriminating
        # between the enterprise and single-machine branches.
        out = _recall(missing_bm_vault, home, vault, restricted_canary, enterprise=False)
        leaked_when_disabled = restricted_canary in out
        lines.append("%s disabling enterprise mode is caught as a leak "
                    "of the restricted note (backwards check)"
                    % ("ok " if leaked_when_disabled else "FAIL"))
        ok = ok and leaked_when_disabled

        return ok, lines
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


class PolicyFailsClosed(unittest.TestCase):
    @unittest.skipUnless(_seam_present(), "bmu_vault_seam fixture is absent")
    def test_missing_and_crashing_policy_withhold_restricted_content(self):
        ok, lines = run_fail_closed_proof()
        self.assertIsNotNone(ok, "\n".join(lines))
        self.assertTrue(ok, "\n".join(lines))


class TheProofCatchesADisabledFailClosedBranch(unittest.TestCase):
    """Driven backwards, folded into run_fail_closed_proof's own last
    assertion: disabling enterprise mode (never a hand edit to the vendored
    product) must flip the restricted-content check from withheld to
    leaked. This class exists so a regression that quietly removed that
    assertion from run_fail_closed_proof is still caught: it re-runs the
    exact single-machine-mode recall in isolation and demands the leak."""

    @unittest.skipUnless(_seam_present(), "bmu_vault_seam fixture is absent")
    def test_single_machine_mode_leaks_the_restricted_note_by_design(self):
        scratch = tempfile.mkdtemp(prefix="bm-policy-backwards-")
        try:
            home, vault, restricted_canary, _open_canary = _make_home(scratch)
            missing_bm_vault = _make_variant(scratch, "missing")
            rc, out = _index(missing_bm_vault, home, vault)
            self.assertEqual(rc, 0, out)
            out = _recall(missing_bm_vault, home, vault, restricted_canary,
                          enterprise=False)
            self.assertIn(restricted_canary, out,
                         "single-machine mode with a missing policy module must "
                         "still serve restricted content (today's documented "
                         "fail-open default); if this now fails, the default "
                         "changed and the forward proof's backwards check needs "
                         "re-reading, not deleting")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def main():
    if not _seam_present():
        print("NO-DATA: %s is missing one or more required fixture files; "
             "see PROVENANCE.md in that directory" % SEAM_DIR)
        return 2
    ok, lines = run_fail_closed_proof()
    if ok is None:
        print("\n".join(lines))
        return 2
    for line in lines:
        print(line)
    if ok:
        print(PASS + " exit 0 test_policy_fail_closed")
        return 0
    print(FAIL + " exit 1 test_policy_fail_closed")
    return 1


if __name__ == "__main__":
    if "--unittest" in sys.argv[1:]:
        sys.argv.remove("--unittest")
        unittest.main()
    else:
        sys.exit(main())
