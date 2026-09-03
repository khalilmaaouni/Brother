#!/usr/bin/env python3
"""Suite for tools/bm_visual.py: the drawn vocabulary, the alert ladder,
the insight box and the rewritten refusals (design
docs/program/absolute-lead/DESIGN-visual-surface.md sections 6, 7, 9.2,
12.1 and 13.1).

HOW A GENERATED VISUAL IS TESTED HERE, because "test the HTML" otherwise
degrades into asserting substrings (design section 13.0):

  1. Parse, do not grep. Every SVG assertion runs against a real
     xml.etree parse of the emitted markup, so a malformed drawing fails
     as a parse error rather than as a missing substring.
  2. Content assertions are set equalities against the rows the drawing
     came from, in both directions: every row reaches a node, and every
     node resolves to a row.
  3. Structural invariants are computed, never eyeballed: node counts,
     edge counts, contrast ratios, the absence of a colour attribute.

Every store this file builds comes from bv.bs, the bm_store module
tools/bm_visual.py itself loaded, so a refusal raised inside the module
and a refusal this file catches are the SAME class. That is the rule
DESIGN-L04 section 17.6 states and tools/test_bm_lead.py repeats.

Python 3.9, standard library only. No network.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import ast
import datetime
import importlib.util
import io
import os
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(HERE, "bm_store.py")
CONTROLLER_FILE = os.path.join(HERE, "bm_controller.py")


def _load(name):
    """Load a sibling module by PATH, the same technique tools/bm_lead.py,
    tools/bm_controller.py and tools/bm_autonomy.py use for bm_store."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_optional(name):
    """None instead of an exception when the module is not there yet.

    Deliberate, and copied from tools/test_bm_lead.py: on the untouched
    tree tools/bm_visual.py does not exist, and a bare import failure here
    would collapse this whole file into ONE error, which is not per-class
    failing-first evidence. With this, every class fails on its own with a
    message naming the missing file, so the RED capture shows a count per
    class."""
    try:
        return _load(name)
    except Exception:  # sbe: allow-silent module not shipped yet, per docstring each class fails on its own with a named message
        return None


bv = _load_optional("bm_visual")
bs = bv.bs if bv is not None else _load("bm_store")
bl = bv.bl if bv is not None else _load("bm_lead")


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _store_reason_codes():
    """Every reason code tools/bm_store.py can emit, read from the SHIPPED
    source with ast rather than from a hand list. A hand list is the thing
    L-S9 exists to make impossible: it goes stale the day somebody adds a
    refusal, and the founder meets the raw code."""
    # Every CONSTRUCTION of an OwnershipRefused counts, not only the ones
    # raised in place. A refusal built by a helper and returned to its caller
    # to raise (_read_only_refusal builds 'store-unreadable' that way) reaches
    # the founder identically, and walking only ast.Raise made this guard
    # report that code as one the store cannot emit.
    tree = ast.parse(_read(STORE_FILE))
    codes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(
            func, "attr", "")
        if name != "OwnershipRefused" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            codes.add(first.value)
    return codes


def _controller_reason_codes():
    """Every refusal reason code tools/bm_controller.py can emit, read from
    the SHIPPED source the same way the store's are.

    WHY A SECOND SCANNER. The store scanner above cannot see these. The
    unattended preflight refuses with codes of its own rather than through
    an OwnershipRefused, so a REFUSAL_HELP enumerated from bm_store.py
    alone left seven codes a founder can already meet with no rewrite, and
    `bm_view.py explain --reason unattended-fence-advisory` answered that
    it was not a reason this product emits, which was false.

    The technique is the one tools/test_bm_controller.py uses over the same
    constants: a module-level assignment whose target starts with
    UNATTENDED_ and whose value is a string starting with 'unattended-'.
    That shape is what separates the reason codes from
    UNATTENDED_FENCE_MODE_COMMAND and UNATTENDED_FENCE_STRICT_COMMAND,
    which hold the commands the rewrites quote rather than codes anything
    refuses with."""
    tree = ast.parse(_read(CONTROLLER_FILE))
    codes = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.startswith("unattended-")):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Name)
                    and target.id.startswith("UNATTENDED_")):
                codes.add(value.value)
    return codes


class _Clock(object):
    """A controllable now_iso, so ages are driven by ARRANGED timestamps
    rather than by sleeping. Shape copied from tools/test_bm_lead.py."""

    FORMAT = "%Y-%m-%dT%H:%M:%SZ"

    def __init__(self, start="2026-08-05T09:00:00Z"):
        self.at = datetime.datetime.strptime(start, self.FORMAT)

    def iso(self):
        return self.at.strftime(self.FORMAT)

    def advance(self, seconds):
        self.at = self.at + datetime.timedelta(seconds=seconds)
        return self.iso()


def _actor(name="coordinator"):
    return {"actor_type": "model", "actor_name": name,
            "session_id": "sess-visual"}


def _insight(**kw):
    row = {"kind": "LEARNING", "subject": "the widget",
           "claim": "the widget builds from a clean checkout",
           "evidence": "python3 tools/test_bm_visual.py",
           "evidence_class": "EXECUTED", "confidence": "high",
           "confidence_basis": "it is a measurement and it repeats",
           "flip_condition": "the build fails on a clean checkout",
           "alternatives": [{"option": "trust the last build",
                             "why_not": "it ran on a dirty tree"}]}
    row.update(kw)
    return row


def _key_decision(**kw):
    row = _insight(kind="DECISION", subject="the payment approach",
                   claim="use hosted checkout",
                   decision_class="RULE", control_offered=1,
                   evidence_class="READ", evidence="tools/bm_store.py:81",
                   confidence="moderate")
    row.update(kw)
    return row


def _a_kind_for(rung):
    """The first alert kind the module maps to `rung`. Derived from the
    shipped map rather than from a constant that would exist only for
    this suite: a fixture hard-coding a kind would go stale the day the
    ladder gains one."""
    for kind in bv.ALERT_KINDS:
        if bv.RUNG_FOR_KIND[kind] == rung:
            return kind
    raise AssertionError("no alert kind maps to %s" % rung)


class VisualCase(unittest.TestCase):
    """A throwaway project root, a store, a project and a clock.

    Every subclass inherits the same guard: tools/bm_visual.py must exist.
    Until it does, every test in this file fails with that one sentence,
    which is the failing-first evidence section 13 asks every new class to
    reproduce."""

    def setUp(self):
        self.assertIsNotNone(
            bv, "tools/bm_visual.py does not exist yet, so nothing in this "
                "file can run. That is the failing-first state "
                "DESIGN-visual-surface section 17 step 1 asks every new "
                "class to reproduce.")
        self.tmp = tempfile.mkdtemp(prefix="bm-visual-")
        self.root = os.path.join(self.tmp, "project")
        os.makedirs(self.root)
        self._real_now = bs.now_iso
        self.clock = _Clock()
        bs.now_iso = self.clock.iso
        self.store = bs.Store(self.root)
        self.actor = _actor()

    def tearDown(self):
        bs.now_iso = self._real_now
        try:
            self.store.close()
        except Exception:  # sbe: allow-silent best-effort teardown close, must not hide a real assertion failure
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- seeding ---------------------------------------------------------

    def seed(self, pid="p1", goal="a booking page my customers can use"):
        stamp = self.clock.iso()
        self.store.upsert_project(
            {"project_id": pid, "name": "widget", "goal": goal,
             "created_at": stamp, "updated_at": stamp}, self.actor)
        return pid

    def sign(self, pid="p1", token_ceiling=40000, minutes_ceiling=90):
        return self.store.sign_contract(
            pid, "ship the booking page", "the booking test passes",
            ["."], [], ["file-create", "file-edit", "build", "test-run",
                        "read-only-inspect"],
            token_ceiling, minutes_ceiling, "Khalil Maaouni", "sess-visual",
            self.actor, supersede=False)

    def open_run(self, pid="p1"):
        record = self.store.claim(
            name="controller-%s" % pid, lifetime="persistent",
            files=["widget.py"], objective="carry the outcome",
            owner="coordinator", session_id="sess-visual")
        run = self.store.open_run(
            pid, "ctrl1", 1, "ship the booking page",
            "the booking test passes", record.lifecycle_uuid,
            "sess-visual", self.actor)
        return run

    def plan_units(self, run, units, pid="p1"):
        """Walk the run's own legal edges to PLANNING, then plan `units`,
        each a (unit_id, objective, lane) triple."""
        for state in ("ORIENTING", "PLANNING"):
            self.store.set_run_state(run["run_id"], state, self.actor,
                                     "fixture", "sess-visual")
        self.store.upsert_units(
            run["run_id"],
            [{"unit_id": uid, "objective": objective, "dependencies": [],
              "read_scope": [], "write_scope": ["%s.py" % uid],
              "role": "builder", "risk_class": "file-create",
              "lane": lane, "done_check": "true",
              "done_check_expect_exit": 0, "verifier": ""}
             for uid, objective, lane in units],
            self.actor, project_id=pid)
        return self.store.list_units(run["run_id"], raw=True)

    def dispatch(self, unit_id, pid="p1"):
        fence = self.store.claim(
            name="unit-%s" % unit_id, lifetime="ephemeral",
            files=["%s.py" % unit_id], objective="do the unit",
            owner="worker", session_id="sess-visual")
        self.store.claim_unit(unit_id, fence.lifecycle_uuid, self.actor,
                              project_id=pid)
        return self.store.record_dispatch(
            unit_id, 1, 1, fence.lifecycle_uuid, "sess-visual", self.actor,
            project_id=pid)

    def facts(self, pid="p1"):
        return bv.visual_facts(self.store, pid)


