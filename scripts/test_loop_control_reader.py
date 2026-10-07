"""Focused real-driver checks for shared control-reader failure direction."""
import os
import shutil
import sys

import test_loop_until_lifecycle as lifecycle


def _w(path, body, mode=0o644):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


def _drive_with_setup_control(control_name=None, create=True):
    """Drive loop_until.sh with a setup step that may create a control file while it runs.

    The setup step is the intake status read, which the driver calls after the admission control check
    and before the new pre-proof-start control check. The receipt tool is stubbed so proof-start can be
    observed without relying on the real loop_receipt.py.
    """
    h = lifecycle.make_home(lanes="8")
    intake = os.path.join(h, ".claude", "bin", "loop_intake.py")
    body = '''#!/usr/bin/env python3
import os, sys
'''
    if create and control_name:
        body += 'open(os.path.join(os.environ["HOME"], ".claude", "evidence", %r), "w").write("owner: mid-setup stop")' % control_name
        body += chr(10)
    body += '''print("INTAKE STATUS: a driver may start")
print("BUDGET_USD 10.00")
print("DEADLINE 23:59")
sys.exit(0)
'''
    _w(intake, body, 0o755)

    scripts = os.path.join(h, "driver")
    shutil.copytree(os.path.dirname(lifecycle.SCRIPT), scripts)
    receipt = os.path.join(scripts, "loop_receipt.py")
    receipt_body = '''#!/usr/bin/env python3
import os, sys
p = os.path.join(os.environ["HOME"], "calls", "proof_start")
open(p, "a").write(" ".join(sys.argv[1:]) + chr(10))
print("STUB RECEIPT")
sys.exit(0)
'''
    _w(receipt, receipt_body, 0o755)

    old = lifecycle.SCRIPT
    try:
        lifecycle.SCRIPT = os.path.join(scripts, "loop_until.sh")
        r = lifecycle.run(h, [lifecycle.future_hhmm(), "1"])
    finally:
        lifecycle.SCRIPT = old
    return h, r


def case_hold_created_during_setup_refuses_before_proof_start():
    h, r = _drive_with_setup_control("LOOP-HOLD.txt", create=True)
    return (r.returncode == 2
            and "a HOLD is in force" in r.stdout
            and "owner: mid-setup stop" in r.stdout
            and "proof-start" not in lifecycle.called(h, "proof_start"))


def case_pause_created_during_setup_refuses_before_proof_start():
    h, r = _drive_with_setup_control("LOOP-PAUSE.txt", create=True)
    return (r.returncode == 2
            and "PAUSE" in r.stdout
            and "owner: mid-setup stop" in r.stdout
            and "proof-start" not in lifecycle.called(h, "proof_start"))


def case_no_control_created_during_setup_calls_proof_start():
    h, r = _drive_with_setup_control(None, create=False)
    return ("proof-start" in lifecycle.called(h, "proof_start")
            and "REFUSED TO START" not in r.stdout)


if __name__ == '__main__':
    lifecycle.CASES = lifecycle.CONTROL_CASES + [
        ("HOLD created during setup refuses before proof-start", case_hold_created_during_setup_refuses_before_proof_start),
        ("PAUSE created during setup refuses before proof-start", case_pause_created_during_setup_refuses_before_proof_start),
        ("no control created during setup calls proof-start", case_no_control_created_during_setup_calls_proof_start),
    ]
    raise SystemExit(lifecycle.main())
