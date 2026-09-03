"""The canonical protocol schema, each law calibrated by attempting the violation.

Four laws under test, from docs/specs/canonical-project-protocol.md:
  1. Round trip: to_dict/from_dict carries every canonical field, exactly.
  2. validate() names every missing or ill-typed required field, all at once.
  3. transition() refuses an illegal move naming from-state, to-state and the
     legal moves, refuses `done` BY NAME, and refuses a silent backward move.
  4. DRIFT: the field lists, enums and state list in the CODE are parsed out
     of the DOCUMENT's own YAML blocks and state list, so the two cannot
     disagree silently. When this test fails, the document wins.

The event stream this module used to test (append_event, read_events,
_iter_open_stream) is retired, Loop 1 delete-list D3: storage moved to
tools/bm_store.py schema 12, and this module is shapes and legality only.
"""
import io
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SPEC = os.path.join(REPO, "docs", "specs", "canonical-project-protocol.md")
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from brotherme.core import schema  # noqa: E402


def _valid(cls):
    """A fully populated, valid instance of each shape. Values are boring on
    purpose: the tests are about the shape, not the content."""
    stamps = {"created_at": "2026-07-31T00:00:00Z",
              "updated_at": "2026-07-31T00:00:00Z",
              "timestamp": "2026-07-31T00:00:00Z"}
    fills = {
        "confidence": "medium", "actor_type": "model", "severity": "high",
        "status": "planned", "requires_human": True,
    }
    fields = {}
    for name in cls.FIELDS:
        if name in stamps:
            fields[name] = stamps[name]
        elif name in fills:
            fields[name] = fills[name]
        elif name in cls.LIST_FIELDS:
            fields[name] = ["item one for %s" % name]
        else:
            fields[name] = "value for %s" % name
    return cls(**fields)


class TestRoundTrip(unittest.TestCase):
    def test_every_shape_round_trips_through_to_dict_and_from_dict(self):
        for cls in schema.SHAPES:
            obj = _valid(cls).validate()
            again = cls.from_dict(obj.to_dict())
            self.assertEqual(obj, again, cls.__name__)
            self.assertEqual(obj.to_dict(), again.to_dict(), cls.__name__)

    def test_from_dict_names_a_missing_field(self):
        data = _valid(schema.Project).to_dict()
        del data["user_outcome"]
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.Project.from_dict(data)
        self.assertIn("user_outcome", str(ctx.exception))
        self.assertIn("missing", str(ctx.exception))

    def test_from_dict_names_an_unknown_field(self):
        data = _valid(schema.Alert).to_dict()
        data["done"] = True
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.Alert.from_dict(data)
        self.assertIn("unknown", str(ctx.exception))
        self.assertIn("done", str(ctx.exception))


class TestValidateNamesEveryField(unittest.TestCase):
    def test_two_missing_fields_are_both_named_in_one_error(self):
        obj = _valid(schema.Project)
        obj.project_id = None
        obj.created_at = ""
        with self.assertRaises(schema.SchemaError) as ctx:
            obj.validate()
        text = str(ctx.exception)
        self.assertIn("project_id", text)
        self.assertIn("created_at", text)

    def test_an_ill_typed_list_field_is_named(self):
        obj = _valid(schema.Task)
        obj.write_scope = "src/pay.py"
        with self.assertRaises(schema.SchemaError) as ctx:
            obj.validate()
        self.assertIn("write_scope", str(ctx.exception))
        self.assertIn("list", str(ctx.exception))

    def test_a_bad_enum_value_is_named_with_the_legal_values(self):
        obj = _valid(schema.Forecast)
        obj.confidence = "certain"
        with self.assertRaises(schema.SchemaError) as ctx:
            obj.validate()
        text = str(ctx.exception)
        self.assertIn("confidence", text)
        for legal in ("low", "medium", "high"):
            self.assertIn(legal, text)

    def test_requires_human_must_be_a_real_bool(self):
        obj = _valid(schema.Alert)
        obj.requires_human = "yes"
        with self.assertRaises(schema.SchemaError) as ctx:
            obj.validate()
        self.assertIn("requires_human", str(ctx.exception))
        self.assertIn("bool", str(ctx.exception))