# ---------------------------------------------------------------------------
# Section 13.1, class 1: the measured colour tokens
# ---------------------------------------------------------------------------

class TestContrastTokens(VisualCase):
    """LENS-C section 5.6's measured table, re-run in the suite. The light
    settled rule on its own tint clears its floor by 0.07 and has no
    margin, so this class is the thing that stops a future tweak from
    failing WCAG quietly."""

    def test_every_measured_pair_clears_its_floor_with_no_rounding(self):
        self.assertTrue(bv.TOKEN_FLOORS, "the measured pair table is empty")
        failures = []
        for theme, fg_key, bg_key, floor in bv.TOKEN_FLOORS:
            table = (bv.TOKENS_LIGHT if theme == "light" else bv.TOKENS_DARK)
            self.assertIn(fg_key, table, "%s theme has no %s" % (theme,
                                                                 fg_key))
            self.assertIn(bg_key, table, "%s theme has no %s" % (theme,
                                                                 bg_key))
            got = bv.contrast_ratio(table[fg_key], table[bg_key])
            # WCAG says a computed value must not be rounded before the
            # comparison, so this compares the raw float.
            if got < floor:
                failures.append((theme, fg_key, bg_key, got, floor))
        self.assertEqual(failures, [],
                         "colour pair(s) below their WCAG floor: %s"
                         % (failures,))
        self.assertEqual(set(bv.TOKENS_LIGHT), set(bv.TOKENS_DARK),
                         "a token defined in one theme and not the other "
                         "cannot be used by a page that must render in "
                         "both")
        for name, table in (("light", bv.TOKENS_LIGHT),
                            ("dark", bv.TOKENS_DARK)):
            for key, value in table.items():
                self.assertRegex(
                    value, r"^#[0-9A-Fa-f]{6}$",
                    "%s token %s is not a six digit hex colour" % (name,
                                                                   key))

    def test_the_formula_matches_two_known_reference_values(self):
        """The two sanity values LENS-C section 5.6 reports, so a broken
        formula cannot pass the table above silently."""
        self.assertEqual(round(bv.contrast_ratio("#000000", "#FFFFFF"), 2),
                         21.0)
        self.assertEqual(round(bv.contrast_ratio("#767676", "#FFFFFF"), 2),
                         4.54)
        self.assertEqual(round(bv.relative_luminance("#FFFFFF"), 4), 1.0)
        self.assertEqual(round(bv.relative_luminance("#000000"), 4), 0.0)

    def test_the_light_settled_rule_is_the_tight_pair_and_is_pinned(self):
        """LENS-C section 5.6 states this pair clears 3:1 by 0.07 and has
        no margin. Pinned to two decimals so any tweak to either colour
        fails HERE, with this sentence, rather than in a browser."""
        got = bv.contrast_ratio(bv.TOKENS_LIGHT["settled-rule"],
                                bv.TOKENS_LIGHT["settled-tint"])
        self.assertEqual(round(got, 2), 3.07)
        self.assertGreaterEqual(got, 3.0)


# ---------------------------------------------------------------------------
# Section 13.1, class 2: colour is the third carrier, never the first
# ---------------------------------------------------------------------------

class TestColourIsNeverAlone(VisualCase):
    """L-S4. Every status is recoverable from a word first, a non colour
    mark second, and colour third."""

    def _seeded_facts(self):
        self.seed()
        self.sign()
        run = self.open_run()
        units = self.plan_units(run, [("u1", "wire the form", "default"),
                                      ("u2", "send the receipt", "second")])
        self.assertEqual(len(units), 2)
        return self.facts()

    def test_every_status_bearing_node_carries_a_word_from_the_lexicon(self):
        facts = self._seeded_facts()
        drawn = 0
        for builder in (bv.diagram_pipeline, bv.diagram_gates,
                        bv.diagram_lanes, bv.diagram_fork, bv.counts_rows):
            diagram = builder(facts)
            if diagram is None:
                continue
            drawn += 1
            for node in diagram["nodes"]:
                self.assertIn(node["status"], bv.STATUS_LEXICON,
                              "node %r carries a status word outside the "
                              "closed lexicon" % (node["label"],))
                self.assertIn(node["status"], node["label"],
                              "the status must be IN the label, not in a "
                              "separate key: %r" % (node["label"],))
        self.assertGreaterEqual(drawn, 3,
                                "the fixture must draw something for this "
                                "assertion to mean anything")

    def test_to_svg_emits_no_colour_attribute(self):
        facts = self._seeded_facts()
        for builder in (bv.diagram_pipeline, bv.diagram_gates,
                        bv.counts_rows):
            diagram = builder(facts)
            if diagram is None:
                continue
            svg = bv.to_svg(diagram)
            for banned in ("fill=", "stroke=", "style=", "color=",
                           "stop-color", "#"):
                self.assertNotIn(
                    banned, svg,
                    "%r appears in a %s drawing; all colour lives in "
                    "THEME_CSS so the stylesheet can be deleted in a test"
                    % (banned, diagram["shape"]))

    def test_the_four_rungs_are_distinguishable_with_the_stylesheet_gone(
            self):
        marks, words = set(), set()
        for rung in bv.RUNGS:
            alert = bv.alert(rung=rung, kind=_a_kind_for(rung),
                             subject="the widget", row_id="r1",
                             message="something happened", action="say go")
            text = bv.render_alert(alert, "text")
            self.assertIn(rung, text)
            words.add(rung)
            marks.add(bv.RUNG_MARKS[rung])
        self.assertEqual(len(marks), 4,
                         "the four non colour marks must all differ: %s"
                         % (sorted(marks),))
        self.assertEqual(len(words), 4)
        self.assertEqual(bv.RUNG_TOKEN["SETTLED"], "",
                         "SETTLED is quiet and uncoloured: do not paint "
                         "the done things green")

    def test_a_rendered_body_survives_the_stylesheet_being_removed(self):
        """THEME_CSS removed, every status still recoverable from words."""
        facts = self._seeded_facts()
        body = "\n".join(
            [bv.to_svg(d) for d in
             (bv.diagram_pipeline(facts), bv.diagram_gates(facts),
              bv.counts_rows(facts)) if d is not None]
            + [bv.render_alert(
                bv.alert(rung=r, kind=_a_kind_for(r),
                         subject="the widget", row_id="r1",
                         message="something happened", action="say go"),
                "html") for r in bv.RUNGS])
        self.assertNotIn(bv.THEME_CSS, body,
                         "the stylesheet must be a separate block, never "
                         "inlined into an element")
        stripped = body.replace(bv.THEME_CSS, "")
        for rung in bv.RUNGS:
            self.assertIn(rung, stripped)
        self.assertIn("NOW", stripped, "the current stage must be a WORD")
        # And every class the markup uses is one THEME_CSS defines. Found
        # by rendering the page and comparing the two sets: the status
        # "not run" was emitting class="bm-not run", which a browser reads
        # as two classes, one of them called "run". A class with no rule
        # is a status that loses its colour silently, which is the one
        # direction the colour rules above cannot catch.
        used = set()
        for value in re.findall(r'class="([^"]+)"', stripped):
            used.update(value.split())
        defined = set(re.findall(r"\.(bm-[A-Za-z0-9-]+)", bv.THEME_CSS))
        self.assertEqual(
            sorted(c for c in used if c not in defined), [],
            "class(es) used by the markup with no rule in THEME_CSS")


