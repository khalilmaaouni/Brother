#!/usr/bin/env python3
"""BrotherSBE SessionStart: digest plus mechanical nags. MUST always exit 0.
Python port (2026-08-17) of the retired sh SessionStart hook: sh is not on the
Windows PATH, so every sh hook was an undeclared Git Bash dependency (the
Windows engineer's report); the rest of the tooling is already Python.

Output is injected into session context (10k char cap). The cap is ENFORCED
HERE rather than assumed. The harness truncates the TAIL, which used to be
where the compaction hint (the recovery pointer, the most safety-relevant
line) printed. So the hint and the nags print FIRST, where truncation can
never reach them, the digest prints last, and if the total runs over the cap
this script cuts it itself with a visible marker instead of letting the
harness cut it in silence. The two halves are measured SEPARATELY, in BYTES
(the sh version's wc -c rule, kept so measurement and cut stay one unit),
and the marker states what was actually cut, measured rather than assumed.

Emitter gating: ONE startup emitter is PROFILE-GATED (the telemetry nags);
the compaction hint and the update notice are UNCONDITIONAL on purpose (the
update notice is the sign the governance engine changed under the user, and
silently dropping it is the quiet downgrade this project exists to refuse).
tools/sbe_profile.py's module-isolation check reads THIS file's text and
FAILs when a gated emitter is not guarded by its module in the lines above.
"""
import json
import os
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = 10000
MARKER_ROOM = 220


def _tool(script, *args, data=None, keep_stderr=False):
    """stdout of one repo python tool, empty on any failure: a SessionStart
    that cannot run a helper injects less context, never a traceback."""
    try:
        p = subprocess.run(
            [sys.executable, os.path.join(DIR, "tools", script), *args],
            input=data, capture_output=True, text=True)
        if keep_stderr and p.stderr:
            sys.stderr.write(p.stderr)
        return p.stdout
    except OSError:
        return ""


def main():
    # Capture the hook JSON from stdin ONCE: replayed to the baseline writer
    # and the compaction hint, ignored by the digest and the nags.
    try:
        payload = sys.stdin.read()
    except OSError:
        payload = ""

    # The session baseline (release blocker 1 item 1.2): what the working
    # tree held BEFORE this session touched it. Its stdout is discarded (a
    # baseline path is not context) and its stderr is kept.
    _tool("sbe_session_baseline.py", "write", data=payload, keep_stderr=True)

    head_parts = [_tool("sbe_telemetry.py", "compact-hint", data=payload)]
    # The telemetry nags are the one profile-gated emitter. The guard line
    # itself carries the profile question, because tools/sbe_profile.py's
    # isolation check reads this file's text and looks for that question in
    # the window directly above the emitter.
    if _gate_open("enabled telemetry"):
        head_parts.append(_tool("sbe_telemetry.py", "startup-nags"))
    head_parts.append(_tool("sbe_telemetry.py", "check-update"))
    head_out = "".join(head_parts).rstrip("\n")
    try:
        with open(os.path.join(DIR, "DIGEST.md"), encoding="utf-8",
                  errors="replace") as f:
            digest_out = f.read().rstrip("\n")
    except OSError:
        digest_out = ""

    # Bytes, not characters, on every platform: the measurement and the cut
    # stay the same unit (the sh version's wc -c discipline).
    head_b = head_out.encode("utf-8")
    total_len = len(("%s\n%s" % (head_out, digest_out)).encode("utf-8"))
    out = sys.stdout.buffer
    if total_len <= CAP:
        out.write(head_b + b"\n" + digest_out.encode("utf-8") + b"\n")
    elif len(head_b) + MARKER_ROOM <= CAP:
        keep = CAP - len(head_b) - MARKER_ROOM
        dig_b = digest_out.encode("utf-8")
        out.write(head_b + b"\n")
        out.write(dig_b[:keep])
        out.write(("\n[BrotherSBE: output truncated at the %s-character injection "
                   "cap. The compaction hint and nags printed in full; the digest "
                   "was cut after %s bytes of its %s. Read DIGEST.md directly for "
                   "the rest.]\n" % (CAP, keep, len(dig_b))).encode("utf-8"))
    else:
        # The hint alone overflows: the digest is dropped ENTIRELY (not "its
        # tail"), and the hint itself is cut. Both facts are stated, because
        # this is exactly the run where a reader most needs to know what they
        # are not seeing.
        out.write(head_b[:CAP - MARKER_ROOM])
        out.write(("\n[BrotherSBE: output truncated at the %s-character injection "
                   "cap. The compaction hint and nags alone are %s bytes, so THEY "
                   "were cut after %s bytes and the digest did not print at all. "
                   "Read the resume brief in your vault and DIGEST.md directly.]\n"
                   % (CAP, len(head_b), CAP - MARKER_ROOM)).encode("utf-8"))
    out.flush()


def _gate_open(question):
    """Whether a module is on: exit 0 from the profile tool asked `question`
    (the words become its CLI arguments), with every failure reading as "not
    on" and the reason discarded, the safe direction for a hook that must
    never fail a session."""
    try:
        return subprocess.run(
            [sys.executable, os.path.join(DIR, "tools", "sbe_profile.py"),
             *question.split()],
            capture_output=True).returncode == 0
    except OSError:
        return False


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # sbe: allow-silent SessionStart must never fail a session; a traceback here would inject noise into context instead of a digest
    sys.exit(0)
