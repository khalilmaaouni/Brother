#!/usr/bin/env python3
"""FX5.a (docs/plan/specs/FX5.md): .github/workflows/required-fast.yml gains a
manual dispatch job that runs only on the private hub, so the candidate
export tree can run on GitHub's Linux before the owner's y. The first job,
the public check of record, is unchanged.

The two job guards are turned into a truth table (event by repository
visibility) and compared with the intent, not compared as strings. The
canary binds the tree it checked by printing it first, and its comment says
the payload's private field is UNVERIFIED for a dispatch (plan v5 rulings).

Python 3.9, standard library only. No network. No em or en dashes.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "required-fast.yml")
GUARD1 = "${{ !github.event.repository.private }}"
GUARD2 = "${{ github.event_name == 'workflow_dispatch' && github.event.repository.private }}"
BIND = ["- name: Bind the candidate tree", "run: git rev-parse HEAD 'HEAD^{tree}'"]
_TOKEN = re.compile(r"\s*(github\.event\.repository\.private|github\.event_name|'workflow_dispatch'|"
                    r"'pull_request'|==|&&|\|\||!|\(|\))")


def load(path=WORKFLOW):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _block(text, key):
    """The lines under the top level `key:` up to the next top level key."""
    lines, out, inside = text.splitlines(), [], False
    for line in lines:
        if re.match(r"^\S", line):
            inside = line.split(":", 1)[0].strip() == key
            continue
        if inside:
            out.append(line)
    return out


def triggers_of(text):
    """The keys indented two spaces directly under on:."""
    return [m.group(1) for m in (re.match(r"^  ([A-Za-z_]+):", l) for l in _block(text, "on")) if m]


def jobs_of(text):
    """Job name to its block, jobs being the two space keys under jobs:, in file order."""
    jobs, name = {}, None
    for line in _block(text, "jobs"):
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            name = m.group(1)
            jobs[name] = []
        elif name:
            jobs[name].append(line)
    return dict((k, "\n".join(v)) for k, v in jobs.items())


def job_if(block):
    m = re.search(r"^    if:\s*(.*?)\s*$", block, re.M)
    return m.group(1) if m else None


def job_steps(block):
    """The stripped lines from steps: to the end of the block, comment and blank lines dropped."""
    lines = block.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "steps:"), None)
    if start is None:
        return []
    return [l.strip() for l in lines[start + 1:] if l.strip() and not l.strip().startswith("#")]


def runs(expr, event, private):
    """Evaluate a job guard built only from the two context fields, the two event names, ==, &&, ||, ! and
    parentheses; ValueError on any other token."""
    m = re.fullmatch(r"\$\{\{(.*)\}\}", (expr or "").strip())
    if not m:
        raise ValueError("not a ${{ }} expression: %r" % (expr,))
    body, pos, out = m.group(1), 0, []
    while body[pos:].strip():
        t = _TOKEN.match(body, pos)
        if not t:
            raise ValueError("token not allowed in a job guard: %r" % body[pos:].strip())
        pos = t.end()
        out.append({"github.event.repository.private": "P", "github.event_name": "E", "&&": "and",
                    "||": "or", "!": "not"}.get(t.group(1), t.group(1)))
    # eval is safe here: every token was matched against the whitelist above (two names, two string literals,
    # ==, and, or, not, parentheses), so nothing else can reach it, and it runs with no builtins.
    return bool(eval(" ".join(out), {"__builtins__": {}}, {"P": private, "E": event}))


class TheCanaryRunsOnlyByHandOnThePrivateHub(unittest.TestCase):

    def setUp(self):
        self.text = load()
        self.jobs = jobs_of(self.text)

    def test_the_triggers_are_the_pull_request_and_the_dispatch_only(self):
        self.assertEqual(triggers_of(self.text), ["pull_request", "workflow_dispatch"])
        for auto in ("push:", "schedule:", "pull_request_target:", "workflow_run:"):
            with self.subTest(trigger=auto):
                self.assertNotIn(auto, self.text)

    def test_the_public_job_keeps_its_guard_byte_for_byte(self):
        self.assertEqual(job_if(self.jobs["required-fast"]), GUARD1)
        self.assertIn("\n    if: %s\n" % GUARD1, self.text)

    def test_the_canary_job_carries_its_own_guard(self):
        self.assertEqual(list(self.jobs), ["required-fast", "required-fast-canary"])
        self.assertEqual(job_if(self.jobs["required-fast-canary"]), GUARD2)

    def test_only_the_stated_job_runs_for_each_event_and_visibility(self):
        want = {("pull_request", False): (True, False), ("pull_request", True): (False, False),
                ("workflow_dispatch", False): (True, False), ("workflow_dispatch", True): (False, True)}
        for (event, private), expected in want.items():
            with self.subTest(event=event, private=private):
                got = tuple(runs(job_if(self.jobs[name]), event, private)
                            for name in ("required-fast", "required-fast-canary"))
                self.assertEqual(got, expected)

    def test_the_canary_runs_the_same_steps_after_binding_its_tree(self):
        first = job_steps(self.jobs["required-fast"])
        canary = job_steps(self.jobs["required-fast-canary"])
        self.assertIn("run: sh scripts/required_fast.sh", first)
        self.assertEqual(canary[1:3], BIND, "the canary prints the commit and tree it checked, right after checkout")
        self.assertEqual(canary[:1] + canary[3:], first)

    def test_both_jobs_are_linux_bounded_and_independent(self):
        for name, block in self.jobs.items():
            with self.subTest(job=name):
                self.assertIn("    runs-on: ubuntu-latest", block)
                timeout = re.search(r"^    timeout-minutes:\s*(\d+)\s*$", block, re.M)
                self.assertTrue(timeout and int(timeout.group(1)) <= 20, block)
        self.assertNotIn("needs:", self.text)

    def test_a_guard_of_any_other_shape_is_refused(self):
        for expr in ("${{ github.actor == 'x' }}", "${{ __import__('os') }}", "github.event_name", ""):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    runs(expr, "workflow_dispatch", True)

    def test_the_canary_says_what_it_does_not_know(self):
        comments = " ".join(l.strip() for l in self.jobs["required-fast-canary"].splitlines()
                            if l.strip().startswith("#"))
        self.assertIn("UNVERIFIED", comments)
        self.assertIn("github.event.repository.private", comments)
        self.assertNotIn("exact", comments.lower())


if __name__ == "__main__":
    unittest.main()