# ---------------------------------------------------------------------------
# Section 13.1, class 3: the size caps, enforced by code
# ---------------------------------------------------------------------------

class TestDiagramCaps(VisualCase):
    """LENS-C section 5.3. When the data exceeds a cap the generator
    AGGREGATES and says so in the caption; it never shrinks the font, and
    it never quietly draws a twelfth node."""

    def _nodes(self, count, status="waiting"):
        return [bv.node("n%d" % i, "step %d, %s" % (i, status), status)
                for i in range(count)]

    def _edges(self, count):
        return [bv.edge("n%d" % i, "n%d" % (i + 1)) for i in range(count)]

    def test_a_shape_outside_the_seven_raises_at_construction(self):
        """The count is pinned deliberately so that a new shape costs
        somebody an argument. It has been paid twice: timeline (the
        progress surface loop) and gantt (Phase 5, founder decision
        2026-08-08). Gantt was admitted rather than folded into timeline
        because the two answer different questions: timeline draws how
        far a piece has moved through its lifecycle, grouped by owner;
        gantt draws how long it took, grouped by phase. An eighth
        requires deleting one."""
        with self.assertRaises(ValueError) as ctx:
            bv.Diagram("pie", self._nodes(2), [], "t", "d", "c")
        for shape in bv.SHAPES:
            self.assertIn(shape, str(ctx.exception))
        self.assertEqual(len(bv.SHAPES), 7,
                         "seven shapes and nothing else; an eighth "
                         "requires deleting one")

    def test_the_refusal_states_the_real_count_not_a_stale_one(self):
        """Regression pin. This sentence read 'Five shapes' while SHAPES
        already held six, because adding timeline widened the tuple and
        left the prose behind. The count is now read from SHAPES, so the
        two cannot disagree again."""
        with self.assertRaises(ValueError) as ctx:
            bv.Diagram("pie", self._nodes(2), [], "t", "d", "c")
        self.assertIn("%d shapes and nothing else" % len(bv.SHAPES),
                      str(ctx.exception))

    def test_the_timeline_lane_cap_raises(self):
        cap = bv.CAPS["timeline"]
        nodes = [bv.node("n%d" % i, "step %d, working" % i, "working",
                         lane="owner %d" % i)
                 for i in range(cap["lanes"] + 1)]
        with self.assertRaises(ValueError) as ctx:
            bv.Diagram("timeline", nodes, [], "t", "d", "c")
        self.assertIn("lane", str(ctx.exception))

    def test_the_timeline_node_cap_raises(self):
        cap = bv.CAPS["timeline"]
        with self.assertRaises(ValueError):
            bv.Diagram("timeline", self._nodes(cap["nodes"] + 1), [],
                       "t", "d", "c")

    def test_the_pipeline_node_and_edge_caps_raise(self):
        cap = bv.CAPS["pipeline"]
        with self.assertRaises(ValueError):
            bv.Diagram("pipeline", self._nodes(cap["nodes"] + 1),
                       self._edges(cap["edges"]), "t", "d", "c")
        with self.assertRaises(ValueError):
            bv.Diagram("pipeline", self._nodes(cap["nodes"]),
                       self._edges(cap["edges"] + 1), "t", "d", "c")

    def test_the_gate_ladder_node_cap_raises(self):
        cap = bv.CAPS["gates"]
        with self.assertRaises(ValueError) as ctx:
            bv.Diagram("gates", self._nodes(cap["nodes"] + 1), [], "t", "d",
                       "c")
        self.assertIn(str(cap["nodes"]), str(ctx.exception))

    def test_the_lane_map_lane_cap_raises(self):
        cap = bv.CAPS["lanes"]
        nodes = [bv.node("n%d" % i, "step %d, working" % i, "working",
                         lane="owner %d" % i)
                 for i in range(cap["lanes"] + 1)]
        with self.assertRaises(ValueError) as ctx:
            bv.Diagram("lanes", nodes, [], "t", "d", "c")
        self.assertIn("lane", str(ctx.exception))

    def test_the_fork_outcome_cap_raises(self):
        cap = bv.CAPS["fork"]
        nodes = [bv.node("q", "the question, open", "open")] + [
            bv.node("o%d" % i, "outcome %d, chosen" % i, "chosen")
            for i in range(cap["outcomes"] + 1)]
        with self.assertRaises(ValueError) as ctx:
            bv.Diagram("fork", nodes,
                       [bv.edge("q", "o%d" % i)
                        for i in range(cap["outcomes"] + 1)],
                       "t", "d", "c")
        self.assertIn("outcome", str(ctx.exception))

    def test_more_gates_than_the_cap_aggregate_and_the_caption_says_so(self):
        self.seed()
        self.sign()
        run = self.open_run()
        units = [("u%d" % i, "step %d" % i, "default") for i in range(14)]
        self.plan_units(run, units)
        diagram = bv.diagram_gates(self.facts())
        self.assertIsNotNone(diagram)
        self.assertLessEqual(len(diagram["nodes"]), bv.CAPS["gates"]["nodes"])
        self.assertIn("of 14", diagram["caption"],
                      "an aggregated ladder must say how many of how many "
                      "it is showing: %r" % (diagram["caption"],))


# ---------------------------------------------------------------------------
# Section 13.1, class 4: escaping
# ---------------------------------------------------------------------------

class TestDiagramEscaping(VisualCase):
    """A record called "-h" exists in this project's own store, created by
    a probe that took a help flag as a name (tools/bm_docs.py:283 to 292
    records it). A label is data, never syntax."""

    HOSTILE = '<script>alert("x") & \'go\' [end] {}'

    def _one(self, label):
        return bv.Diagram("pipeline",
                          [bv.node("n1", "%s, NOW" % bv._flat(label), "NOW")],
                          [], "a title", "a description", "a caption")

    def test_a_label_with_syntax_characters_survives_both_emitters(self):
        diagram = self._one(self.HOSTILE)
        text = bv.to_text(diagram)
        svg = bv.to_svg(diagram)
        self.assertNotIn("<script", svg)
        self.assertNotIn("<script", text)
        self.assertIn("NOW", text)
        self.assertIn("NOW", svg)

    def test_a_record_named_dash_h_renders_as_a_label_not_as_syntax(self):
        self.assertEqual(bv._flat("-h"), "h")
        self.assertEqual(bv._flat(""), "unnamed")
        self.assertEqual(bv._flat("   "), "unnamed")
        diagram = self._one("-h")
        self.assertIn("h, NOW", bv.to_text(diagram))

    def test_the_svg_parses_as_xml_after_escaping(self):
        svg = bv.to_svg(self._one(self.HOSTILE))
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("figure") or root.tag == "figure",
                        "to_svg returns the whole figure: %r" % (root.tag,))

    def test_an_ampersand_is_escaped_exactly_once(self):
        diagram = self._one("cash & carry")
        svg = bv.to_svg(diagram)
        self.assertIn("&amp;", svg)
        self.assertNotIn("&amp;amp;", svg)
        # And the text emitter does not escape at all: a terminal is not
        # XML, and "&amp;" printed at a founder is a leak of the page's
        # own syntax into his chat.
        self.assertIn("cash & carry", bv.to_text(diagram))


# ---------------------------------------------------------------------------
# Section 13.1, class 5: the accessibility pair
# ---------------------------------------------------------------------------

