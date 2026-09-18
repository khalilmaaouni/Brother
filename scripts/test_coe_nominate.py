#!/usr/bin/env python3
"""Calibration for scripts/coe_nominate.py.

Every forbidden term used below is FAKE, chosen the same way
test_coe_outside_gate.py chooses its own fixtures: never a real client or
team term from this estate's private-terms lists, so a scanner's test file
cannot itself become the leak this subsystem exists to prevent.

WHY THE DETERMINISM TESTS ARE SHAPED THE WAY THEY ARE. The worker brief
warns, correctly, that "if you iterate a set or a dict whose order varies,
this property dies silently and the test will still usually pass." That
warning is sharper than it first looks: Python's string hashing is
randomized ONCE PER PROCESS (PYTHONHASHSEED, random by default since
Python 3.3), so a `set` of seat-id strings iterates in a STABLE order for
the whole lifetime of one process, but a DIFFERENT order in a different
process (most of the time; there is a small chance two random seeds happen
to agree). A test that calls nominate() twenty times in a loop, in this
same test process, would therefore stay green even if nominate() secretly
built its output order from a set, because the hash seed never changes
mid-process. TestD1DeterminismInProcess below is kept anyway, because the
brief asks for it literally and it is real (cheap) coverage against
non-hash-seed bugs (accidental randomness, accidental clock or PID use).
But it is not, by itself, evidence against the hash-seed bug, and it says
so. TestD1DeterminismAcrossProcesses is the test that actually is: it
shells out to a fresh Python interpreter twenty times, forcing a different
PYTHONHASHSEED each time, and compares the JSON each subprocess printed.
scripts/coe_nominate.py's own nomination loop never iterates a set or a
dict to decide output order (see that module's DETERMINISM section); this
suite proves it rather than assuming it.
"""
import copy
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coe_nominate as N  # noqa: E402
import coe_outside_gate as G  # noqa: E402

# A fake category and a fake short term, matching test_coe_outside_gate.py's
# own convention exactly ("Vex" hidden inside the ordinary word "convex").
FAKE_TERMS = {"fake-client-terms": ["Vex"]}


def _seat(seat_id, owns_attributes, model="opus", outside=False,
          authoritative_on=None):
    """A minimal seat dict, shaped like docs/plan/COE-SEATS.json's own
    entries. Not run through COE-01's schema validator: nominate()
    trusts a caller-supplied `seats=` override exactly as its own
    docstring says it does, so these fixtures only need to carry the
    keys nominate() itself reads (id, owns_attributes, model, outside).
    """
    return {
        "id": seat_id,
        "owns_attributes": list(owns_attributes),
        "model": model,
        "effort": "high",
        "outside": outside,
        "cost_class": "medium",
        "authoritative_on": list(authoritative_on) if authoritative_on else [],
    }


def _problem(problem_id="p1", attributes=None, risk_class="low",
             content_leaves_machine=False, content=None):
    problem = {
        "id": problem_id,
        "attributes": list(attributes) if attributes else [],
        "risk_class": risk_class,
        "content_leaves_machine": content_leaves_machine,
    }
    if content is not None:
        problem["content"] = content
    return problem


# A small closed seat pool used throughout, deliberately NOT the real
# registry: architect (opus) and operations (sonnet) supply two in-vendor
# families; adversary-outside supplies a third, outside family, gated.
SEATS = [
    _seat("architect", ["duplicate-truth", "boundaries", "component-reuse"],
          model="opus"),
    _seat("operations", ["false-refusal", "liveness", "stalling"],
          model="sonnet"),
    _seat("security", ["privacy", "trust-boundary", "leakage"],
          model="opus"),
    _seat("adversary-outside", ["independent-family", "structural-critique"],
          model="an outside model", outside=True),
]


class TestProblemValidation(unittest.TestCase):
    """Malformed input is refused before any nomination logic runs."""

    def test_non_dict_problem_refused(self):
        with self.assertRaises(N.NominationRefused):
            N.nominate("not-a-dict", seats=SEATS)

    def test_missing_required_key_refused(self):
        problem = _problem(attributes=["duplicate-truth"])
        del problem["risk_class"]
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS)
        self.assertIn("risk_class", str(ctx.exception))

    def test_attributes_not_a_list_refused(self):
        problem = _problem(attributes=["duplicate-truth"])
        problem["attributes"] = "duplicate-truth"
        with self.assertRaises(N.NominationRefused):
            N.nominate(problem, seats=SEATS)

    def test_unknown_attribute_refused_names_the_attribute(self):
        problem = _problem(attributes=["warp-drive-calibration"])
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS)
        self.assertIn("warp-drive-calibration", str(ctx.exception))

    def test_unknown_risk_class_refused(self):
        problem = _problem(attributes=["duplicate-truth"], risk_class="none")
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS)
        self.assertIn("none", str(ctx.exception))

    def test_content_leaves_machine_not_bool_refused(self):
        problem = _problem(attributes=["duplicate-truth"])
        problem["content_leaves_machine"] = "false"
        with self.assertRaises(N.NominationRefused):
            N.nominate(problem, seats=SEATS)


