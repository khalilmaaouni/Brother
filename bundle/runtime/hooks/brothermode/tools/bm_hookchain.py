#!/usr/bin/env python3
"""The hook chain driver: one stdin payload, several sibling tools, in order.

THE WINDOWS PORT (2026-08-17). hooks/hooks.json's Stop and PreCompact
entries used to be `sh -c` scripts (p=$(cat); printf %s "$p" | python3 ...;
...), which read the payload once and replayed it to each program. `sh` is
not on the Windows PATH, so both chains were installed-and-silently-dead on
any Windows machine without Git Bash. This file is that shell one-liner as a
Python program: read stdin once, feed the same payload to every program in
the named chain, run them ALL regardless of individual failures (the shell
`;` semantics), and exit with the LAST program's exit code (also the shell
`;` semantics). Windows behavior remains UNVERIFIED until a real Windows
machine runs it; docs/WINDOWS-CHECK.md is the protocol for closing that.

THE CHAINS TABLE IS THE SINGLE COPY. Before this port the chain contents
lived spelled out in FOUR places (hooks/hooks.json, scripts/install.py's
hook_commands(), docs/QUICKSTART.md and docs/SETUP.md), the four-copy law
scripts/install.py documents. All four now name this driver and the chain
contents live once, here, where tools/test_bm_consent.py enumerates them to
prove every chained program still carries its own consent gate: the driver
itself gates nothing, exactly as the sh -c wrapper gated nothing, because
every program in the table refuses on its own before consent.

Output ordering: each program's stdout and stderr are captured and re-emitted
through this process's own streams, in program order. That keeps the content
of both streams intact for the hook runner AND for tools/bm_hookbench.py,
which executes hook programs in process with patched streams; a child
process writing straight to the inherited descriptors would bypass those.

The subprocess import is a NAMED exception in tools/test_bm.py's
zero-network suite, the same local-execution posture as bm_autosave.py
driving git: every process this file starts is a sibling tool from this
repository, on this machine, with no network anywhere. SECURITY.md
documents it beside the other named exceptions.

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_bm_repo_scope():
    """Load bm_repo_scope.py by path, the same load-by-path shape every
    sibling tool in this directory uses. E76 per-repository hook scoping:
    checked ONCE here, before any chained program runs, rather than in
    each of bm_lead.py, bm_view.py and bm_autosave.py individually, since
    those three are reachable ONLY through this driver (hooks/hooks.json
    never registers them directly). This is a narrower exception to "the
    driver gates nothing" than it looks: that rule is about the CONSENT
    gate specifically (tools/test_bm_consent.py enumerates every chained
    program and demands each one gate itself, because consent is a
    per-program install question); repo scoping is a per-invocation
    question about which repository this whole hook run is happening in,
    answered once and identical for every program in the chain, so
    answering it once here is equivalent to every program answering it
    separately, not a weaker check wearing the same name."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_repo_scope_for_hookchain", os.path.join(HERE, "bm_repo_scope.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional gate module load; hooks_off degrades to active when this returns None
        return None

#: Chain name to the (module, argv) rows it runs, in order. Every module
#: lives beside this file in tools/ and carries its own consent gate;
#: tools/test_bm_consent.py reads this table mechanically, so a program
#: added here without a gate fails that suite by name rather than shipping
#: ungated (the exact defect class the 2026-08-02 review found when the
#: PreCompact line's SECOND program shipped ungated).
CHAINS = {
    "stop": (
        ("bm_telemetry.py", ("stop-warn",)),
        ("bm_lead.py", ("watchdog", "--tick")),
        ("bm_view.py", ("render", "--if-stale")),
        ("bm_view.py", ("alert", "--tick")),
    ),
    "precompact": (
        ("bm_autosave.py", ("precompact",)),
        ("bm_telemetry.py", ("precompact-brief",)),
    ),
}


def run_chain(name, payload):
    """Every program in the chain, each fed the same payload, none skipped
    because an earlier one failed. Returns the last program's exit code,
    which is what `a; b; c` would have exited with."""
    last = 0
    for module, args in CHAINS[name]:
        path = os.path.join(HERE, module)
        try:
            result = subprocess.run(
                [sys.executable, path] + list(args), input=payload,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True)
        except OSError as exc:
            # The shell analogue: a missing program printed one line to
            # stderr and the chain carried on. 127 is the shell's own code
            # for it.
            sys.stderr.write("bm_hookchain: %s did not run: %s\n"
                             % (module, exc))
            last = 127
            continue
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        last = result.returncode
    return last


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0] not in CHAINS:
        sys.stderr.write(
            "bm_hookchain: exactly one argument, the chain name, one of: "
            "%s\n" % ", ".join(sorted(CHAINS)))
        return 2
    # The shell version's p=$(cat) stripped trailing newlines; matched here
    # so every chained program receives byte-identical stdin.
    try:
        payload = sys.stdin.read()
    except (IOError, OSError, ValueError):
        payload = ""
    payload = payload.rstrip("\n")
    _rs = _load_bm_repo_scope()
    if _rs is not None and _rs.hooks_off(payload=payload):
        return 0
    return run_chain(argv[0], payload)


if __name__ == "__main__":
    sys.exit(main())