class TestSvgAccessibilityPair(VisualCase):
    """W3C complex images: a picture carries a short text alternative and a
    long one, and the caption states the same content in prose a sighted
    non expert can read without decoding the picture."""

    def _every_diagram(self):
        self.seed()
        self.sign()
        run = self.open_run()
        self.plan_units(run, [("u1", "wire the form", "default"),
                              ("u2", "send the receipt", "second")])
        self.store.record_insight("p1", _key_decision(), self.actor)
        facts = self.facts()
        out = []
        for builder in (bv.diagram_pipeline, bv.diagram_gates,
                        bv.diagram_lanes, bv.diagram_fork, bv.counts_rows):
            diagram = builder(facts)
            if diagram is not None:
                out.append(diagram)
        self.assertGreaterEqual(len(out), 4)
        return out

    def test_every_drawing_has_role_img_with_a_title_and_a_desc(self):
        for diagram in self._every_diagram():
            root = ET.fromstring(bv.to_svg(diagram))
            svg = root.find("svg")
            self.assertIsNotNone(svg, "%s has no svg element"
                                 % diagram["shape"])
            self.assertEqual(svg.get("role"), "img")
            title = svg.find("title")
            desc = svg.find("desc")
            self.assertIsNotNone(title, "%s has no title" % diagram["shape"])
            self.assertIsNotNone(desc, "%s has no desc" % diagram["shape"])
            self.assertTrue((title.text or "").strip())
            self.assertTrue((desc.text or "").strip())

    def test_the_title_and_desc_are_wired_by_aria(self):
        for diagram in self._every_diagram():
            root = ET.fromstring(bv.to_svg(diagram))
            svg = root.find("svg")
            title, desc = svg.find("title"), svg.find("desc")
            self.assertEqual(svg.get("aria-labelledby"), title.get("id"))
            self.assertEqual(svg.get("aria-describedby"), desc.get("id"))
            self.assertTrue(title.get("id"))
            self.assertNotEqual(title.get("id"), desc.get("id"))

    def test_the_caption_is_non_empty_and_differs_from_the_title(self):
        for diagram in self._every_diagram():
            root = ET.fromstring(bv.to_svg(diagram))
            caption = root.find("figcaption")
            self.assertIsNotNone(caption,
                                 "%s has no figcaption" % diagram["shape"])
            text = "".join(caption.itertext()).strip()
            self.assertTrue(text)
            self.assertNotEqual(text, diagram["title"],
                                "a caption that repeats the title adds "
                                "nothing a reader could not already see")


# ---------------------------------------------------------------------------
# Section 13.1, class 6: the alert ladder
# ---------------------------------------------------------------------------

class TestAlertsNowIsDerivedAndCapped(VisualCase):
    """L-S6 and L-S7. There is no alerts table for this ladder: there is
    one pure function over rows, so an alert cannot go stale and nobody has
    to write dismissal logic."""

    def _needs_you_x2(self):
        self.seed()
        self.sign()
        self.store.record_insight("p1", _key_decision(), self.actor)
        self.store.record_insight(
            "p1", _key_decision(subject="the refund policy",
                                claim="refund inside 14 days",
                                decision_class="PREFERENCE"), self.actor)
        self.store.queue_human_step(
            "p1", "payment", "default", "authorise the card on file", "",
            [], "sess-visual", self.actor)

    def test_never_more_than_one_needs_you_is_shown(self):
        self._needs_you_x2()
        alerts = bv.alerts_now(self.store, "p1")
        top = [a for a in alerts if a["rung"] == "NEEDS YOU"]
        self.assertEqual(len(top), 1,
                         "a second NEEDS YOU queues behind the first and is "
                         "counted, never shown beside it")
        self.assertGreaterEqual(top[0]["more"], 1,
                                "the queued ones must be COUNTED, not "
                                "dropped in silence")

    def test_at_most_two_rungs_reach_one_chat_message(self):
        self._needs_you_x2()
        self.store.record_insight("p1", _insight(kind="RISK",
                                                 evidence_class="REASONED",
                                                 evidence=""), self.actor)
        chat = bv.alerts_for_chat(bv.alerts_now(self.store, "p1"))
        self.assertLessEqual(len({a["rung"] for a in chat}), 2,
                             "several alerts at once create a bad "
                             "experience: two rungs is the cap")
        self.assertTrue(chat)

    def test_a_cleared_condition_removes_its_alert_with_no_dismissal_call(
            self):
        self.seed()
        self.sign()
        out = self.store.record_insight("p1", _key_decision(), self.actor)
        before = bv.alerts_now(self.store, "p1")
        self.assertTrue([a for a in before if a["kind"] == "key-decision"])
        self.store.record_insight(
            "p1", _insight(kind="HANDBACK", supersedes=out["insight_id"],
                           control_offered=1, control_taken=1), self.actor)
        after = bv.alerts_now(self.store, "p1")
        self.assertEqual([a for a in after if a["kind"] == "key-decision"],
                         [],
                         "the condition cleared, so the alert is gone; "
                         "nothing was dismissed")
        self.assertTrue([a for a in after if a["rung"] == "SETTLED"],
                        "a decision that closed becomes SETTLED, quietly")

    def test_the_dedupe_key_collapses_a_repeat_and_counts_it(self):
        self.seed()
        self.sign()
        one = dict(rung="AT RISK", kind="open-risk", subject="the retry test",
                   row_id="i1", message="it failed 3 of 9 runs",
                   action="repeat it 20 times")
        deduped = bv._dedupe([bv.alert(**one), bv.alert(**one),
                              bv.alert(**dict(one, row_id="i2"))])
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["repeats"], 1,
                         "a repeat of the same (rung, kind, subject, row) "
                         "is counted, not re-thrown")
        self.assertEqual(deduped[1]["repeats"], 0)

    def test_an_at_risk_never_promotes_by_age(self):
        self.seed()
        self.sign()
        run = self.open_run()
        self.plan_units(run, [("u1", "wire the form", "default")])
        self.dispatch("u1")
        self.clock.advance(bv.STALE_DISPATCH_SECONDS * 10)
        alerts = bv.alerts_now(self.store, "p1")
        stale = [a for a in alerts if a["kind"] == "stale-dispatch"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["rung"], "AT RISK",
                         "time based escalation is the classic manufacture "
                         "of noise: age never promotes a rung")
        # And the timeout this reads is the engine's own number, not a
        # second copy that can drift.
        engine = re.search(r"DEFAULT_DISPATCH_TIMEOUT_SECONDS\s*=\s*(\d+)",
                           _read(CONTROLLER_FILE))
        self.assertIsNotNone(engine)
        self.assertEqual(bv.STALE_DISPATCH_SECONDS, int(engine.group(1)))

    def test_the_order_is_stable_across_two_calls_on_the_same_rows(self):
        self._needs_you_x2()
        self.store.record_insight("p1", _insight(kind="RISK"), self.actor)
        first = bv.alerts_now(self.store, "p1")
        second = bv.alerts_now(self.store, "p1")
        self.assertEqual(first, second)
        rungs = [a["rung"] for a in first]
        self.assertEqual(rungs, sorted(rungs, key=bv.RUNGS.index),
                         "the ladder orders the list: top rung first")

    def test_a_condition_with_no_query_behind_it_cannot_become_an_alert(
            self):
        with self.assertRaises(ValueError) as ctx:
            bv.alert(rung="NEEDS YOU", kind="seems-a-bit-weird",
                     subject="s", row_id="r", message="m", action="a")
        self.assertIn("seems-a-bit-weird", str(ctx.exception))
        with self.assertRaises(ValueError):
            bv.alert(rung="URGENT", kind="key-decision", subject="s",
                     row_id="r", message="m", action="a")
        self.assertEqual(set(bv.RUNG_FOR_KIND), set(bv.ALERT_KINDS),
                         "every kind's rung is a lookup, never a feeling")

    def test_settled_is_silent_and_carries_no_colour(self):
        delivery, role, cap = bv.RUNG_DELIVERY["SETTLED"]
        self.assertEqual(delivery, "silent")
        self.assertEqual(role, "")
        self.assertIsNone(cap)
        self.assertEqual(bv.RUNG_TOKEN["SETTLED"], "")
        self.assertEqual(bv.RUNG_DELIVERY["NEEDS YOU"],
                         ("interrupt", "alert", 1))
        self.assertEqual(bv.RUNG_DELIVERY["AT RISK"][1], "status")
        row = bv.alert(rung="SETTLED", kind="resolved-alert", subject="s",
                       row_id="r", message="m", action="")
        self.assertEqual(set(row), set(bv.ALERT_FIELDS),
                         "an alert carries exactly the documented fields, "
                         "because a page renderer reads them by name")
        html = bv.render_alert(row, "html")
        self.assertNotIn("role=", html,
                         "SETTLED is not announced: no ARIA role at all")


# ---------------------------------------------------------------------------
# Section 13.1, class 7: the insight box
# ---------------------------------------------------------------------------