class TestEmptyAttributesEdge(unittest.TestCase):
    """Edge: a problem with no attributes. Refused explicitly, before any
    seat is even looked at, never silently returned as an empty council.
    """

    def test_no_attributes_refused_with_explicit_reason(self):
        problem = _problem(attributes=[])
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS)
        self.assertIn("no attributes", str(ctx.exception))


class TestZeroSeatPoolEdge(unittest.TestCase):
    """Edge: a registry (seat pool) that loads zero seats."""

    def test_empty_seat_pool_refused(self):
        problem = _problem(attributes=["duplicate-truth"])
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=[])
        self.assertIn("zero seats", str(ctx.exception))


class TestNoMatchingSeatEdge(unittest.TestCase):
    """Edge: an attribute that is valid (in coe_registry.KNOWN_ATTRIBUTES)
    but that no seat in the pool owns. Real, expected data (per COE-01's
    own seats_owning() semantics), not a schema error, but it still leaves
    nomination with nobody to seat, so the whole nomination refuses.
    """

    def test_attribute_no_seat_owns_refuses(self):
        pool = [_seat("architect", ["duplicate-truth"], model="opus")]
        problem = _problem(attributes=["observability"])  # nobody owns it
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=pool)
        self.assertIn("no seat", str(ctx.exception))


class TestD2StructureNotAvailability(unittest.TestCase):
    """A seat is seated because the problem carries an attribute it owns,
    and the rationale names that attribute. Also covers the "exactly one
    attribute" and "every attribute maps to the same single seat" edges.
    """

    def test_exactly_one_attribute_seats_its_owner_with_named_rationale(self):
        problem = _problem(attributes=["privacy"])
        council = N.nominate(problem, seats=SEATS)
        self.assertEqual(council.seats, ("security",))
        self.assertEqual(council.rationale["security"], "privacy")

    def test_privacy_attribute_always_seats_security(self):
        # The exact property named in the brief: a privacy attribute
        # always seats the security seat, and the rationale names it.
        problem = _problem(
            attributes=["false-refusal", "privacy", "duplicate-truth"])
        council = N.nominate(problem, seats=SEATS)
        self.assertIn("security", council.seats)
        self.assertEqual(council.rationale["security"], "privacy")

    def test_every_attribute_mapping_to_one_seat_seats_it_once(self):
        problem = _problem(
            attributes=["duplicate-truth", "boundaries", "component-reuse"])
        council = N.nominate(problem, seats=SEATS)
        self.assertEqual(council.seats, ("architect",))
        # Rationale names the FIRST attribute that matched, never a list.
        self.assertEqual(council.rationale["architect"], "duplicate-truth")

    def test_every_seat_matching_seats_all_of_them_in_order(self):
        shared_pool = [
            _seat("a-seat", ["privacy"], model="opus"),
            _seat("b-seat", ["privacy"], model="sonnet"),
            _seat("c-seat", ["privacy"], model="opus"),
        ]
        problem = _problem(attributes=["privacy"])
        council = N.nominate(problem, seats=shared_pool)
        self.assertEqual(council.seats, ("a-seat", "b-seat", "c-seat"))


class TestD3NoSelfReview(unittest.TestCase):
    def test_author_excluded_and_appears_in_refusals(self):
        problem = _problem(attributes=["duplicate-truth", "false-refusal"])
        council = N.nominate(problem, seats=SEATS, author="architect")
        self.assertNotIn("architect", council.seats)
        self.assertIn("operations", council.seats)
        refused_ids = [r["seat"] for r in council.refusals]
        self.assertIn("architect", refused_ids)
        reason = next(r["reason"] for r in council.refusals
                      if r["seat"] == "architect")
        self.assertIn("author", reason.lower())
        self.assertIn("D3", reason)

    def test_author_none_excludes_nobody(self):
        problem = _problem(attributes=["duplicate-truth"])
        council = N.nominate(problem, seats=SEATS, author=None)
        self.assertEqual(council.seats, ("architect",))

    def test_author_naming_unregistered_seat_is_a_documented_no_op(self):
        problem = _problem(attributes=["duplicate-truth"])
        with_ghost_author = N.nominate(
            problem, seats=SEATS, author="no-such-seat-anywhere")
        without_author = N.nominate(problem, seats=SEATS, author=None)
        self.assertEqual(with_ghost_author, without_author)


