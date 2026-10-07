#!/usr/bin/env python3
"""followup_obligation.py on a temporary run directory: the id is TRIGGER_KEYS
only, validation refuses hostile and forbidden input, persist is idempotent,
and the fold keeps the first OBLIGATION row and counts what it skips.

Fixtures are deliberately tiny: journal.MAX_LINE_BYTES is PIPE_BUF, 512 bytes
on macOS, and an obligation row that does not fit is refused, not truncated.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import journal  # noqa: E402
import followup_obligation as fo  # noqa: E402
import task_watchdog  # noqa: E402

DUE = "2026-10-03T09:00Z"
EXPIRES = "2026-10-04T09:00Z"


def trigger(subject="s", **extra):
    t = {"kind": "r", "subject": subject, "due_at": DUE, "owner": "o"}
    t.update(extra)
    return t


def auth(scope=None):
    return {"granted_by": "g", "scope": [] if scope is None else scope,
            "action_ref": "a"}


def plant(run_dir, row):
    """A row written straight to the file, as a second writer or a corrupt
    tool would leave it; the fold must cope with whatever is there."""
    base = {"event_id": "e%d" % id(row), "parent_ids": [], "run_id": "r",
            "session_id": None, "unit_id": None, "at": DUE}
    base.update(row)
    with open(os.path.join(run_dir, journal.JOURNAL_FILENAME), "a") as fh:
        fh.write(json.dumps(base) + "\n")
    return base["event_id"]


def obligation_payload(t, owner="o"):
    return {"obligation_id": fo.obligation_id(t), "trigger": t,
            "owner": owner, "expires_at": EXPIRES, "authorization": auth()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fo")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def rows(self):
        return journal.read(self.tmp) or []


class ObligationId(Base):
    def test_extra_key_is_excluded_from_the_id(self):
        self.assertEqual(fo.obligation_id(trigger()),
                         fo.obligation_id(trigger(note="decorative")))
        self.assertNotEqual(fo.obligation_id(trigger("s")),
                            fo.obligation_id(trigger("t")))

    def test_missing_key_raises_naming_it(self):
        t = trigger()
        del t["due_at"]
        with self.assertRaisesRegex(ValueError, "due_at"):
            fo.obligation_id(t)


class Validation(Base):
    def test_valid_passes(self):
        fo.validate_trigger(trigger(), "o", EXPIRES, auth(["remind"]))
        fo.validate_trigger(trigger(due_at="2026-10-03T09:00:00+02:00"), "o",
                            "2026-10-03T09:00:00+02:00", auth())

    def test_each_problem_is_refused(self):
        cases = [
            (dict(trigger=trigger(kind="")), "kind"),
            (dict(trigger=trigger(due_at="2026-10-03T09:00:00")), "due_at"),
            (dict(trigger=trigger(due_at="tomorrow")), "due_at"),
            (dict(expires_at="2026-10-04"), "expires_at"),
            (dict(expires_at="2026-10-02T09:00Z"), "before"),
            (dict(owner=""), "owner"),
            (dict(authorization={"scope": [], "action_ref": "a"}),
             "granted_by"),
            (dict(authorization={"granted_by": "g", "action_ref": "a"}),
             "scope"),
            (dict(authorization={"granted_by": "g", "scope": []}),
             "action_ref"),
            (dict(authorization=auth("remind")), "scope"),
        ]
        for change, word in cases:
            args = dict(trigger=trigger(), owner="o", expires_at=EXPIRES,
                        authorization=auth())
            args.update(change)
            with self.subTest(change=change):
                with self.assertRaisesRegex(ValueError, word):
                    fo.validate_trigger(**args)

    def test_forbidden_scope_is_refused_and_never_persisted(self):
        for scope in ("external-send", "third-party-send", "publish",
                      " External-Send "):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(ValueError, "forbidden"):
                    fo.persist(self.tmp, trigger(), "o", EXPIRES,
                               auth(["remind", scope]))
        self.assertIsNone(journal.read(self.tmp))

    def test_hostile_inputs_are_refused_never_typeerror(self):
        hostile = [None, True, 1, float("nan"), [], {}, ["x"], "", {1}]
        for bad in hostile:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    fo.validate_trigger(bad, "o", EXPIRES, auth())
                with self.assertRaises(ValueError):
                    fo.validate_trigger(trigger(subject=bad), "o", EXPIRES,
                                        auth())
                with self.assertRaises(ValueError):
                    fo.validate_trigger(trigger(), bad, EXPIRES, auth())
                with self.assertRaises(ValueError):
                    fo.validate_trigger(trigger(), "o", bad, auth())
                with self.assertRaises(ValueError):
                    fo.validate_trigger(trigger(), "o", EXPIRES, bad)
                with self.assertRaises(ValueError):
                    fo.persist(self.tmp, trigger(), "o", EXPIRES, bad)
                if not isinstance(bad, list):
                    with self.assertRaises(ValueError):
                        fo.validate_trigger(trigger(), "o", EXPIRES, {
                            "granted_by": "g", "scope": bad,
                            "action_ref": "a"})
                with self.assertRaises(ValueError):
                    fo.obligation_id(bad)
        with self.assertRaisesRegex(ValueError, "scope"):
            fo.validate_trigger(trigger(), "o", EXPIRES, auth("x"))
        with self.assertRaises(ValueError):
            fo.obligation_id(trigger(subject={1, 2}))
        self.assertIsNone(journal.read(self.tmp))


class Persist(Base):
    def test_persist_twice_appends_one_row(self):
        first = fo.persist(self.tmp, trigger(), "o", EXPIRES, auth())
        second = fo.persist(self.tmp, trigger(note="x"), "o", EXPIRES, auth())
        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertEqual(len(self.rows()), 1)
        folded = fo.read_obligations(self.tmp)
        record = folded[fo.obligation_id(trigger())]
        self.assertEqual(record["event_id"], first)
        self.assertEqual(record["owner"], "o")
        self.assertEqual(folded["duplicates"], 0)
        self.assertEqual(folded["malformed"], 0)

    def test_empty_run_dir_writes_nothing(self):
        self.assertIsNone(fo.persist("", trigger(), "o", EXPIRES, auth()))
        self.assertIsNone(fo.persist(None, trigger(), "o", EXPIRES, auth()))

    def test_journal_refusal_returns_none(self):
        missing = os.path.join(self.tmp, "nope")
        self.assertIsNone(fo.persist(missing, trigger(), "o", EXPIRES, auth()))

    def test_oversized_row_is_refused_not_truncated(self):
        big = trigger(subject="x" * journal.MAX_LINE_BYTES)
        self.assertIsNone(fo.persist(self.tmp, big, "o", EXPIRES, auth()))
        self.assertIsNone(journal.read(self.tmp))

    def test_completed_id_is_a_noop_returning_original(self):
        first = fo.persist(self.tmp, trigger(), "o", EXPIRES, auth())
        plant(self.tmp, {"type": fo.COMPLETED, "payload": {
            "obligation_id": fo.obligation_id(trigger())}})
        self.assertEqual(fo.persist(self.tmp, trigger(), "o", EXPIRES,
                                    auth()), first)
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue(
            fo.read_obligations(self.tmp)[fo.obligation_id(trigger())]
            ["completed"])

    def test_many_ids_in_one_journal(self):
        ids = [fo.persist(self.tmp, trigger(s), "o", EXPIRES, auth())
               for s in ("a", "b", "c")]
        self.assertEqual(len(set(ids)), 3)
        folded = fo.read_obligations(self.tmp)
        self.assertEqual(
            sorted(k for k, v in folded.items() if isinstance(v, dict)),
            sorted(fo.obligation_id(trigger(s)) for s in ("a", "b", "c")))


class ReadObligations(Base):
    def test_no_journal_is_none(self):
        self.assertIsNone(fo.read_obligations(self.tmp))

    def test_first_obligation_row_wins_and_later_is_a_duplicate(self):
        t = trigger()
        first = plant(self.tmp, {"event_id": "e1", "type": fo.OBLIGATION,
                                 "payload": obligation_payload(t, "first")})
        plant(self.tmp, {"event_id": "e2", "type": fo.OBLIGATION,
                         "payload": obligation_payload(t, "second")})
        folded = fo.read_obligations(self.tmp)
        record = folded[fo.obligation_id(t)]
        self.assertEqual(record["owner"], "first")
        self.assertEqual(record["event_id"], first)
        self.assertEqual(record["duplicates"], 1)
        self.assertEqual(folded["duplicates"], 1)
        self.assertEqual(fo.persist(self.tmp, t, "o", EXPIRES, auth()), "e1")

    def test_marks_fold_in_file_order(self):
        t = trigger()
        oid = fo.persist(self.tmp, t, "o", EXPIRES, auth())
        key = fo.obligation_id(t)
        n1 = plant(self.tmp, {"event_id": "n1", "type": fo.NOTIFIED,
                              "payload": {"obligation_id": key}})
        n2 = plant(self.tmp, {"event_id": "n2", "type": fo.NOTIFIED,
                              "payload": {"obligation_id": key}})
        plant(self.tmp, {"type": fo.CANCELLED,
                         "payload": {"obligation_id": key}})
        record = fo.read_obligations(self.tmp)[key]
        self.assertEqual(record["event_id"], oid)
        self.assertEqual(record["notified"], [n1, n2])
        self.assertTrue(record["cancelled"])
        self.assertFalse(record["completed"])

    def test_malformed_rows_are_skipped_and_counted(self):
        t = trigger()
        good = obligation_payload(t)
        plant(self.tmp, {"type": fo.OBLIGATION, "payload": "junk"})
        plant(self.tmp, {"type": fo.OBLIGATION,
                         "payload": dict(good, obligation_id="0" * 64)})
        plant(self.tmp, {"type": fo.OBLIGATION, "payload": dict(
            good, authorization=auth(["publish"]))})
        plant(self.tmp, {"type": fo.OBLIGATION, "payload": {
            "payload_truncated": "NO-DATA"}})
        plant(self.tmp, {"type": fo.NOTIFIED,
                         "payload": {"obligation_id": "duplicates"}})
        plant(self.tmp, {"type": fo.NOTIFIED, "payload": None})
        plant(self.tmp, {"type": "unrelated", "payload": "ignored"})
        with open(os.path.join(self.tmp, journal.JOURNAL_FILENAME),
                  "a") as fh:
            fh.write("[1, 2]\n")
        folded = fo.read_obligations(self.tmp)
        self.assertEqual(folded["malformed"], 6)
        self.assertNotIn(fo.obligation_id(t), folded)
        self.assertTrue(fo.persist(self.tmp, t, "o", EXPIRES, auth()))
        self.assertIn(fo.obligation_id(t), fo.read_obligations(self.tmp))

    def test_two_writers_both_append_fold_keeps_first(self):
        t = trigger()
        plant(self.tmp, {"event_id": "w1", "type": fo.OBLIGATION,
                         "payload": obligation_payload(t)})
        plant(self.tmp, {"event_id": "w2", "type": fo.OBLIGATION,
                         "payload": obligation_payload(t)})
        folded = fo.read_obligations(self.tmp)
        self.assertEqual(folded[fo.obligation_id(t)]["event_id"], "w1")
        self.assertEqual(folded["duplicates"], 1)


PAST_DUE = "2000-01-01T00:00Z"
FAR_EXPIRES = "2999-01-01T00:00Z"
NOW = "2026-10-03T12:00Z"


def state(**change):
    """One folded obligation as read_obligations returns it: due, unexpired."""
    record = {"event_id": "e0", "trigger": trigger(), "owner": "o",
              "expires_at": EXPIRES, "authorization": auth(),
              "notified": [], "cancelled": False, "completed": False,
              "duplicates": 0}
    record.update(change)
    return record


class Reconcile(Base):
    def test_due_obligation_is_notified(self):
        self.assertEqual(fo.reconcile(state(), NOW), ("notify", "due"))
        self.assertEqual(fo.reconcile(state(), NOW, []), ("notify", "due"))

    def test_one_fixture_per_skip_reason(self):
        cases = [
            (state(cancelled=True), NOW, None, "cancelled"),
            (state(completed=True), NOW, None, "completed"),
            (state(notified=["n1"]), NOW, None, "notified"),
            (state(), EXPIRES, None, "expired"),
            (state(), "2026-10-03T08:59Z", None, "not-due"),
            (state(), NOW, [["2026-10-03T11:00Z", "2026-10-03T13:00Z"]],
             "quiet"),
            (state(), "not a time", None, "malformed"),
        ]
        for record, now, quiet, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(fo.reconcile(record, now, quiet),
                                 ("skip", reason))

    def test_cancelled_obligation_is_never_notified(self):
        # The owner who cancelled their own obligation sees skip, not a nag.
        self.assertEqual(fo.reconcile(state(cancelled=True), NOW),
                         ("skip", "cancelled"))

    def test_order_first_reason_wins(self):
        self.assertEqual(
            fo.reconcile(state(cancelled=True, completed=True,
                               notified=["n"]), "bad"),
            ("skip", "cancelled"))
        self.assertEqual(
            fo.reconcile(state(completed=True, notified=["n"]), NOW),
            ("skip", "completed"))
        # Stale: expired after being due is 'expired', not-due never asked.
        self.assertEqual(fo.reconcile(state(), "2026-10-05T00:00Z"),
                         ("skip", "expired"))
        self.assertEqual(
            fo.reconcile(state(), "2026-10-03T08:00Z",
                         [["2026-10-03T07:00Z", "2026-10-03T09:00Z"]]),
            ("skip", "not-due"))

    def test_quiet_window_is_half_open(self):
        windows = [["2026-10-01T00:00Z", "2026-10-01T06:00Z"],
                   ["2026-10-03T10:00Z", "2026-10-03T12:00Z"],
                   ["2026-10-03T20:00Z", "2026-10-03T22:00Z"]]
        self.assertEqual(fo.reconcile(state(), "2026-10-03T12:00Z", windows),
                         ("notify", "due"))
        self.assertEqual(fo.reconcile(state(), "2026-10-03T10:00Z", windows),
                         ("skip", "quiet"))
        self.assertEqual(fo.reconcile(state(), "2026-10-03T11:59Z", windows),
                         ("skip", "quiet"))
        self.assertEqual(fo.reconcile(state(), "2026-10-03T13:00Z", windows),
                         ("notify", "due"))

    def test_unparsable_clock_is_malformed_never_now(self):
        # Due long ago and expiring far ahead: a guessed "now" would notify.
        record = state(trigger=trigger(due_at=PAST_DUE),
                       expires_at=FAR_EXPIRES)
        self.assertEqual(fo.reconcile(record, NOW), ("notify", "due"))
        for bad in ("not a time", "", "2026-10-03T12:00:00", None, 1, True,
                    float("nan"), [], {}):
            with self.subTest(bad=bad):
                self.assertEqual(fo.reconcile(record, bad),
                                 ("skip", "malformed"))

    def test_empty_and_hostile_states_are_malformed(self):
        hostile = [{}, None, True, 1, float("nan"), "s", ["x"], {1},
                   state(cancelled="yes"), state(completed=1),
                   state(notified="n"), state(notified=None),
                   state(expires_at="tomorrow"), state(trigger=None),
                   state(trigger=trigger(due_at="soon")),
                   state(authorization=auth(["publish"])),
                   state(owner="")]
        for bad in hostile:
            with self.subTest(bad=bad):
                self.assertEqual(fo.reconcile(bad, NOW),
                                 ("skip", "malformed"))

    def test_hostile_quiet_is_malformed(self):
        for bad in ("2026-10-03T11:00Z", 1, True, {}, [["a", "b"]],
                    [["2026-10-03T11:00Z"]], [None], [{1}],
                    [["2026-10-03T13:00Z", "2026-10-03T11:00Z"]],
                    [["2026-10-03T11:00Z", float("nan")]]):
            with self.subTest(bad=bad):
                self.assertEqual(fo.reconcile(state(), NOW, bad),
                                 ("skip", "malformed"))


class Mark(Base):
    def test_mark_appends_one_row_that_the_fold_reads(self):
        fo.persist(self.tmp, trigger(), "o", EXPIRES, auth())
        oid = fo.obligation_id(trigger())
        event_id = fo.mark(self.tmp, oid, fo.CANCELLED, "o")
        self.assertTrue(event_id)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["type"], fo.CANCELLED)
        self.assertEqual(rows[-1]["event_id"], event_id)
        self.assertEqual(rows[-1]["payload"], {"obligation_id": oid,
                                               "by": "o"})
        self.assertTrue(fo.read_obligations(self.tmp)[oid]["cancelled"])
        self.assertTrue(fo.mark(self.tmp, oid, fo.COMPLETED, "o"))
        self.assertTrue(fo.read_obligations(self.tmp)[oid]["completed"])

    def test_other_row_type_raises(self):
        fo.persist(self.tmp, trigger(), "o", EXPIRES, auth())
        oid = fo.obligation_id(trigger())
        for bad in (fo.NOTIFIED, fo.OBLIGATION, "cancelled", None, 1, True,
                    ["x"], {1}, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    fo.mark(self.tmp, oid, bad, "o")
        for bad in ("", None, 1, True, ["o"]):
            with self.subTest(by=bad):
                with self.assertRaises(ValueError):
                    fo.mark(self.tmp, oid, fo.CANCELLED, bad)
        self.assertEqual(len(self.rows()), 1)

    def test_unknown_id_is_refused_with_none(self):
        fo.persist(self.tmp, trigger(), "o", EXPIRES, auth())
        for bad in ("0" * 64, "duplicates", "malformed", None, 1, ["x"],
                    {1}, float("nan")):
            with self.subTest(bad=bad):
                self.assertIsNone(fo.mark(self.tmp, bad, fo.CANCELLED, "o"))
        self.assertIsNone(fo.mark(os.path.join(self.tmp, "nope"),
                                  fo.obligation_id(trigger()),
                                  fo.CANCELLED, "o"))
        self.assertIsNone(fo.mark("", fo.obligation_id(trigger()),
                                  fo.CANCELLED, "o"))
        self.assertEqual(len(self.rows()), 1)


class Notify(Base):
    def due_obligation(self):
        t = trigger(due_at=PAST_DUE)
        self.assertTrue(fo.persist(self.tmp, t, "o", FAR_EXPIRES, auth()))
        return fo.obligation_id(t)

    def test_notify_twice_appends_one_row(self):
        oid = self.due_obligation()
        verdict, event_id = fo.notify(self.tmp, oid, NOW)
        self.assertEqual(verdict, "notified")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["type"], fo.NOTIFIED)
        self.assertEqual(rows[-1]["event_id"], event_id)
        self.assertEqual(rows[-1]["payload"], {"obligation_id": oid,
                                               "at": NOW})
        self.assertEqual(fo.notify(self.tmp, oid, NOW), ("skip", "notified"))
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(fo.read_obligations(self.tmp)[oid]["notified"],
                         [event_id])

    def test_cancelled_by_owner_is_never_notified(self):
        oid = self.due_obligation()
        self.assertTrue(fo.mark(self.tmp, oid, fo.CANCELLED, "o"))
        self.assertEqual(fo.notify(self.tmp, oid, NOW),
                         ("skip", "cancelled"))
        self.assertEqual(len(self.rows()), 2)

    def test_skip_appends_nothing(self):
        oid = self.due_obligation()
        self.assertEqual(
            fo.notify(self.tmp, oid, NOW,
                      [["2026-10-03T11:00Z", "2026-10-03T13:00Z"]]),
            ("skip", "quiet"))
        self.assertEqual(fo.notify(self.tmp, oid, "bad"),
                         ("skip", "malformed"))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(fo.notify(self.tmp, oid, "2026-10-03T13:00Z",
                                   [["2026-10-03T11:00Z",
                                     "2026-10-03T13:00Z"]])[0], "notified")

    def test_unknown_id_and_missing_journal_are_malformed(self):
        missing = os.path.join(self.tmp, "nope")
        self.assertEqual(fo.notify(missing, "0" * 64, NOW),
                         ("skip", "malformed"))
        self.assertEqual(fo.notify("", "0" * 64, NOW), ("skip", "malformed"))
        self.due_obligation()
        for bad in ("0" * 64, "duplicates", "malformed", None, 1, True,
                    ["x"], {1}, float("nan")):
            with self.subTest(bad=bad):
                self.assertEqual(fo.notify(self.tmp, bad, NOW),
                                 ("skip", "malformed"))
        self.assertEqual(len(self.rows()), 1)


class RenderLine(Base):
    def record(self, subject="s", **change):
        r = state(trigger=trigger(subject), authorization=auth(["remind",
                                                                "local"]))
        r.update(change)
        return r

    def test_exact_shape(self):
        oid = fo.obligation_id(trigger())
        self.assertEqual(
            fo.render_line(oid, self.record()),
            "FOLLOW-UP %s due %s owner o: r s (authorized by g, scope "
            "remind,local, action a)" % (oid[:12], DUE))
        self.assertIn("scope none,", fo.render_line(
            oid, self.record(authorization=auth())))

    def test_long_subject_is_cut_with_three_dots_under_400(self):
        oid = fo.obligation_id(trigger())
        line = fo.render_line(oid, self.record("x" * 1000))
        self.assertLess(len(line), 400)
        self.assertEqual(len(line), 399)
        self.assertIn("xxx... (authorized by g", line)
        self.assertTrue(line.endswith("action a)"))

    def test_line_breaks_never_reach_the_line(self):
        oid = fo.obligation_id(trigger())
        line = fo.render_line(oid, self.record("a\nb\rc\u2028d\x00e"))
        self.assertEqual(line.splitlines(), [line])
        self.assertIn("a b c d e", line)

    def test_hostile_inputs_raise_valueerror(self):
        oid = fo.obligation_id(trigger())
        for bad in (None, "", 1, True, ["x"], {1}, float("nan")):
            with self.subTest(oid=bad):
                with self.assertRaises(ValueError):
                    fo.render_line(bad, self.record())
        for bad in (None, {}, 1, True, ["x"], "s", float("nan"),
                    state(trigger=None), state(owner=None),
                    state(authorization=None),
                    state(authorization=auth("x"))):
            with self.subTest(folded=bad):
                with self.assertRaises(ValueError):
                    fo.render_line(oid, bad)


def gate_answers(*answers):
    """A stub gate answering `answers` in order, recording what it was
    asked."""
    asked = []
    queue = list(answers)

    def stub(observables, dial=None):
        asked.append(observables)
        return queue.pop(0)
    stub.asked = asked
    return stub


def refuse(*args, **kwargs):
    raise AssertionError("an external sender was reached")


class DueLines(Base):
    """task_watchdog.due_lines on a temporary run directory, fixed clock."""

    def add(self, subject):
        t = trigger(subject, due_at=PAST_DUE)
        self.assertTrue(fo.persist(self.tmp, t, "o", FAR_EXPIRES, auth()))
        return fo.obligation_id(t)

    def notified_rows(self):
        return [r for r in self.rows() if r.get("type") == fo.NOTIFIED]

    def run_due(self, gate="execute_then_check", quiet=None, now=NOW):
        stub = gate if callable(gate) else (lambda o, dial=None: gate)
        with mock.patch.object(task_watchdog, "autonomy_gate", stub):
            return task_watchdog.due_lines(self.tmp, now, quiet)

    def test_due_cancelled_and_notified_with_stub_gate(self):
        due = self.add("d")
        cancelled = self.add("c")
        self.assertTrue(fo.mark(self.tmp, cancelled, fo.CANCELLED, "o"))
        done = self.add("n")
        self.assertEqual(fo.notify(self.tmp, done, NOW)[0], "notified")
        before = len(self.rows())
        stub = gate_answers("refuse_until_approved")
        lines = self.run_due(stub)
        rendered = fo.render_line(due, fo.read_obligations(self.tmp)[due])
        self.assertEqual(lines, ["HELD " + rendered])
        self.assertEqual(stub.asked, [task_watchdog.FOLLOW_UP_OBSERVABLES])
        self.assertEqual(len(self.rows()), before)
        self.assertEqual(fo.read_obligations(self.tmp)[due]["notified"], [])
        lines = self.run_due(gate_answers("execute_then_check"))
        self.assertEqual(lines, [rendered])
        self.assertEqual(len(self.rows()), before + 1)
        self.assertEqual(len(fo.read_obligations(self.tmp)[due]["notified"]),
                         1)

    def test_gate_refusal_holds_one_and_prints_the_other(self):
        first, second = self.add("a"), self.add("b")
        lines = self.run_due(gate_answers("refuse_until_approved",
                                          "execute_then_check"))
        folded = fo.read_obligations(self.tmp)
        self.assertEqual(lines, ["HELD " + fo.render_line(first, folded[first]),
                                 fo.render_line(second, folded[second])])
        self.assertEqual(folded[first]["notified"], [])
        self.assertEqual(len(folded[second]["notified"]), 1)

    def test_live_dial_default_holds_and_a0_prints(self):
        oid = self.add("a")
        env = dict(os.environ)
        env.pop("BROTHER_AUTONOMY_DIAL", None)
        with mock.patch.dict(os.environ, env, clear=True):
            held = task_watchdog.due_lines(self.tmp, NOW)
        self.assertEqual(len(held), 1)
        self.assertTrue(held[0].startswith("HELD FOLLOW-UP %s" % oid[:12]))
        self.assertEqual(self.notified_rows(), [])
        with mock.patch.dict(os.environ, {"BROTHER_AUTONOMY_DIAL": "A0"}):
            shown = task_watchdog.due_lines(self.tmp, NOW)
        self.assertEqual(shown, [held[0][len("HELD "):]])
        self.assertEqual(len(self.notified_rows()), 1)

    def test_empty_run_dir_is_empty(self):
        for empty in ("", None, "   "):
            with self.subTest(run_dir=empty):
                self.assertEqual(task_watchdog.due_lines(empty, NOW), [])

    def test_no_journal_is_empty_and_missing_dir_is_no_data(self):
        self.assertEqual(self.run_due(), [])
        lines = task_watchdog.due_lines(os.path.join(self.tmp, "nope"), NOW)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("FOLLOW-UP NO-DATA"))

    def test_module_missing_is_one_no_data_line(self):
        with mock.patch.dict(sys.modules, {"followup_obligation": None}):
            self.assertEqual(task_watchdog.due_lines(self.tmp, NOW),
                             ["FOLLOW-UP NO-DATA: module missing"])

    def test_no_external_sender_is_reached(self):
        self.add("a")
        with mock.patch.dict(sys.modules, {"bm_vault_notify": None}), \
                mock.patch.object(subprocess, "run", refuse), \
                mock.patch.object(subprocess, "Popen", refuse), \
                mock.patch.object(subprocess, "call", refuse), \
                mock.patch.object(subprocess, "check_output", refuse), \
                mock.patch.object(os, "system", refuse):
            lines = self.run_due()
        self.assertEqual(len(lines), 1)

    def snapshot(self):
        out = {}
        sbe = os.path.join(str(task_watchdog.REPO_ROOT), ".sbe")
        for where in (self.tmp, sbe, str(task_watchdog.REPO_ROOT)):
            if not os.path.isdir(where):
                continue
            for name in os.listdir(where):
                path = os.path.join(where, name)
                st = os.stat(path)
                out[path] = (st.st_size, st.st_mtime_ns)
        return out

    def test_only_the_journal_changes(self):
        self.add("a")
        before = self.snapshot()
        self.assertEqual(len(self.run_due()), 1)
        after = self.snapshot()
        changed = sorted(p for p in set(before) | set(after)
                         if before.get(p) != after.get(p))
        self.assertEqual(changed, [os.path.join(self.tmp,
                                                journal.JOURNAL_FILENAME)])

    def test_many_print_one_line_each_in_journal_order(self):
        ids = [self.add(s) for s in ("c", "a", "b")]
        lines = self.run_due()
        self.assertEqual([line.split()[1] for line in lines],
                         [oid[:12] for oid in ids])

    def test_same_session_restarting_stays_quiet(self):
        self.add("a")
        self.assertEqual(len(self.run_due()), 1)
        self.assertEqual(self.run_due(), [])
        self.assertEqual(len(self.notified_rows()), 1)

    def test_stale_and_not_due_print_nothing(self):
        t = trigger("late", due_at=PAST_DUE)
        fo.persist(self.tmp, t, "o", "2001-01-01T00:00Z", auth())
        fo.persist(self.tmp, trigger("soon", due_at="2999-01-01T00:00Z"),
                   "o", FAR_EXPIRES, auth())
        self.assertEqual(self.run_due(), [])
        self.assertEqual(self.notified_rows(), [])

    def test_quiet_window_prints_nothing(self):
        self.add("a")
        self.assertEqual(
            self.run_due(quiet=[["2026-10-03T11:00Z", "2026-10-03T13:00Z"]]),
            [])
        self.assertEqual(self.notified_rows(), [])

    def test_corrupt_line_is_skipped_and_the_rest_printed(self):
        first = self.add("a")
        with open(os.path.join(self.tmp, journal.JOURNAL_FILENAME),
                  "a") as fh:
            fh.write("{torn\n")
        second = self.add("b")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            lines = self.run_due()
        self.assertEqual([line.split()[1] for line in lines],
                         [first[:12], second[:12]])
        self.assertIn("torn line", err.getvalue())

    def test_concurrent_race_prints_at_most_twice(self):
        oid = self.add("a")
        for n in ("r1", "r2"):
            plant(self.tmp, {"event_id": n, "type": fo.NOTIFIED,
                             "payload": {"obligation_id": oid, "at": NOW}})
        self.assertEqual(fo.read_obligations(self.tmp)[oid]["notified"],
                         ["r1", "r2"])
        self.assertEqual(self.run_due(), [])

    def test_hostile_inputs_are_no_data_never_typeerror(self):
        self.add("a")
        for bad in (1, True, ["x"], {1}, float("nan")):
            with self.subTest(run_dir=bad):
                lines = task_watchdog.due_lines(bad, NOW)
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith("FOLLOW-UP NO-DATA"))
        for bad in (None, "", "not a time", "2026-10-03T12:00:00", 1, True,
                    ["x"], {1}, float("nan")):
            with self.subTest(now=bad):
                lines = self.run_due(now=bad)
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith(
                    "FOLLOW-UP NO-DATA: now_utc"))
        for bad in ("x", 1, True, {}, [["a", "b"]], [None], [{1}]):
            with self.subTest(quiet=bad):
                lines = self.run_due(quiet=bad)
                self.assertEqual(lines, [
                    "FOLLOW-UP NO-DATA: quiet windows do not parse"])
        self.assertEqual(self.notified_rows(), [])


class TriageHook(Base):
    """main --triage prints the due lines after the triage lines and before
    the ready set, and never changes the exit code."""

    def run_main(self, due=None):
        patches = [
            mock.patch.object(task_watchdog, "read_registry",
                              lambda: None),
            mock.patch.object(task_watchdog, "load_triage_offset",
                              lambda *a, **k: 0),
            mock.patch.object(task_watchdog, "read_day_plan_rows",
                              lambda *a, **k: []),
            mock.patch.dict(os.environ, {journal.RUN_DIR_ENV_VAR: self.tmp,
                                         "BROTHER_AUTONOMY_DIAL": "A0"}),
        ]
        if due is not None:
            patches.append(mock.patch.object(task_watchdog, "due_lines", due))
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with contextlib.redirect_stdout(out):
                code = task_watchdog.main(["--triage"])
        return code, out.getvalue().splitlines()

    def test_due_line_is_printed_before_the_ready_set(self):
        t = trigger("a", due_at=PAST_DUE)
        fo.persist(self.tmp, t, "o", FAR_EXPIRES, auth())
        code, lines = self.run_main()
        self.assertEqual(code, 2)
        follow = [i for i, line in enumerate(lines) if "FOLLOW-UP" in line]
        ready = [i for i, line in enumerate(lines) if "ready set" in line]
        self.assertEqual(len(follow), 1)
        self.assertTrue(lines[follow[0]].startswith(
            "task-watchdog: FOLLOW-UP %s" % fo.obligation_id(t)[:12]))
        self.assertEqual(len(ready), 1)
        self.assertLess(follow[0], ready[0])

    def test_a_raising_due_lines_is_one_no_data_line_same_exit(self):
        def boom(*args):
            raise RuntimeError("journal\nexploded")
        code, lines = self.run_main(boom)
        self.assertEqual(code, 2)
        self.assertIn("task-watchdog: FOLLOW-UP NO-DATA: journal exploded",
                      lines)
        self.assertTrue(any("ready set" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