class TestInsightBoxHasSixSlots(VisualCase):
    """Six slots or it is not a box. The missing slot is always CHANGES IF
    and that is the slot that makes the claim falsifiable."""

    def _row(self, **kw):
        self.seed()
        out = self.store.record_insight("p1", _insight(**kw), self.actor)
        return self.store.get_insight(out["insight_id"], raw=True)

    def test_every_slot_is_non_empty_in_both_media(self):
        row = self._row()
        slots = bv.insight_box_slots(row)
        self.assertEqual([label for label, _v in slots],
                         list(bv.INSIGHT_SLOTS))
        self.assertEqual(len(slots), 6)
        for label, value in slots:
            self.assertTrue(str(value).strip(), "slot %s is empty" % label)
        for medium in ("text", "html"):
            out = bv.render_insight_box(row, medium)
            for label, _value in slots:
                self.assertIn(label, out)

    def test_a_five_slot_render_fails_rather_than_shortening_the_box(self):
        self.seed()
        row = dict(_insight(), insight_id="i1", flip_condition="")
        with self.assertRaises(ValueError) as ctx:
            bv.render_insight_box(row, "text")
        self.assertIn("CHANGES IF", str(ctx.exception))
        for empty in ("claim", "evidence", "confidence"):
            with self.assertRaises(ValueError):
                bv.render_insight_box(dict(_insight(), insight_id="i1",
                                           **{empty: ""}), "text")

    def test_slot_six_is_byte_equal_to_the_handback_promise(self):
        row = self._row()
        slots = dict(bv.insight_box_slots(row))
        self.assertEqual(slots["YOUR MOVE"], bl.HANDBACK_OPTION_TEXT)
        self.assertIn(bl.HANDBACK_OPTION_TEXT,
                      bv.render_insight_box(row, "text"))
        self.assertEqual(bv.INSIGHT_SLOTS[-1], "YOUR MOVE",
                         "always present, always last")

    def test_a_reasoned_row_renders_the_prefix_and_the_words_not_tested(
            self):
        row = self._row(evidence_class="REASONED", evidence="",
                        claim="the retry path is probably decorative")
        for medium in ("text", "html"):
            out = bv.render_insight_box(row, medium)
            self.assertIn(bl.evidence_label("REASONED"), out)
            self.assertIn("not tested", out,
                          "two independent markings, because this is the "
                          "founder's own non negotiable")
            self.assertIn("REASONED", out)

    def test_a_reasoned_row_carries_no_rung_bar(self):
        row = self._row(evidence_class="REASONED", evidence="")
        text = bv.render_insight_box(row, "text")
        for mark in bv.RUNG_MARKS.values():
            self.assertNotIn(mark, text,
                             "a reasoned claim gets no rung bar in any "
                             "surface")
        self.assertNotIn(bv.RUNG_BAR_CLASS,
                         bv.render_insight_box(row, "html"))
        executed = self._row(subject="the build")
        self.assertIn(bv.RUNG_BAR_CLASS,
                      bv.render_insight_box(executed, "html"))


# ---------------------------------------------------------------------------
# Section 13.1, class 8: the surface router
# ---------------------------------------------------------------------------

class TestSurfaceRouter(VisualCase):
    """Section 3.3: one function, first match, so two runs on the same rows
    always agree, and an item that matches no branch has no job."""

    def test_branch_one_an_item_he_must_act_on_reaches_all_three(self):
        for kind in bv.MUST_ACT_KINDS:
            got = bv.surface_for({"kind": kind})
            self.assertEqual(got, ("S4", "S3", "S1"),
                             "%s must never live only in a page he has not "
                             "opened" % kind)

    def test_branch_two_an_answer_to_a_question_goes_to_the_chat(self):
        for kind in bv.ASKED_KINDS:
            self.assertEqual(bv.surface_for({"kind": kind}), ("S3",))

    def test_branch_three_state_worth_returning_to_goes_to_the_page(self):
        for kind in bv.STATE_KINDS:
            self.assertEqual(bv.surface_for({"kind": kind}), ("S1", "S2"))

    def test_branch_four_a_picture_goes_to_the_page_only(self):
        for kind in bv.SHAPES:
            self.assertEqual(bv.surface_for({"kind": kind}), ("S1",))

    def test_branch_five_anything_left_over_is_not_shown(self):
        with self.assertRaises(ValueError) as ctx:
            bv.surface_for({"kind": "a nice thought"})
        self.assertIn("a nice thought", str(ctx.exception))
        with self.assertRaises(ValueError):
            bv.surface_for({})

    def test_the_branches_are_disjoint_so_first_match_is_deterministic(self):
        seen = []
        for group in (bv.MUST_ACT_KINDS, bv.ASKED_KINDS, bv.STATE_KINDS,
                      bv.SHAPES):
            seen.extend(group)
        self.assertEqual(len(seen), len(set(seen)),
                         "an item kind in two branches makes the router's "
                         "answer depend on the order somebody wrote it")
        self.assertEqual(bv.diagram_for("where are we"), "pipeline")
        self.assertEqual(bv.diagram_for("what is blocking"), "gates")
        self.assertEqual(bv.diagram_for("who owns this"), "lanes")
        self.assertEqual(bv.diagram_for("what am I being asked"), "fork")
        self.assertEqual(bv.diagram_for("how much against what limit"),
                         "counts")
        self.assertEqual(bv.diagram_for("anything else at all"), "")
        for kinds in (bv.MUST_ACT_KINDS, bv.ASKED_KINDS, bv.STATE_KINDS,
                      bv.SHAPES):
            for kind in kinds:
                for surface in bv.surface_for({"kind": kind}):
                    self.assertIn(surface, bv.SURFACES,
                                  "the router named a surface that does "
                                  "not exist")


# ---------------------------------------------------------------------------
# Section 13.1, class 9: every refusal is rewritten
# ---------------------------------------------------------------------------

class TestEveryRefusalIsRewritten(VisualCase):
    """L-S9. A refusal never reaches the founder unrewritten, and the list
    of codes is read from the SHIPPED sources of BOTH modules that emit
    them, tools/bm_store.py and tools/bm_controller.py, so it cannot go
    stale for either one."""

    def test_every_reason_code_either_module_can_emit_has_an_entry(self):
        store_codes = _store_reason_codes()
        self.assertGreater(len(store_codes), 100,
                           "the scanner found almost nothing, so it is "
                           "broken rather than the map being complete")
        controller_codes = _controller_reason_codes()
        self.assertGreaterEqual(len(controller_codes), 7,
                                "the unattended preflight has at least seven "
                                "reason codes, so a scanner finding %s of "
                                "them is broken rather than the preflight "
                                "having shrunk"
                                % (len(controller_codes),))
        codes = store_codes | controller_codes
        missing = sorted(codes - set(bv.REFUSAL_HELP))
        self.assertEqual(missing, [],
                         "reason code(s) with no founder facing rewrite: %s"
                         % (missing,))
        extra = sorted(set(bv.REFUSAL_HELP) - codes)
        self.assertEqual(extra, [],
                         "REFUSAL_HELP names code(s) neither the store nor "
                         "the controller can emit, so they are dead "
                         "entries: %s" % (extra,))
        for code, entry in sorted(bv.REFUSAL_HELP.items()):
            self.assertEqual(len(entry), 3,
                             "%s must be (context, hint, next_action)"
                             % code)
            for part in entry:
                self.assertTrue(part.strip(), "%s has an empty part" % code)
                # KEPT for the controller's seven as well, deliberately.
                # Their next_action lines do name settings the founder
                # types, but as whole commands (export BM_FENCE_MODE=
                # enforced) rather than as flags, so none contains "--" and
                # none needs an exemption. An entry that did would be a
                # rewrite drifting back towards the system's own terms,
                # which is the thing this assertion exists to catch, so
                # weakening it for one source would give the seven a lower
                # bar than the rest of the map for no reason.
                self.assertNotIn("--", part,
                                 "%s reads like a command line, not like a "
                                 "sentence" % code)

    def test_the_unattended_rewrites_are_the_controllers_own_words(self):
        """RF-4 (GAP-19): the controller entries in REFUSAL_HELP are no
        longer a hand-typed COPY checked for equality against the
        original; both tools/bm_controller.py's UNATTENDED_REFUSAL_HELP
        and tools/bm_visual.py's REFUSAL_HELP entries for these codes are
        sourced from the single catalog in tools/bm_messages.py, so this
        asserts IDENTITY, not equality. A wording change made in one place
        cannot drift from the other because there is only one object: two
        independently retyped literals could satisfy an == check today and
        still diverge tomorrow, which is the exact incident RF-4 records
        (the wording drifted twice in one night). `is` cannot be satisfied
        that way.

        WHY THE CATALOG AND NOT A DIRECT IMPORT OF bm_controller.py.
        tools/bm_visual.py may not import tools/bm_controller.py: the
        controller is one of the two shipping modules allowed to import
        subprocess, and tools/bm_view.py loads bm_visual to render a page,
        so sharing by import would pull subprocess into the render path.
        tools/bm_messages.py is constants-only (no I/O, no subprocess, no
        writes), so either side can load it at no such cost.

        The controller is loaded HERE rather than beside bv at the top, and
        only for its text constants: every store and every exception class
        in this file still comes from bv.bs, which is the rule this file's
        own docstring states.

        NOT an independent `_load("bm_messages")` here: the generic _load()
        both this file and tools/bm_controller.py use always re-executes a
        module fresh (deliberately, for bm_store; see the class-identity
        trap tools/bm_project.py documents), so a THIRD independent load of
        bm_messages.py would produce a THIRD object, never `is`-equal to
        either surface's own copy regardless of whether the production
        wiring is right. tools/bm_controller.py and tools/bm_visual.py
        instead cache their own bm_messages load in sys.modules under one
        key and reuse whichever loaded first (bv, loaded at this test
        file's own module level, loads first), so bc's own
        UNATTENDED_REFUSAL_HELP and bv's own REFUSAL_HELP entries are
        already backed by the identical object without this test loading
        anything a third time."""
        bc = _load("bm_controller")
        for code in sorted(_controller_reason_codes()):
            self.assertIs(bv.REFUSAL_HELP[code], bc.UNATTENDED_REFUSAL_HELP[code],
                          "%s comes from a different object in the two "
                          "surfaces, so a future edit to one could still "
                          "drift from the other" % (code,))

    def test_the_failure_block_has_five_parts_and_exactly_one_expander(self):
        block = bv.failure_block("path-escape",
                                 "write your project page",
                                 "OwnershipRefused: path-escape ...")
        for part in bv.FAILURE_BLOCK_PARTS:
            self.assertIn(part, block)
        self.assertEqual(block.count(bv.EXPANDER_LABEL), 1,
                         "one expander, and it is the only place a raw log "
                         "may ever appear")
        html = bv.failure_block("path-escape", "write your project page",
                                "raw", medium="html")
        self.assertEqual(html.count("<details"), 1)
        self.assertEqual(html.count("</details>"), 1)
        with self.assertRaises(KeyError):
            bv.failure_block("no-such-code-anywhere", "x", "y")