class TestD4FamilyDiversity(unittest.TestCase):
    def test_high_risk_with_two_families_succeeds(self):
        problem = _problem(
            attributes=["duplicate-truth", "false-refusal"],
            risk_class="high")
        council = N.nominate(problem, seats=SEATS)
        self.assertEqual(set(council.seats), {"architect", "operations"})
        self.assertEqual(council.families, ("opus", "sonnet"))

    def test_high_risk_with_one_family_raises(self):
        problem = _problem(
            attributes=["duplicate-truth", "boundaries"],  # both architect
            risk_class="high")
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS)
        self.assertIn("D4", str(ctx.exception))

    def test_critical_risk_with_one_family_raises(self):
        problem = _problem(
            attributes=["duplicate-truth", "boundaries"],
            risk_class="critical")
        with self.assertRaises(N.NominationRefused):
            N.nominate(problem, seats=SEATS)

    def test_low_risk_with_one_family_does_not_raise(self):
        # D4 only binds at high/critical; below that a single-family
        # council is real, accepted data.
        problem = _problem(
            attributes=["duplicate-truth", "boundaries"], risk_class="low")
        council = N.nominate(problem, seats=SEATS)
        self.assertEqual(council.families, ("opus",))

    def test_medium_risk_with_one_family_does_not_raise(self):
        problem = _problem(
            attributes=["duplicate-truth", "boundaries"],
            risk_class="medium")
        council = N.nominate(problem, seats=SEATS)
        self.assertEqual(council.families, ("opus",))


class TestOutsideGateIntegration(unittest.TestCase):
    def test_outside_seat_refused_when_content_leaves_machine_false(self):
        calls = []
        original_check = G.check
        G.check = lambda *a, **k: calls.append(1) or original_check(*a, **k)
        try:
            problem = _problem(
                attributes=["independent-family"],
                content_leaves_machine=False)
            with self.assertRaises(N.NominationRefused) as ctx:
                N.nominate(problem, seats=SEATS)
            # The council ended up empty (the only candidate was refused),
            # so the top-level message is the generic "matched no seat"
            # one; the SPECIFIC reason lives in .refusals, which is what a
            # caller actually inspects to learn why.
            outside_refusal = next(
                r for r in ctx.exception.refusals
                if r["seat"] == "adversary-outside")
            self.assertIn("content_leaves_machine", outside_refusal["reason"])
            # The gate must never even be called when the flag is False:
            # the decision is made on the flag alone, before any scan.
            self.assertEqual(calls, [])
        finally:
            G.check = original_check

    def test_outside_seat_refused_when_content_missing(self):
        problem = _problem(
            attributes=["independent-family"], content_leaves_machine=True)
        # No "content" key at all.
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS)
        outside_refusal = next(
            r for r in ctx.exception.refusals
            if r["seat"] == "adversary-outside")
        self.assertIn("no content to scan", outside_refusal["reason"])

    def test_outside_seat_nominated_when_content_clean(self):
        problem = _problem(
            attributes=["independent-family"],
            content_leaves_machine=True,
            content="nothing forbidden lives in this sentence")
        council = N.nominate(
            problem, seats=SEATS, outside_gate_terms=FAKE_TERMS)
        self.assertEqual(council.seats, ("adversary-outside",))

    def test_outside_seat_refused_when_gate_refuses_never_names_the_term(self):
        problem = _problem(
            attributes=["independent-family"],
            content_leaves_machine=True,
            content="please do not Vex the cat")
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS, outside_gate_terms=FAKE_TERMS)
        outside_refusal = next(
            r for r in ctx.exception.refusals
            if r["seat"] == "adversary-outside")
        message = outside_refusal["reason"]
        self.assertIn("outside gate refused", message)
        self.assertIn("fake-client-terms", message)
        self.assertNotIn("vex", message.lower())

    def test_false_positive_measured_word_does_not_block_the_seat(self):
        # The exact shape test_coe_outside_gate.py measures: a forbidden
        # term embedded in an ordinary word ("convex") must not trip the
        # gate, so the outside seat is still nominated.
        problem = _problem(
            attributes=["independent-family"],
            content_leaves_machine=True,
            content="the lens has a convex shape")
        council = N.nominate(
            problem, seats=SEATS, outside_gate_terms=FAKE_TERMS)
        self.assertEqual(council.seats, ("adversary-outside",))


