#!/usr/bin/env python3
"""Every scripts/test_*.py and scripts/loop/test_*.py must be run by a battery, by name.

WHY. A battery names its checks one by one, so a new test file that nobody registers is
run by nothing. Measured 2026-09-20: 23 of 402 were unregistered, and two of them had been
failing unseen (a stale privacy assertion about the public export, and a skill count that
read 34 while the tree held 82). A list of the missing ones would age the same way, so this
is a control: adding a test without registering it turns this red.

A file that truly cannot run in a battery goes in EXEMPT with its reason. An exemption for a
file that no longer exists also fails, so the list cannot rot either.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BATTERIES = ("check_all.sh", "required_fast.sh")
EXEMPT = {}  # scripts-relative path ("test_name.py" or "loop/test_name.py"): reason


def registered_names(folder=HERE, batteries=BATTERIES):
    names = set()
    for battery in batteries:
        with open(os.path.join(folder, battery), encoding="utf-8") as fh:
            for line in fh:
                if line.lstrip().startswith("#"):
                    continue  # a test named only in a comment is not run
                names.update(re.findall(r"scripts/((?:loop/)?test_\w+\.py)", line))
                # the other spelling a battery uses: python3 -m unittest scripts.test_x
                names.update(m.replace(".", "/") + ".py"
                             for m in re.findall(r"\bscripts\.((?:loop\.)?test_\w+)", line))
    return names


def unregistered(folder=HERE, batteries=BATTERIES, exempt=None):
    exempt = EXEMPT if exempt is None else exempt
    shipped = {n for n in os.listdir(folder) if re.fullmatch(r"test_\w+\.py", n)}
    loop = os.path.join(folder, "loop")
    if os.path.isdir(loop):
        shipped.update("loop/" + n for n in os.listdir(loop) if re.fullmatch(r"test_\w+\.py", n))
    return sorted(shipped - registered_names(folder, batteries) - set(exempt)), sorted(set(exempt) - shipped)


class BatteryRegistration(unittest.TestCase):
    def test_every_scripts_test_is_run_by_a_battery(self):
        missing, stale = unregistered()
        self.assertEqual(missing, [], "run by no battery: register each in scripts/check_all.sh")
        self.assertEqual(stale, [], "exempted but no longer shipped: drop from EXEMPT")

    def test_the_check_itself_can_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for name in ("test_seen.py", "test_dotted.py", "test_orphan.py", "test_only_in_a_comment.py", "helper.py"):
                open(os.path.join(d, name), "w").close()
            with open(os.path.join(d, "check_all.sh"), "w") as fh:
                fh.write('run_check "seen" python3 scripts/test_seen.py -v\n'
                         'run_check "dotted" python3 -m unittest -v scripts.test_dotted\n'
                         '# run_check "c" python3 scripts/test_only_in_a_comment.py\n')
            open(os.path.join(d, "required_fast.sh"), "w").close()
            self.assertEqual(unregistered(d), (["test_only_in_a_comment.py", "test_orphan.py"], []))
            self.assertEqual(unregistered(d, exempt={"test_orphan.py": "r", "test_gone.py": "r"}),
                             (["test_only_in_a_comment.py"], ["test_gone.py"]))
            os.remove(os.path.join(d, "required_fast.sh"))
            with self.assertRaises(OSError):  # a missing battery blocks; it never reads as "all registered"
                unregistered(d)

    def test_loop_tests_use_the_same_registration_and_exemption_rules(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "loop"))
            for name in ("test_seen.py", "test_dotted.py", "test_orphan.py", "test_only_in_a_comment.py", "helper.py"):
                open(os.path.join(d, "loop", name), "w").close()
            # A registered top-level sibling must not hide the loop orphan.
            open(os.path.join(d, "test_orphan.py"), "w").close()
            with open(os.path.join(d, "check_all.sh"), "w") as fh:
                fh.write('run_check "seen" python3 -B scripts/loop/test_seen.py\n'
                         'run_check "top" python3 scripts/test_orphan.py\n'
                         '# run_check "c" python3 -B scripts/loop/test_only_in_a_comment.py\n')
            with open(os.path.join(d, "required_fast.sh"), "w") as fh:
                fh.write('run_check "dotted" python3 -m unittest scripts.loop.test_dotted\n')
            self.assertEqual(unregistered(d),
                             (["loop/test_only_in_a_comment.py", "loop/test_orphan.py"], []))
            self.assertEqual(unregistered(d, exempt={"loop/test_orphan.py": "r", "loop/test_gone.py": "r"}),
                             (["loop/test_only_in_a_comment.py"], ["loop/test_gone.py"]))
            os.remove(os.path.join(d, "required_fast.sh"))
            with self.assertRaises(OSError):
                unregistered(d)


if __name__ == "__main__":
    unittest.main()