# ---------------------------------------------------------------------------
# Section 13.1, class 10: one vocabulary, two renderings
# ---------------------------------------------------------------------------

class TestTextAndHtmlCarryTheSameFacts(VisualCase):
    """L-S5. A concept that works in only one of the two media does not
    enter the vocabulary."""

    def _facts(self):
        self.seed()
        self.sign()
        run = self.open_run()
        self.plan_units(run, [("u1", "wire the form", "default"),
                              ("u2", "send the receipt", "second")])
        self.store.record_insight("p1", _key_decision(), self.actor)
        # A step only a human can take, so the lane map has TWO owners and
        # is drawn at all: it is drawn only when ownership is the
        # important question, which is the rule the builder enforces.
        self.store.queue_human_step(
            "p1", "payment", "second", "authorise the card on file", "",
            [], "sess-visual", self.actor)
        self.store.record_spend("p1", 18000, 26, "fixture", "sess-visual",
                                self.actor)
        return self.facts()

    def test_each_shape_carries_the_same_status_words_in_both_emitters(self):
        facts = self._facts()
        checked = 0
        for builder in (bv.diagram_pipeline, bv.diagram_gates,
                        bv.diagram_lanes, bv.diagram_fork, bv.counts_rows):
            diagram = builder(facts)
            if diagram is None:
                continue
            checked += 1
            text, svg = bv.to_text(diagram), bv.to_svg(diagram)
            in_text = {w for w in bv.STATUS_LEXICON if w in text}
            in_svg = {w for w in bv.STATUS_LEXICON if w in svg}
            self.assertEqual(in_text, in_svg,
                             "%s says different things in the terminal and "
                             "in the page" % diagram["shape"])
        self.assertEqual(checked, 5, "all five shapes must be exercised")

    def test_the_six_slot_values_are_identical_across_media(self):
        self.seed()
        out = self.store.record_insight("p1", _insight(), self.actor)
        row = self.store.get_insight(out["insight_id"], raw=True)
        text = bv.render_insight_box(row, "text")
        html = bv.render_insight_box(row, "html")
        for _label, value in bv.insight_box_slots(row):
            self.assertIn(value, text)
            self.assertIn(bv.xml_escape(value), html)

    def test_a_count_rows_numbers_are_identical_in_both_media(self):
        facts = self._facts()
        counts = bv.counts_rows(facts)
        text, svg = bv.to_text(counts), bv.to_svg(counts)
        numbers_seen = 0
        for node in counts["nodes"]:
            self.assertIn(node["detail"], text)
            self.assertIn(bv.xml_escape(node["detail"]), svg)
            numbers_seen += 1
        self.assertGreaterEqual(numbers_seen, 4)
        self.assertIn('role="progressbar"', svg,
                      "the bar is decoration and the number is the content, "
                      "so the bar must announce the real sentence")
        self.assertIn("aria-valuetext=", svg)


# ---------------------------------------------------------------------------
# Section 13.1, class 11: the terminal is ASCII
# ---------------------------------------------------------------------------

class TestNoNonAsciiInTerminalOutput(VisualCase):
    """The repository runs a Windows CI job, and non ASCII in terminal
    output is a class of failure this project has already paid for.

    Every hostile character below is written as an ESCAPE, never as
    the character itself. Two reasons, both real: this project's copy
    rule forbids an em or an en dash anywhere in any file, a test
    fixture included, and a literal accented byte in a source file is
    the thing that makes a suite behave differently depending on how
    the checkout was encoded."""

    EM = u"\u2014"                          # em dash
    EN = u"\u2013"                          # en dash
    ACUTE = u"caf\u00e9"                    # an accented word
    TICK = u"\u2705"                        # an emoji
    CURLY = u"l\u2019approche de paiement"  # a curly apostrophe

    def test_every_text_medium_path_emits_ascii_only(self):
        self.seed(goal="une page de reservation pour mes clients %s vite"
                       % self.EM)
        self.sign()
        run = self.open_run()
        self.plan_units(run, [("u1", "%s %s wire the form"
                               % (self.ACUTE, self.EN),
                               "default"),
                              ("u2", "send the receipt %s"
                               % self.TICK, "second")])
        self.store.record_insight(
            "p1", _key_decision(subject=self.CURLY,
                                claim="use hosted checkout %s for now"
                                      % self.EM),
            self.actor)
        facts = self.facts()
        emitted = []
        for builder in (bv.diagram_pipeline, bv.diagram_gates,
                        bv.diagram_lanes, bv.diagram_fork, bv.counts_rows):
            diagram = builder(facts)
            if diagram is not None:
                emitted.append(bv.to_text(diagram))
        for alert in bv.alerts_now(self.store, "p1"):
            emitted.append(bv.render_alert(alert, "text"))
        row = self.store.list_insights("p1", raw=True)[0]
        emitted.append(bv.render_insight_box(row, "text"))
        emitted.append(bv.failure_block("not-found", "open your project",
                                        "raw %s output"
                                        % self.EM))
        self.assertGreaterEqual(len(emitted), 7)
        for text in emitted:
            try:
                text.encode("ascii")
            except UnicodeEncodeError as e:
                self.fail("non ASCII reached a terminal path: %s" % e)
            self.assertNotIn(self.EM, text)


# ---------------------------------------------------------------------------
# Section 13.1, class 12: progress_facts and the timeline shape
# (DESIGN-progress-surface.md items 1 and 2)
# ---------------------------------------------------------------------------