class TestD4VersusGateCollision(unittest.TestCase):
    """The case named explicitly in the brief: a high-risk problem whose
    ONLY second family is an outside seat, and the gate refuses it. The
    right answer is to refuse the whole nomination, never to proceed with
    one family or to let the gate wave the content through.
    """

    def test_collision_refuses_the_whole_nomination(self):
        problem = _problem(
            attributes=["duplicate-truth", "independent-family"],
            risk_class="high",
            content_leaves_machine=True,
            content="please do not Vex the cat")
        with self.assertRaises(N.NominationRefused) as ctx:
            N.nominate(problem, seats=SEATS, outside_gate_terms=FAKE_TERMS)
        exc = ctx.exception
        self.assertIn("D4", exc.reason)
        # The refusal names WHY the second family was missing: the outside
        # seat's own gate refusal is visible in .refusals, not hidden
        # behind a bare "could not satisfy D4".
        outside_refusal = next(
            r for r in exc.refusals if r["seat"] == "adversary-outside")
        self.assertIn("outside gate refused", outside_refusal["reason"])
        self.assertNotIn("vex", outside_refusal["reason"].lower())

    def test_collision_resolved_when_gate_allows(self):
        problem = _problem(
            attributes=["duplicate-truth", "independent-family"],
            risk_class="high",
            content_leaves_machine=True,
            content="nothing forbidden lives in this sentence")
        council = N.nominate(
            problem, seats=SEATS, outside_gate_terms=FAKE_TERMS)
        self.assertEqual(
            set(council.seats), {"architect", "adversary-outside"})
        self.assertEqual(council.families, ("an outside model", "opus"))


class TestSameProblemTwoCallers(unittest.TestCase):
    """Edge: the same problem nominated twice by different callers (here,
    two independent call sites in the same process) must agree exactly.
    """

    def test_two_separate_calls_agree(self):
        problem = _problem(
            attributes=["duplicate-truth", "false-refusal"],
            risk_class="high")

        def caller_one():
            return N.nominate(copy.deepcopy(problem), seats=SEATS)

        def caller_two():
            return N.nominate(copy.deepcopy(problem), seats=SEATS)

        self.assertEqual(caller_one(), caller_two())


class TestD1DeterminismInProcess(unittest.TestCase):
    """The literal ask: twenty calls, same problem, compare the full
    ordered seat list each time, not a set. See the module docstring for
    why this alone does not prove hash-seed-independence; that is
    TestD1DeterminismAcrossProcesses below.
    """

    def test_twenty_in_process_calls_agree_on_full_ordered_council(self):
        problem = _problem(
            attributes=["duplicate-truth", "false-refusal", "privacy"],
            risk_class="high")
        results = [N.nominate(copy.deepcopy(problem), seats=SEATS)
                   for _ in range(20)]
        first = results[0]
        for council in results[1:]:
            self.assertEqual(council.seats, first.seats)  # ordered compare
            self.assertEqual(council, first)


class TestD1DeterminismAcrossProcesses(unittest.TestCase):
    """The test that can actually catch "iterates a set, order varies":
    twenty fresh interpreters, twenty different PYTHONHASHSEED values, one
    nomination each, and the full ordered seat list compared across all
    twenty. If coe_nominate.py ever iterated a set of seat ids (or a plain
    dict keyed by seat id built from something other than list order) to
    decide Council.seats, a different hash seed would, on most seeds,
    produce a different order, and this test would go red. It is
    the seats= override that makes this self-contained: no dependency on
    the real docs/plan/COE-SEATS.json being unchanged on disk.
    """

    _SUBPROCESS_SCRIPT = (
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "import coe_nominate as N\n"
        "problem = json.loads(sys.argv[1])\n"
        "seats = json.loads(sys.argv[2])\n"
        "council = N.nominate(problem, seats=seats)\n"
        "print(json.dumps({\n"
        "    'seats': list(council.seats),\n"
        "    'rationale': council.rationale,\n"
        "    'families': list(council.families),\n"
        "}))\n"
    ) % HERE

    def _nominate_in_fresh_process(self, problem, seats, hash_seed):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = str(hash_seed)
        result = subprocess.run(
            [sys.executable, "-c", self._SUBPROCESS_SCRIPT,
             json.dumps(problem), json.dumps(seats)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise AssertionError(
                "subprocess with PYTHONHASHSEED=%s failed (exit %d):\n%s"
                % (hash_seed, result.returncode, result.stderr))
        return json.loads(result.stdout)

    def test_twenty_hash_seeds_agree_on_full_ordered_council(self):
        problem = _problem(
            attributes=["duplicate-truth", "false-refusal", "privacy"],
            risk_class="high")
        outputs = [
            self._nominate_in_fresh_process(problem, SEATS, hash_seed)
            for hash_seed in range(20)
        ]
        first = outputs[0]
        for output in outputs[1:]:
            self.assertEqual(output["seats"], first["seats"])
            self.assertEqual(output["rationale"], first["rationale"])
            self.assertEqual(output["families"], first["families"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
