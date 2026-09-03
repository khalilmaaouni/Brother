#!/usr/bin/env python3
"""Opt-in live integration path for `sbe pr verify`. Run:
python3 tools/test_sbe_prverify_live.py

Spec of record: docs/specs/2026-07-30-sbe-pr-verify.md, "Opt-in live test".

This is NOT part of the fixture suite in tools/test_sbe_prverify.py, and it
never opens a socket unless an operator has explicitly opted in by setting
BOTH SBE_LIVE_GH_REPO (owner/name) and SBE_LIVE_GH_PR (the PR number to
verify), with a token discoverable the same way `sbe pr verify` itself would
discover one (GITHUB_TOKEN, then GH_TOKEN, then `gh auth token`). Without
both, this prints exactly one NO-DATA line naming what it did not do, and
exits 0: a skip, never a failure, because the reference machine this project
is built on (docs/specs/2026-07-30-sbe-pr-verify.md, "Machine facts") holds
gh with no token, and most CI runners are in the same position. It never
mutates the remote: everything it can reach is a GET.

Kept as a standalone script rather than a unittest.TestCase on purpose: a
suite runner that discovers "every test class in tools/" (there is exactly
one such runner, evals/test_no_data_class.py's load_tool_modules(), and it
imports every .py file under tools/ for its OWN registry walk, never to run
unittest tests inside them) must not accidentally invoke the live path by
importing this file; import-time here only defines functions, and the live
network call happens only inside main(), only under the __main__ guard.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")


def discover_token():
    """(token, source, detail). Three values, not two, for the same reason
    every other multi-value helper in this project's tools/ is a 3-tuple:
    evals/test_no_data_class.py reads any 2-tuple return it cannot prove is
    never a "PASS" head as a possible verdict source, and this is not one.

    Mirrors the order the spec fixes for `sbe pr verify` itself: GITHUB_TOKEN,
    then GH_TOKEN, then `gh auth token`. A failure of the third is absence,
    never an error: `gh` missing, not installed, or not logged in are the
    same finding from here, that no token was found.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token, "GITHUB_TOKEN", "the GITHUB_TOKEN environment variable is set"
    token = os.environ.get("GH_TOKEN")
    if token:
        return token, "GH_TOKEN", "the GH_TOKEN environment variable is set"
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                              timeout=10)
    except OSError as exc:
        return None, None, "gh is not runnable here (%s), so gh auth token was not tried" % exc
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip(), "gh auth token", "gh auth token returned one"
    return None, None, ("gh auth token did not return one (exit %d): not logged in, or no "
                        "token scope" % proc.returncode)


def main():
    repo = os.environ.get("SBE_LIVE_GH_REPO")
    number = os.environ.get("SBE_LIVE_GH_PR")
    token, source, detail = discover_token()

    missing = []
    if not repo:
        missing.append("SBE_LIVE_GH_REPO (owner/name) is not set")
    if not number:
        missing.append("SBE_LIVE_GH_PR (the PR number to verify) is not set")
    if not token:
        missing.append("no token is discoverable (%s)" % detail)

    if missing:
        sys.stdout.write(
            "NO-DATA: tools/test_sbe_prverify_live.py did not run the live path because %s. "
            "This is the opt-in integration test the spec calls for: set SBE_LIVE_GH_REPO, "
            "SBE_LIVE_GH_PR, and a token (GITHUB_TOKEN, GH_TOKEN, or `gh auth login`) to "
            "exercise it. It is never exercised in CI, and it never mutates the remote.\n"
            % "; ".join(missing))
        return 0

    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    try:
        from brothersbe import prverify as mod
    except ImportError as exc:
        sys.stderr.write(
            "tools/test_sbe_prverify_live.py: opted in (SBE_LIVE_GH_REPO=%s, SBE_LIVE_GH_PR=%s) "
            "but src/brothersbe/prverify.py does not exist yet (%s). This is the live path; it "
            "is not the RED fixture suite, and there is nothing to skip to here.\n"
            % (repo, number, exc))
        return 1

    report = mod.evaluate(repo, int(number), token)
    sys.stdout.write(mod.render(report) + "\n")
    return 0 if report.get("final") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