class TestProgressFactsIsPureAndStoreFree(unittest.TestCase):
    """No VisualCase here on purpose: progress_facts takes plain dicts, no
    store handle at all, which this class proves by never opening one."""

    def test_zero_tasks_is_a_real_answer_not_an_error(self):
        pf = bv.progress_facts({"tasks": [], "dependencies": [],
                                "evidence": {}})
        self.assertEqual({"lanes": []}, pf)

    def test_no_rows_at_all_behaves_the_same_as_empty_rows(self):
        self.assertEqual({"lanes": []}, bv.progress_facts({}))
        self.assertEqual({"lanes": []}, bv.progress_facts(None))

    def test_one_task_lands_in_one_lane_with_no_blocker(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "wire the form",
                      "status": "ready", "assigned_human": "Khalil"}],
            "dependencies": [], "evidence": {}})
        self.assertEqual(1, len(pf["lanes"]))
        lane = pf["lanes"][0]
        self.assertEqual("you", lane["lane"])
        self.assertEqual(1, len(lane["items"]))
        item = lane["items"][0]
        self.assertEqual("t1", item["task_id"])
        self.assertEqual("wire the form", item["label"])
        self.assertEqual("ready", item["state"])
        self.assertEqual("", item["evidence_ref"],
                         "no evidence on record is the literal absence, "
                         "never invented text, at the facts layer")
        self.assertEqual([], item["blocked_by"])
        self.assertEqual("", item["action"])

    def test_an_unassigned_task_lands_in_its_own_lane(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "wire the form",
                      "status": "planned"}],
            "dependencies": [], "evidence": {}})
        self.assertEqual("unassigned", pf["lanes"][0]["lane"])

    def test_a_blocked_task_carries_its_blocking_ids_and_its_own_action(self):
        pf = bv.progress_facts({
            "tasks": [
                {"task_id": "t1", "title": "wire the form",
                 "status": "active", "assigned_human": "Khalil"},
                {"task_id": "t2", "title": "send the receipt",
                 "status": "blocked", "assigned_runtime": "claude-code",
                 "blockers": ["waiting on the payment provider reply"]},
            ],
            "dependencies": [{"task_id": "t2", "depends_on_task_id": "t1"}],
            "evidence": {}})
        by_id = {i["task_id"]: i
                 for lane in pf["lanes"] for i in lane["items"]}
        self.assertEqual(["t1"], by_id["t2"]["blocked_by"])
        self.assertEqual("waiting on the payment provider reply",
                         by_id["t2"]["action"])
        self.assertEqual([], by_id["t1"]["blocked_by"])

    def test_blocking_ids_are_sorted_and_deduplicated(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t3", "title": "ship it",
                      "status": "blocked"}],
            "dependencies": [
                {"task_id": "t3", "depends_on_task_id": "t2"},
                {"task_id": "t3", "depends_on_task_id": "t1"},
                {"task_id": "t3", "depends_on_task_id": "t1"}],
            "evidence": {}})
        item = pf["lanes"][0]["items"][0]
        self.assertEqual(["t1", "t2"], item["blocked_by"])

    def test_evidence_reference_is_the_newest_one_on_record(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "wire the form",
                      "status": "verified"}],
            "dependencies": [],
            "evidence": {"t1": [
                {"ref": "tools/test_bm_view.py", "created_at": "2026-08-06"},
                {"ref": "tools/test_bm_visual.py", "created_at": "2026-08-07"},
            ]}})
        self.assertEqual("tools/test_bm_visual.py",
                         pf["lanes"][0]["items"][0]["evidence_ref"])

    def test_a_very_long_label_is_flattened_and_capped_not_dropped(self):
        long_title = "wire " + "a very long form title " * 20
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": long_title,
                      "status": "active"}],
            "dependencies": [], "evidence": {}})
        label = pf["lanes"][0]["items"][0]["label"]
        self.assertTrue(label)
        self.assertLessEqual(len(label), 48,
                             "a label reaching a drawn node must be capped, "
                             "never truncate mid drawing")

    def test_a_non_ascii_label_survives_and_still_draws_in_both_media(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1",
                      "title": u"café receipts ✅",
                      "status": "active"}],
            "dependencies": [], "evidence": {}})
        label = pf["lanes"][0]["items"][0]["label"]
        self.assertTrue(label, "a non ASCII title must not collapse to "
                               "nothing")
        diagram = bv.diagram_timeline(pf)
        self.assertIsNotNone(diagram)
        svg = bv.to_svg(diagram)
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("figure"),
                        "a non ASCII label must still produce valid XML")
        text = bv.to_text(diagram)
        try:
            text.encode("ascii")
        except UnicodeEncodeError as e:
            self.fail("non ASCII reached the terminal rendering: %s" % e)


class TestTheTimelineShape(VisualCase):
    """D6. Lanes against ordered items, a progress fraction per bar,
    still no colour attribute of its own."""

    def test_diagram_for_reaches_timeline_and_nothing_else_does(self):
        self.assertEqual("timeline", bv.diagram_for("how is this "
                                                     "progressing"))
        self.assertEqual("timeline", bv.diagram_for("progress by lane"))

    def test_zero_tasks_draws_nothing_a_real_answer_not_a_blank_picture(
            self):
        self.assertIsNone(bv.diagram_timeline(bv.progress_facts({})))
        self.assertIsNone(bv.diagram_timeline({"lanes": []}))

    def test_one_task_draws_one_bar_with_a_word_from_the_lexicon(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "wire the form",
                      "status": "active", "assigned_human": "Khalil"}],
            "dependencies": [], "evidence": {}})
        diagram = bv.diagram_timeline(pf)
        self.assertIsNotNone(diagram)
        self.assertEqual(1, len(diagram["nodes"]))
        node = diagram["nodes"][0]
        self.assertIn(node["status"], bv.STATUS_LEXICON)
        self.assertIn(node["status"], node["label"])
        self.assertEqual("you", node["lane"])

    def test_a_blocked_task_draws_as_blocked(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "send the receipt",
                      "status": "blocked"}],
            "dependencies": [], "evidence": {}})
        diagram = bv.diagram_timeline(pf)
        self.assertEqual("BLOCKED", diagram["nodes"][0]["status"])

    def test_the_fraction_is_the_closed_lifecycle_order_never_invented(
            self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "ship it",
                      "status": "closed"}],
            "dependencies": [], "evidence": {}})
        node = bv.diagram_timeline(pf)["nodes"][0]
        self.assertEqual(bv.TASK_STATES.index("closed") + 1, node["value"])
        self.assertEqual(len(bv.TASK_STATES), node["limit"])

    def test_to_svg_emits_no_colour_attribute(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "wire the form",
                      "status": "active", "assigned_human": "Khalil"},
                     {"task_id": "t2", "title": "send the receipt",
                      "status": "blocked", "assigned_runtime": "claude"}],
            "dependencies": [{"task_id": "t2",
                             "depends_on_task_id": "t1"}],
            "evidence": {}})
        svg = bv.to_svg(bv.diagram_timeline(pf))
        for banned in ("fill=", "stroke=", "style=", "color=",
                       "stop-color", "#"):
            self.assertNotIn(banned, svg,
                             "%r appears in a timeline drawing; all "
                             "colour lives in THEME_CSS" % (banned,))
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("figure"))

    def test_text_and_svg_carry_the_same_status_words(self):
        pf = bv.progress_facts({
            "tasks": [{"task_id": "t1", "title": "wire the form",
                      "status": "accepted", "assigned_human": "Khalil"},
                     {"task_id": "t2", "title": "send the receipt",
                      "status": "blocked", "assigned_runtime": "claude"}],
            "dependencies": [], "evidence": {}})
        diagram = bv.diagram_timeline(pf)
        text, svg = bv.to_text(diagram), bv.to_svg(diagram)
        in_text = {w for w in bv.STATUS_LEXICON if w in text}
        in_svg = {w for w in bv.STATUS_LEXICON if w in svg}
        self.assertEqual(in_text, in_svg)
        self.assertIn("BLOCKED", in_text)

    def test_the_lane_cap_aggregates_never_raises_from_real_rows(self):
        self.assertLessEqual(bv.CAPS["timeline"]["lanes"], 4)
        tasks = [{"task_id": "t%d" % i, "title": "step %d" % i,
                  "status": "ready", "assigned_human": "Khalil"}
                 for i in range(bv.CAPS["timeline"]["nodes"] + 5)]
        pf = bv.progress_facts({"tasks": tasks, "dependencies": [],
                                "evidence": {}})
        diagram = bv.diagram_timeline(pf)
        self.assertLessEqual(len(diagram["nodes"]),
                             bv.CAPS["timeline"]["nodes"])

    def test_every_task_state_has_a_drawn_status_the_guard_proves_it(self):
        """Calibration: the same shape as _check_stage_map's own proof,
        so the guard is shown to fire rather than merely exist."""
        with self.assertRaises(RuntimeError):
            bv._check_task_status_map(
                list(bv.TASK_STATES) + ["some-future-state"],
                bv._TIMELINE_STATUS_WORD, bv.STATUS_LEXICON)
        with self.assertRaises(RuntimeError):
            bv._check_task_status_map(
                bv.TASK_STATES,
                dict(bv._TIMELINE_STATUS_WORD, planned="a-made-up-word"),
                bv.STATUS_LEXICON)
        self.assertTrue(bv._check_task_status_map(
            bv.TASK_STATES, bv._TIMELINE_STATUS_WORD, bv.STATUS_LEXICON))

    def test_surface_for_timeline_is_a_shape_only_no_longer_a_state_kind(
            self):
        """The real collision this loop found: STATE_KINDS used to carry
        an entry literally spelled "timeline" too, so the router could not
        tell the new shape from the old state kind. Renamed to
        catchup-story; this pins the fix."""
        self.assertEqual(("S1",), bv.surface_for({"kind": "timeline"}))
        self.assertNotIn("timeline", bv.STATE_KINDS)
        self.assertIn("catchup-story", bv.STATE_KINDS)


