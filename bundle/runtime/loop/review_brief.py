#!/usr/bin/env python3
"""Assemble an adversarial review brief for one worker build: the whole unit spec plus the build JSON.
The reviewer's findings are a PROPOSAL the orchestrator re-runs by hand; it never decides a PASS.
usage: review_brief.py <spec.md> <sub> <build.json> <out.md>"""
import sys
spec_path, sub, build_path, out = sys.argv[1:5]
with open(spec_path, encoding="utf-8") as f: spec = f.read()
with open(build_path, encoding="utf-8") as f: build = f.read()
text = ("House laws: unknown, corrupt or missing input BLOCKS or is NO-DATA, never the safe case; a control that prevents beats a check that reports; "
"every check must be able to go red. No person, client or employer names. No long dashes. Say UNKNOWN rather than guess.\n\n"
"You are the RED TEAM for ONE worker build of sub unit %s. A sandbox grader may already have passed it: its tests are red without the code, green with it, and its own mutations are caught. "
"That proves the code agrees with ITS OWN tests, nothing more. Today seven builds passed that grader and were still wrong, always in one of these ways. Check each, in order:\n"
"1. SPEC ALGORITHM: list every numbered algorithm step and every REQ line in the specification that names this sub unit's functions. For each, quote the line of the build that implements it, or write MISSING. A missing safety tier is the most common defect.\n"
"2. TESTS THAT ASSERT THE DEFECT: name any test whose expected value contradicts the specification.\n"
"3. HOSTILE INPUTS: for each public function give concrete calls (wrong type, unhashable, None, NaN, empty, a str where a list is expected, a bool where an int is expected, a path shaped id, a missing file, a file that raises or exits at import, counts that cannot all be true) and say for each whether the build REFUSES, CRASHES with a raw exception, or ACCEPTS. Only name a crash or accept you can trace in the code shown.\n"
"4. SURVIVABLE MUTATIONS: name one line semantic breaks the build's tests would NOT catch, with the missing test in one sentence.\n"
"5. SHARED MODULE RISK: if it patches an existing file, name any existing behaviour it changes.\n"
"End with one line: VERDICT: LAND AS IS, or VERDICT: FIX FIRST followed by the numbered fixes, smallest first. If you find nothing real, say so. Do not invent findings. At most 900 words.\n\n"
"THE WHOLE UNIT SPECIFICATION:\n%s\n\nTHE BUILD (JSON of edits, tests, mutations):\n%s\n" % (sub, spec, build))
with open(out, "w", encoding="utf-8") as f: f.write(text)
print(len(text.encode()))
