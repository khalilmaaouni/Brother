#!/usr/bin/env python3
"""Tests for the owner hand steps checker in scripts/host_doc_check.py (HP1.d, spec docs/plan/specs/HP1.md).

check_owner_steps runs on small seeded pages in a temporary checkout, one defect per page, and on the real page with a
runner that stands in for the commands. The checker starts no process, so a runnable step is run through a fake runner
here. Every secret shaped string is BUILT AT RUNTIME, never written as a literal. Each test builds its own folder.

usage (repo root): python3 scripts/test_hp1_owner_steps.py TestOwnerStepsChecker -v
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import host_doc_check as hdc  # noqa: E402

PAGE = "docs/how-to/host-live-proof.md"
VERIFY = "python3 scripts/host_live_verify.py docs/plan/evidence/HP1-host-live.jsonl"
PROOF = "python3 scripts/host_live_proof.py --print-owner-commands antigravity"
HOSTS = "Antigravity, Codex and Claude Code are the three hosts.\n"
FENCE = "`" * 3


def block(command, expected, info="bash", gap=""):
    out = "%s%s\n%s\n%s\n%s" % (FENCE, info, command, FENCE, gap)
    if expected is not None:
        out += "\n%sexpected\n%s\n%s\n" % (FENCE, expected, FENCE)
    return out


def step(number, command, expected, info="bash", gap="\n"):
    return "## Step %d: title\n\n%s\n" % (number, block(command, expected, info, gap))


def page(*parts, hosts=HOSTS, declared=""):
    return hosts + declared + "\n" + "\n".join(parts)


def runner_for(output, code=0):
    calls = []

    def run(argv, cwd, env):
        calls.append(list(argv))
        return code, output
    run.calls = calls
    return run


class TestOwnerStepsChecker(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="hp1d-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(os.path.join(self.root, "docs", "how-to"))
        for name in ("host_live_proof.py", "host_live_verify.py"):
            shutil.copy(os.path.join(HERE, name), os.path.join(self.root, "scripts", name))

    def check(self, text, runner=None):
        with open(os.path.join(self.root, PAGE), "w", encoding="utf-8") as fh:
            fh.write(text)
        return hdc.check_owner_steps(self.root, PAGE, runner)

    def assertRefused(self, refusals, fragment):
        self.assertTrue(any(fragment in line for line in refusals), "wanted %r in %r" % (fragment, refusals))

    def test_a_clean_hand_page_passes(self):
        text = page(step(1, "echo hello", "hello"), step(2, "echo again", "again"))
        self.assertEqual(self.check(text), [])

    def test_steps_reads_the_command_block_and_the_expected_block(self):
        found = hdc.steps(page(step(1, PROOF, "^# HP1"), step(2, "echo hi", "hi")))
        self.assertEqual([s["number"] for s in found], [1, 2])
        self.assertEqual(found[0]["expected"], "^# HP1")
        self.assertTrue(found[0]["runnable"])
        self.assertFalse(found[1]["runnable"])
        self.assertEqual(found[1]["commands"][0]["command"], "echo hi")

    def test_a_step_with_no_expected_block_is_refused(self):
        refusals = self.check(page(step(1, "echo hello", None)))
        self.assertRefused(refusals, "step 1 has no expected block")

    def test_an_expected_block_after_prose_is_not_immediate(self):
        text = page(step(1, "echo hello", "hello", gap="\nSome prose between the two blocks.\n"))
        self.assertRefused(self.check(text), "step 1 has no expected block")

    def test_an_empty_expected_block_is_refused(self):
        self.assertRefused(self.check(page(step(1, "echo hello", "   "))), "expected block is empty")

    def test_an_expected_block_that_matches_anything_is_refused(self):
        for pattern in (".*", ".*\n", "(?s).*", "^", "x*"):
            with self.subTest(pattern=pattern):
                self.assertRefused(self.check(page(step(1, VERIFY, pattern), step(2, "echo a", "a")),
                                              runner_for("NO-DATA: x")), "matches anything")

    def test_a_command_whose_script_does_not_resolve_is_refused(self):
        refusals = self.check(page(step(1, "python3 scripts/no_such_script.py go", "go")))
        self.assertRefused(refusals, "no_such_script.py, which is not in the tree")

    def test_a_command_whose_flag_does_not_resolve_is_refused(self):
        refusals = self.check(page(step(1, "python3 scripts/host_live_proof.py --no-such-flag antigravity", "x")))
        self.assertRefused(refusals, "--no-such-flag")

    def test_a_secret_in_a_command_line_is_refused(self):
        token = "sk" + "-" + "Ab1" * 8
        bearer = "\x42earer " + "q9" * 8
        cases = (
            "curl-less --api-key " + token,
            "run --token=" + "z" * 12,
            "TOKEN=" + "z" * 12 + " echo hi",
            "echo " + bearer,
            "echo gh" + "p_" + "a1" * 10,
            "echo --password <the-password>",
            "login <api-key>",
        )
        for command in cases:
            with self.subTest(command=command[:12]):
                text = page(step(1, command, "ok"), declared="<!-- owner-values: the-password, api-key -->")
                self.assertRefused(self.check(text), "asks for a secret")

    def test_a_secret_read_from_the_environment_is_not_refused(self):
        text = page(step(1, "echo --token $MY_TOKEN", "ok"))
        self.assertFalse(any("secret" in line for line in self.check(text)))

    def test_a_page_that_omits_a_host_is_refused(self):
        for omitted in ("Antigravity", "Codex", "Claude"):
            with self.subTest(omitted=omitted):
                hosts = "Antigravity, Codex and Claude Code.\n".replace(omitted, "Other")
                self.assertRefused(self.check(page(step(1, "echo a", "a"), hosts=hosts)), "omits the host")

    def test_a_placeholder_must_be_declared(self):
        text = page(step(1, "echo <workspace>", "ok"))
        self.assertRefused(self.check(text), "<workspace>, which the page does not declare")
        declared = page(step(1, "echo <workspace>", "ok"), declared="<!-- owner-values: workspace -->")
        self.assertEqual(self.check(declared), [])

    def test_steps_out_of_order_are_refused(self):
        text = page(step(2, "echo a", "a"), step(1, "echo b", "b"))
        self.assertRefused(self.check(text), "out of order")

    def test_the_same_step_twice_is_refused(self):
        self.assertRefused(self.check(page(step(1, "echo a", "a"), step(1, "echo b", "b"))), "appears twice")
        self.assertRefused(self.check(page(step(1, "echo a", "a"), step(2, "echo a", "a"))), "repeats the command")

    def test_a_page_with_no_step_is_refused(self):
        self.assertRefused(self.check(HOSTS + "Just prose.\n"), "no numbered step")

    def test_a_runnable_step_whose_output_differs_is_refused(self):
        text = page(step(1, VERIFY, "^NO-DATA"), step(2, "echo a", "a"))
        self.assertEqual(self.check(text, runner_for("NO-DATA: the file is not recorded yet")), [])
        refusals = self.check(text, runner_for("GREEN: all fine"))
        self.assertRefused(refusals, "does not match the expected block")

    def test_a_runnable_step_is_run_through_the_runner_with_its_argv(self):
        runner = runner_for("NO-DATA: x")
        self.check(page(step(1, VERIFY, "^NO-DATA")), runner)
        self.assertEqual(runner.calls, [VERIFY.split()])

    def test_a_runnable_step_with_no_runner_is_no_data(self):
        refusals = self.check(page(step(1, VERIFY, "^NO-DATA")))
        self.assertRefused(refusals, "NO-DATA")

    def test_a_runner_that_fails_or_answers_badly_is_no_data(self):
        text = page(step(1, VERIFY, "^NO-DATA"))

        def boom(argv, cwd, env):
            raise OSError("gone")
        self.assertRefused(self.check(text, boom), "NO-DATA: the command runner raised OSError")
        for answer in (None, 0, "NO-DATA", (True, "NO-DATA"), (0, None), (0, "a", "b")):
            with self.subTest(answer=answer):
                self.assertRefused(self.check(text, lambda a, c, e, r=answer: r), "NO-DATA: the command runner returned")

    def test_a_runnable_expected_block_must_be_a_valid_regex(self):
        self.assertRefused(self.check(page(step(1, VERIFY, "(unclosed")), runner_for("x")), "not a valid regular")

    def test_the_real_page_passes_when_the_commands_print_what_it_expects(self):
        outputs = {
            "antigravity": "# HP1 Antigravity live proof, run hp1-x-1: quit Antigravity, then start it from this "
                           "terminal\nexport A=b",
            "codex": "# HP1 Codex live proof, run hp1-y-2: sign in once, then run the three probes non interactively",
            "claude": "# HP1 Claude Code live proof, run hp1-z-3: sign in to Claude Code once, then run the three probes "
                      "non interactively",
        }

        def run(argv, cwd, env):
            if argv[1].endswith("host_live_verify.py"):
                return 3, "NO-DATA: docs/plan/evidence/HP1-host-live.jsonl is not recorded yet"
            return 0, outputs[argv[-1]]
        real = os.path.join(REPO, PAGE)
        self.assertTrue(os.path.isfile(real), "the owner page is missing")
        self.assertEqual(hdc.check_owner_steps(REPO, PAGE, run), [])
        with open(real, encoding="utf-8") as fh:
            found = hdc.steps(fh.read())
        self.assertEqual([s["number"] for s in found], [1, 2, 3])   # 9de057782: each host ends with collect

    def test_hostile_input_is_refused_never_a_crash(self):
        for root in (None, 5, True, [], {}, "", "\x00", float("nan"), os.path.join(self.root, "nope")):
            with self.subTest(root=root):
                self.assertTrue(hdc.check_owner_steps(root, PAGE))
        for bad in (None, 5, True, [], {}, "", "\x00", float("nan"), b"x", "missing.md"):
            with self.subTest(page=bad):
                self.assertTrue(hdc.check_owner_steps(self.root, bad))
        for bad in (None, 5, True, [], {}, float("nan"), b"x"):
            with self.subTest(text=bad):
                with self.assertRaises(ValueError):
                    hdc.steps(bad)
        for bad in (None, 5, True, "x", [], float("nan"), {"expected": 5}, {"expected": []}, {"expected": None},
                    {"expected": {}}, {"number": True, "expected": float("nan")}):
            with self.subTest(step=bad):
                self.assertTrue(hdc.expected_output_refusals(bad))
        for runner in (5, "x", [], {}):
            self.assertTrue(self.check(page(step(1, VERIFY, "^NO-DATA")), runner))

    def test_a_binary_page_is_refused(self):
        with open(os.path.join(self.root, PAGE), "wb") as fh:
            fh.write(b"\xff\xfe\x00 not utf8")
        self.assertTrue(hdc.check_owner_steps(self.root, PAGE))


if __name__ == "__main__":
    unittest.main()
