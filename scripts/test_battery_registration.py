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
#: THE SUBDIRECTORIES THIS SCANNER READS, and the ones it deliberately does not.
#: Until 2026-10-25 it read scripts/ and scripts/loop/ ONLY. JT0's harness then landed in
#: scripts/journey/ and this check passed with exit 0 because NOTHING LOOKED THERE: a new folder was a
#: way out of the law the file exists to enforce. Every immediate subdirectory is now read, and each
#: exclusion below says why it is not a check.
SKIP_DIRS = {
    "fixtures": "inputs to other checks, never checks themselves: the test_*.py files under "
                "scripts/fixtures/review-depth/*/base/ are what a review depth fixture FEEDS to a tool, "
                "so running them proves nothing about this repository",
    "__pycache__": "build output, not source",
}

EXEMPT = {  # scripts-relative path ("test_name.py" or "loop/test_name.py"): reason
    "test_reach.py": "OUT1's frozen harness (the stepping stone rescope, 2026-10-30): Reach and Preserved are RED BY ASSERTION until OUT1.a and OUT1.b are built. A battery that ran it would refuse every landing to express two unbuilt sub units; go_no_go_119's must-haves-done row holds the release unready meanwhile.",
    "test_loop_delivery.py": "LOOP1's frozen harness (rescope 2026-10-30): Room, Admitted and Delivered are RED BY ASSERTION until LOOP1.a, LOOP1.b and LOOP1.c are built; Delivered reads the stage rows of AUTH1.b's landing through the loop. must-haves-done holds the release unready meanwhile.",
    "test_learning.py": "LEARN1's frozen harness (rescope 2026-10-30): Applied, Prevented and Recalled are RED BY ASSERTION until LEARN1.a to LEARN1.c are built. must-haves-done holds the release unready meanwhile.",
    "test_auth_path.py": "AUTH1's frozen harness (rescope 2026-10-30): Expiry and Dollars are RED BY ASSERTION until AUTH1.b and AUTH1.c are built (AUTH1.a is pinned in test_loop_budget_grant.py, green). must-haves-done holds the release unready meanwhile.",
    "test_benchmark_arms.py": "BASE1's frozen harness (rescope 2026-10-30): Arms, Tasks, Pilot and Ranked are RED BY ASSERTION until BASE1.a to BASE1.d are built. must-haves-done holds the release unready meanwhile.",
    "test_brother_hook.py": "HD1's frozen harness: Deny (HD1.d, the deny class the dispatcher refuses "
                           "with: a session answer, a write into the park folder, a ledger line "
                           "per deny) and Wired.test_one_command_per_event (HD1.e) are RED BY "
                           "ASSERTION, twelve cases, while those two sub units are held behind the "
                           "HP1 conflict; HD1.a, HD1.b, HD1.c and HD1.f are landed and their twenty "
                           "four cases pass. A battery that ran it would refuse every landing on "
                           "this branch to express two held sub units. The landed part is run by "
                           "scripts/test_brother_hook_deadline.py and scripts/test_integrate_commit_gate.py.",
    "test_first_task.py": "HX119.a's frozen harness: every class is RED BY ASSERTION until its own "
                          "sub unit is built (FIRST1.c, FIRST1.d, FIRST1H.a, FIRST1H.b), which is what "
                          "the plan requires of it. A battery that ran it would refuse every landing on "
                          "this branch to express four unfinished units. scripts/test_plan_119.py "
                          "HarnessFirst is what holds it to the bar, and it must go red for the right "
                          "reason: an assertion, never a skip and never an import error.",
    "test_proof_source.py": "HX119.b's frozen harness: Deterministic, Index and Line are RED BY "
                            "ASSERTION until PC1H.a, PC1H.b and PC1.c are built. Same reason as "
                            "test_first_task.py: a battery that ran it would refuse every landing "
                            "on this branch. scripts/test_plan_119.py HarnessCard holds it.",
    "test_pages_wellformed.py": "HX119.b's frozen harness: Wellformed is RED BY ASSERTION until "
                                "PAGE1.a adds `<` to bm_store's path lookbehind, so a closing HTML "
                                "tag stops being masked as a path. HarnessCard holds it.",
    "test_routed_model_safety.py": "HX119.c's frozen harness: CJK, RoutedFailures and Vendors are "
                                   "RED BY ASSERTION until CJK1H.a, CJK1H.b and CJK1.c are built. "
                                   "scripts/test_plan_119.py HarnessSafety holds it.",
    "test_footprint.py": "HX119.c's frozen harness: NoConsentNoWrite, BrotherModeWriters and "
                         "Forget are RED BY ASSERTION until FOOT1.a, FOOT1.b and FOOT1H.a are "
                         "built. These are a consent boundary, so every requirement fails closed: "
                         "an unreadable file and a missing docs/DATA.md are failures, never passes. "
                         "HarnessSafety holds it.",
    "test_owner_paths.py": "HX119.d's frozen harness: the six VaultDefaults classes are RED BY "
                           "ASSERTION until VRT2 routes seventeen shipped modules through the "
                           "vault resolver instead of defaulting to one person's own documents "
                           "folder. VaultRoot and VaultWriters are S11.a's to add to this same "
                           "file, so it must not be pinned. HarnessVault holds it.",
    "test_proof_can_fail.py": "PROOF1's frozen harness: SelfRevert, ZeroTests, Label and Labels are "
                              "all four RED BY ASSERTION, twelve of twelve tests, until PROOF1.a to "
                              "PROOF1.d are built, which is what the plan requires of it. A battery "
                              "that ran it would refuse every landing on this branch to express one "
                              "unfinished unit. PROOF1's lane is session and not loop, so no Harness "
                              "class in scripts/test_plan_119.py reads it (group_problems scans loop "
                              "lane units only): the four classes ARE the sub done checks, and the "
                              "bar they are held to is that each one is red by an assertion, never "
                              "by a skip and never by an import error.",
    "test_h1_activation.py": "S9's frozen harness: Activated is RED BY ASSERTION, one of nine tests, "
                             "until the owner's own hand activates H1 and the activation record "
                             "exists. The eight fixture cases beside it are green and prove each "
                             "guard can refuse; the one that is red is the only one that reads the "
                             "real record, which is the point. A battery that ran it would refuse "
                             "every landing on this branch to express one unfinished unit. S9's lane "
                             "is owner, so no Harness class in scripts/test_plan_119.py reads it "
                             "(group_problems scans loop lane units only).",
    "test_landing_classes.py": "S8's frozen harness: ClassesProven is RED BY ASSERTION, two of "
                               "eleven tests, until the product and kernel landing classes each have "
                               "a watched daylight landing recorded. The nine fixture cases beside "
                               "them are green and prove each guard can refuse. A battery that ran "
                               "it would refuse every landing on this branch to express one "
                               "unfinished unit. S8's lane is field, so no Harness class in "
                               "scripts/test_plan_119.py reads it.",
    "test_battery_clean.py": "S10's frozen harness: PoolTimeout is RED BY ASSERTION, six of six, "
                             "until perturb_pool gains a baseline wall that answers to the pool's own "
                             "concurrency, and NativeWorker is red on the one case that asks for D16's "
                             "outcome to be written down. A battery that ran it would refuse every "
                             "landing on this branch to express one unfinished unit, and NativeWorker "
                             "runs the whole land_apply file twice, once per interpreter, which is "
                             "minutes of wall that the battery already spends on that file's own row. "
                             "S10's lane is session, so no Harness class in scripts/test_plan_119.py "
                             "reads it (group_problems scans loop lane units only).",
}