def _gtask(tid, phase="", status="planned", started=None, completed=None,
           title=None):
    return {"task_id": tid, "title": title or ("Task " + tid),
            "status": status, "phase": phase,
            "started_at": started, "completed_at": completed,
            "blockers": []}


class TestGanttFacts(unittest.TestCase):
    """Phase 5, the progress view. gantt_facts is pure over already
    gathered rows, exactly like progress_facts beside it: no store
    handle, no SQL, no clock."""

    def test_no_tasks_is_no_data_not_an_empty_chart(self):
        self.assertIsNone(bv.diagram_gantt(bv.gantt_facts({"tasks": []})))

    def test_phases_come_out_in_first_appearance_order(self):
        facts = bv.gantt_facts({"tasks": [
            _gtask("a", phase="Phase C"),
            _gtask("b", phase="Phase 2"),
            _gtask("c", phase="Phase C")]})
        self.assertEqual([p["phase"] for p in facts["phases"]],
                         ["Phase C", "Phase 2"])
        self.assertEqual([i["task_id"] for i in facts["phases"][0]["items"]],
                         ["a", "c"])

    def test_a_task_with_no_phase_is_named_unphased_never_filed_elsewhere(
            self):
        """The record says nothing, so the page says nothing. Filing it
        under whichever phase is current would be a guess the founder
        never made (see _TASKS_V18_COLUMN)."""
        facts = bv.gantt_facts({"tasks": [_gtask("a")]})
        self.assertEqual(facts["phases"][0]["phase"], bv.UNPHASED)

    def test_a_box_ticks_only_with_a_finished_state_and_recorded_evidence(
            self):
        rows = {"tasks": [_gtask("done-with", status="accepted"),
                          _gtask("done-without", status="accepted"),
                          _gtask("evidence-unfinished", status="active")],
                "evidence": {"done-with": [{"ref": "test_all: ALL GREEN"}],
                             "evidence-unfinished": [{"ref": "partial"}]}}
        ticks = {i["task_id"]: i["ticked"]
                 for i in bv.gantt_facts(rows)["phases"][0]["items"]}
        self.assertTrue(ticks["done-with"])
        self.assertFalse(ticks["done-without"])
        self.assertFalse(ticks["evidence-unfinished"])

    def test_the_percentage_is_a_count_of_records_not_a_feeling(self):
        rows = {"tasks": [_gtask("a", status="accepted"),
                          _gtask("b", status="accepted"),
                          _gtask("c", status="active"),
                          _gtask("d", status="planned")],
                "evidence": {"a": [{"ref": "green"}]}}
        facts = bv.gantt_facts(rows)
        self.assertEqual((facts["ticked"], facts["total"]), (1, 4))

    def test_the_window_spans_the_earliest_start_to_the_latest_finish(self):
        rows = {"tasks": [
            _gtask("a", started="2026-08-01T00:00:00Z",
                   completed="2026-08-03T00:00:00Z"),
            _gtask("b", started="2026-08-02T00:00:00Z",
                   completed="2026-08-09T00:00:00Z")]}
        self.assertEqual(bv.gantt_facts(rows)["window"],
                         {"start": "2026-08-01", "end": "2026-08-09",
                          "span_days": 9})

    def test_no_dates_anywhere_means_no_window_and_the_bars_say_so(self):
        """Founder decision 2026-08-08: real dates where recorded,
        progress fill where not. With nothing dated there is no time
        axis to draw, and the items must say dated=False rather than
        borrow a window from somewhere."""
        facts = bv.gantt_facts({"tasks": [_gtask("a")]})
        self.assertIsNone(facts["window"])
        self.assertFalse(facts["phases"][0]["items"][0]["dated"])

    def test_an_undated_task_beside_dated_ones_still_says_it_is_undated(self):
        rows = {"tasks": [
            _gtask("dated", started="2026-08-01T00:00:00Z",
                   completed="2026-08-02T00:00:00Z"),
            _gtask("bare")]}
        items = {i["task_id"]: i
                 for i in bv.gantt_facts(rows)["phases"][0]["items"]}
        self.assertTrue(items["dated"]["dated"])
        self.assertFalse(items["bare"]["dated"])

    def test_a_started_but_unfinished_task_runs_to_the_window_end(self):
        """An open bar has a real start and no end. It draws to the edge
        of the window rather than to a completion date nobody recorded."""
        rows = {"tasks": [
            _gtask("open", started="2026-08-01T00:00:00Z"),
            _gtask("shut", started="2026-08-01T00:00:00Z",
                   completed="2026-08-05T00:00:00Z")]}
        items = {i["task_id"]: i
                 for i in bv.gantt_facts(rows)["phases"][0]["items"]}
        self.assertEqual(items["open"]["end"], "2026-08-05")
        self.assertTrue(items["open"]["open_ended"])
        self.assertFalse(items["shut"]["open_ended"])

    def test_an_unparseable_date_is_dropped_not_guessed(self):
        rows = {"tasks": [_gtask("bad", started="last Tuesday")]}
        facts = bv.gantt_facts(rows)
        self.assertIsNone(facts["window"])
        self.assertFalse(facts["phases"][0]["items"][0]["dated"])


class TestGanttDiagram(unittest.TestCase):

    def _facts(self):
        return bv.gantt_facts({"tasks": [
            _gtask("a", phase="Phase C", status="accepted",
                   started="2026-08-01T00:00:00Z",
                   completed="2026-08-03T00:00:00Z"),
            _gtask("b", phase="Phase 2", status="active",
                   started="2026-08-03T00:00:00Z")],
            "evidence": {"a": [{"ref": "test_all: ALL GREEN"}]}})

    def test_it_draws_one_node_per_task_grouped_into_its_phase(self):
        drawn = bv.diagram_gantt(self._facts())
        self.assertEqual(drawn["shape"], "gantt")
        self.assertEqual([n["lane"] for n in drawn["nodes"]],
                         ["Phase C", "Phase 2"])

    def test_the_same_rows_render_byte_identical_twice(self):
        """The view law: geometry from integers, never from a clock or a
        measured font, so a page regenerated a week later is the same
        bytes."""
        facts = self._facts()
        self.assertEqual(bv.to_svg(bv.diagram_gantt(facts)),
                         bv.to_svg(bv.diagram_gantt(facts)))

    def test_the_svg_carries_the_tick_state_of_every_box(self):
        svg = bv.to_svg(bv.diagram_gantt(self._facts()))
        self.assertIn("bm-tick-on", svg)
        self.assertIn("bm-tick-off", svg)

    def test_the_prose_caption_states_the_count_not_an_impression(self):
        drawn = bv.diagram_gantt(self._facts())
        self.assertIn("1 of 2", drawn["caption"])

    def test_the_petrol_accent_is_a_token_in_both_themes(self):
        """Founder decision 2026-08-08: petrol as the accent, the
        colourblind-safe status colours kept underneath it."""
        for table in (bv.TOKENS_LIGHT, bv.TOKENS_DARK):
            self.assertIn("accent", table)
            self.assertIn("paper", table)

    def test_the_accent_clears_the_contrast_floor_in_both_themes(self):
        self.assertGreaterEqual(
            bv.contrast_ratio(bv.TOKENS_LIGHT["accent"],
                              bv.TOKENS_LIGHT["paper"]), 4.5)
        self.assertGreaterEqual(
            bv.contrast_ratio(bv.TOKENS_DARK["accent"],
                              bv.TOKENS_DARK["paper"]), 4.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