class TestTransitions(unittest.TestCase):
    def _task(self, status="planned"):
        obj = _valid(schema.Task)
        obj.status = status
        return obj

    def test_the_legal_forward_chain_is_allowed_end_to_end(self):
        task = self._task("planned")
        for nxt in ("ready", "active", "awaiting review", "verified",
                    "accepted", "delivered", "monitored", "closed"):
            schema.transition(task, nxt)
            self.assertEqual(task.status, nxt)

    def test_blocking_and_unblocking_are_legal(self):
        task = self._task("active")
        schema.transition(task, "blocked")
        self.assertEqual(task.status, "blocked")
        schema.transition(task, "active")
        self.assertEqual(task.status, "active")

    def test_a_skipped_stage_is_refused_naming_both_states_and_the_legal_moves(self):
        task = self._task("planned")
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.transition(task, "accepted")
        text = str(ctx.exception)
        self.assertIn("planned", text)
        self.assertIn("accepted", text)
        self.assertIn("ready", text, "the legal move must be named")
        self.assertEqual(task.status, "planned", "a refusal must not move")

    def test_done_is_refused_by_name_with_the_spec_rule(self):
        task = self._task("verified")
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.transition(task, "done")
        text = str(ctx.exception)
        self.assertIn("done", text.lower())
        self.assertIn("not a valid state", text)
        self.assertEqual(task.status, "verified")

    def test_a_task_may_not_carry_done_as_a_status_either(self):
        task = self._task("done")
        with self.assertRaises(schema.SchemaError) as ctx:
            task.validate()
        self.assertIn("not a valid state", str(ctx.exception))

    def test_a_backward_move_without_a_reason_is_refused_and_with_one_allowed(self):
        task = self._task("awaiting review")
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.transition(task, "active")
        self.assertIn("reason", str(ctx.exception))
        self.assertEqual(task.status, "awaiting review")
        schema.transition(task, "active",
                          reason="reviewer found the totals off by one")
        self.assertEqual(task.status, "active")

    def test_closed_is_terminal(self):
        task = self._task("closed")
        with self.assertRaises(schema.SchemaError):
            schema.transition(task, "monitored")
        self.assertEqual(task.status, "closed")

    def test_closed_refuses_a_backward_move_even_with_a_reason(self):
        """Regression: the first build allowed any backward move out of
        closed when a reason was given, so 'finished for good' was not
        true and the terminality test above passed vacuously (it only
        caught the missing-reason refusal)."""
        task = self._task("closed")
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.transition(task, "planned", reason="oops, reopen it")
        self.assertIn("finished for good", str(ctx.exception))
        self.assertEqual(task.status, "closed")


class TestDriftAgainstTheDocument(unittest.TestCase):
    """Parse the spec's own YAML blocks and state list and hold the code to
    them, field for field, in order. The document is the source of truth."""

    @classmethod
    def setUpClass(cls):
        with io.open(SPEC, encoding="utf-8") as fh:
            cls.spec = fh.read()
        cls.blocks = re.findall(r"```yaml\n(.*?)```", cls.spec, re.S)

    def _fields(self, block):
        out = []
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            self.assertIn(":", line, "not a field line: %r" % line)
            out.append(line.split(":", 1)[0].strip())
        return tuple(out)

    def _enum(self, block, field):
        for line in block.splitlines():
            if line.strip().startswith(field + ":"):
                value = line.split(":", 1)[1]
                return tuple(p.strip() for p in value.split("|"))
        self.fail("field %s not found in block" % field)

    def test_the_document_has_exactly_five_yaml_shapes(self):
        self.assertEqual(len(self.blocks), 5,
                         "the spec must carry exactly the five shapes")

    def test_every_field_list_matches_the_document_exactly_in_order(self):
        for cls, block in zip(schema.SHAPES, self.blocks):
            self.assertEqual(
                cls.FIELDS, self._fields(block),
                "%s has drifted from the spec's YAML shape. The document "
                "is the source of truth; fix brotherme/core/schema.py."
                % cls.__name__)

    def test_the_enumerated_values_match_the_document(self):
        self.assertEqual(schema.Forecast.ENUMS["confidence"],
                         self._enum(self.blocks[1], "confidence"))
        self.assertEqual(schema.AttributionEvent.ENUMS["actor_type"],
                         self._enum(self.blocks[3], "actor_type"))
        self.assertEqual(schema.Alert.ENUMS["severity"],
                         self._enum(self.blocks[4], "severity"))

    def test_the_ten_states_match_the_documents_numbered_list_in_order(self):
        listed = tuple(re.findall(r"^\s*\d+\.\s+`([^`]+)`", self.spec, re.M))
        self.assertEqual(len(listed), 10, "the spec numbers exactly ten states")
        self.assertEqual(schema.STATES, listed)

    def test_every_transition_target_is_a_real_state(self):
        self.assertEqual(tuple(schema.LEGAL_TRANSITIONS), schema.STATES)
        for state, targets in schema.LEGAL_TRANSITIONS.items():
            for target in targets:
                self.assertIn(target, schema.STATES,
                              "%s points at %s" % (state, target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