def registered_names(folder=HERE, batteries=BATTERIES):
    names = set()
    for battery in batteries:
        with open(os.path.join(folder, battery), encoding="utf-8") as fh:
            for line in fh:
                if line.lstrip().startswith("#"):
                    continue  # a test named only in a comment is not run
                names.update(re.findall(r"scripts/((?:[\w.-]+/)?test_\w+\.py)", line))
                # the other spelling a battery uses: python3 -m unittest scripts.test_x
                names.update(m.replace(".", "/") + ".py"
                             for m in re.findall(r"\bscripts\.((?:loop\.)?test_\w+)", line))
    return names


def unregistered(folder=HERE, batteries=BATTERIES, exempt=None):
    exempt = EXEMPT if exempt is None else exempt
    shipped = {n for n in os.listdir(folder) if re.fullmatch(r"test_\w+\.py", n)}
    for entry in sorted(os.listdir(folder)):
        sub = os.path.join(folder, entry)
        if not os.path.isdir(sub) or entry in SKIP_DIRS:
            continue
        shipped.update(entry + "/" + n for n in os.listdir(sub)
                       if re.fullmatch(r"test_\w+\.py", n))
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
            self.assertEqual(unregistered(d, exempt={}),   # a fixture folder: the live EXEMPT list is not its own
                              (["test_only_in_a_comment.py", "test_orphan.py"], []))
            self.assertEqual(unregistered(d, exempt={"test_orphan.py": "r", "test_gone.py": "r"}),
                             (["test_only_in_a_comment.py"], ["test_gone.py"]))
            os.remove(os.path.join(d, "required_fast.sh"))
            with self.assertRaises(OSError):  # a missing battery blocks; it never reads as "all registered"
                unregistered(d)

    def test_any_new_subdirectory_is_read_and_fixtures_are_not(self):
        """A NEW folder must not be a way out of this law. Until 2026-10-25 only scripts/ and
        scripts/loop/ were read, so JT0's harness in scripts/journey/ passed with exit 0 because nothing
        looked there. Every immediate subdirectory is read now, and SKIP_DIRS carries the exclusions with
        their reasons: a test_*.py under scripts/fixtures/ is an INPUT to another check, never a check."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for sub in ("journey", "persona", "fixtures"):
                os.mkdir(os.path.join(d, sub))
                open(os.path.join(d, sub, "test_orphan.py"), "w").close()
            with open(os.path.join(d, "check_all.sh"), "w") as fh:
                fh.write('run_check "p" python3 -B scripts/persona/test_orphan.py\n')
            open(os.path.join(d, "required_fast.sh"), "w").close()
            missing, stale = unregistered(d, exempt={})
            self.assertIn("journey/test_orphan.py", missing,
                          "a test in a new subdirectory must be seen, or the folder is a way out")
            self.assertNotIn("persona/test_orphan.py", missing,
                             "a registered test in a subdirectory must count as registered")
            self.assertNotIn("fixtures/test_orphan.py", missing,
                             "a fixture INPUT is not a check and must not be demanded of a battery")
            self.assertEqual(stale, [])

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
            self.assertEqual(unregistered(d, exempt={}),
                             (["loop/test_only_in_a_comment.py", "loop/test_orphan.py"], []))
            self.assertEqual(unregistered(d, exempt={"loop/test_orphan.py": "r", "loop/test_gone.py": "r"}),
                             (["loop/test_only_in_a_comment.py"], ["loop/test_gone.py"]))
            os.remove(os.path.join(d, "required_fast.sh"))
            with self.assertRaises(OSError):
                unregistered(d)


if __name__ == "__main__":
    unittest.main()
