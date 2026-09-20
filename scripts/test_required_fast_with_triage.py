"""test_required_fast_with_triage.py: proves required_fast_with_triage.sh
never lets the advisory J064 triage pass change the gate's own exit code,
by driving it against a FAKE required_fast.sh with a known exit code --
never the real check suites (this must stay fast).

The property this file exists to assert: PIPESTATUS[0] (the gate's own
exit code) is captured before the triage step runs and is the ONLY thing
this wrapper exits with, whatever the triage step itself does -- crash,
hang past nothing (it is never awaited past its own subprocess call), or
print garbage.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WRAPPER = os.path.join(HERE, "required_fast_with_triage.sh")


def _make_fixture(fake_gate_body, fake_triage_body=None):
    """A temp scripts/ dir holding required_fast_with_triage.sh (copied
    verbatim), a fake required_fast.sh with the given body, and either
    the REAL jev_checks.py (off mode, side-effect-free, no network) or a
    fake one when the test needs to force a triage-side failure."""
    root = tempfile.mkdtemp(prefix="required-fast-triage-fixture-")
    scripts_dir = os.path.join(root, "scripts")
    os.makedirs(scripts_dir)
    shutil.copyfile(WRAPPER, os.path.join(scripts_dir, "required_fast_with_triage.sh"))
    os.chmod(os.path.join(scripts_dir, "required_fast_with_triage.sh"),
             os.stat(os.path.join(scripts_dir, "required_fast_with_triage.sh")).st_mode
             | stat.S_IEXEC)

    gate_path = os.path.join(scripts_dir, "required_fast.sh")
    with open(gate_path, "w") as f:
        f.write("#!/bin/sh\n" + fake_gate_body + "\n")
    os.chmod(gate_path, os.stat(gate_path).st_mode | stat.S_IEXEC)

    if fake_triage_body is not None:
        with open(os.path.join(scripts_dir, "jev_checks.py"), "w") as f:
            f.write(fake_triage_body)
    else:
        shutil.copyfile(os.path.join(HERE, "jev_checks.py"),
                        os.path.join(scripts_dir, "jev_checks.py"))
        # off mode reads these two paths; point them at files that exist
        # but declare every entry off, so the real CLI's off-branch (rule
        # 9: costs nothing, touches neither registry nor network) is what
        # actually runs here.
        import json
        os.makedirs(os.path.join(root, "data"), exist_ok=True)
        with open(os.path.join(root, "data", "jev-seams.json"), "w") as f:
            json.dump({"modes": {"J064": "off"}}, f)
        with open(os.path.join(root, "data", "jev-registry.json"), "w") as f:
            json.dump({"entries": []}, f)

    return scripts_dir, root


def _run(scripts_dir, root):
    env = dict(os.environ)
    proc = subprocess.run(
        ["bash", os.path.join(scripts_dir, "required_fast_with_triage.sh")],
        capture_output=True, text=True, timeout=20, cwd=root, env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


class RequiredFastWithTriage(unittest.TestCase):
    def test_gate_pass_exits_zero(self):
        scripts_dir, root = _make_fixture("echo gate output; exit 0")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        rc, out = _run(scripts_dir, root)
        self.assertEqual(rc, 0, out)

    def test_gate_fail_still_exits_with_gates_own_code(self):
        # The whole point of PIPESTATUS: `tee` itself always exits 0, so a
        # wrapper that forgot to capture PIPESTATUS[0] would report the
        # gate as passing even when it failed. This is the mutation this
        # test exists to catch.
        scripts_dir, root = _make_fixture("echo a real failure line; exit 1")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        rc, out = _run(scripts_dir, root)
        self.assertEqual(rc, 1, out)

    def test_gate_nodata_exit_code_passes_through_unchanged(self):
        scripts_dir, root = _make_fixture("exit 2")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        rc, _out = _run(scripts_dir, root)
        self.assertEqual(rc, 2)

    def test_triage_crashing_never_changes_the_gates_exit_code(self):
        # A broken/missing jev_checks.py (import error, bad json, anything)
        # must still leave the gate's own exit code as the wrapper's exit
        # code -- the `|| echo ... (ignored)` in the wrapper is what this
        # asserts.
        scripts_dir, root = _make_fixture(
            "echo gate output; exit 1",
            fake_triage_body="import sys\nsys.exit(1)\n",
        )
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        rc, out = _run(scripts_dir, root)
        self.assertEqual(rc, 1, out)
        self.assertIn("advisory only, ignored", out)

    def test_gate_output_is_actually_captured_for_triage(self):
        # Proves the log file the triage step reads is the REAL gate
        # output, not an empty file -- catches a wiring mistake where tee
        # writes to the wrong path or the log is read before it is closed.
        scripts_dir, root = _make_fixture(
            "echo THIS_LINE_MUST_REACH_TRIAGE; exit 0",
            fake_triage_body=(
                "import sys\n"
                "path = sys.argv[sys.argv.index('--lines-file') + 1]\n"
                "text = open(path).read()\n"
                "sys.exit(0 if 'THIS_LINE_MUST_REACH_TRIAGE' in text else 3)\n"
            ),
        )
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        rc, out = _run(scripts_dir, root)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("advisory only, ignored", out)


if __name__ == "__main__":
    unittest.main()
