#!/usr/bin/env python3
"""The Delivery Receipt v1 contract, asserted against a receipt the engine
writes tonight.

Row S8 (the founder's switching strategy, section 7 zone 1 and section 24 P1
item 1): freeze the receipt contract so a reader knows, before opening a
receipt, which questions it answers and which it does not.

THE DOCUMENT IS THE SOURCE OF TRUTH, NOT THIS FILE. Both tables of
docs/plan/DELIVERY-RECEIPT-V1.md are parsed here at run time: the field table
gives the required paths and their types, and the absence table gives the
fields a NOT YET WRITTEN question would be answered by, which must be absent
today. Nothing is hard coded in this module, so a row deleted from the page
stops being asserted and a row added starts being asserted, without an edit
here. That is the point: the page cannot quietly promise a field the engine
does not write, and the engine cannot quietly grow one the page does not
mention.

THE RECEIPT IS GENERATED, NEVER FIXTURED. A fixture receipt would pin what
somebody once saw; this generates one by running scripts/brother_run.py end to
end on the README's toy repository through the stubbed model seam
(DOOR_MODEL_CMD and MODEL_WORKER_CMD), exactly as
test_brother_run.TheFirstRunLeavesAReceipt does, and reads the file the engine
left behind. No network, no real model.

RECEIPT_CONTRACT_DOC in the environment overrides which document is parsed.
That exists so the check can be driven backwards (add a fake required field to
a COPY of the page and watch this name it), never so a run can quietly point at
a friendlier page.
"""
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# The engine and the toy-run helpers, reused rather than rewritten: make_repo,
# write_stub, WRITER_MODEL, sh and BROTHER_RUN all already exist there and are
# what the E81 receipt test drives the engine with.
import test_brother_run as TBR  # noqa: E402

DOC_PATH = os.environ.get(
    "RECEIPT_CONTRACT_DOC",
    os.path.join(os.path.dirname(HERE), "docs", "plan",
                 "DELIVERY-RECEIPT-V1.md"))

#: The type words the document is allowed to use, and what each accepts. A
#: word outside this table is a documentation defect, not a silent pass:
#: _check_type refuses it by name.
TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "object or null": lambda v: v is None or isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "integer or null": lambda v: v is None or (isinstance(v, int)
                                               and not isinstance(v, bool)),
    "number or null": lambda v: v is None or (isinstance(v, (int, float))
                                              and not isinstance(v, bool)),
    "boolean": lambda v: isinstance(v, bool),
    "boolean or null": lambda v: v is None or isinstance(v, bool),
}

_CELL = re.compile(r"`([^`]+)`")


def _rows(text):
    """Every markdown table row in `text`, as a list of stripped cells. Rows
    of dashes (the header separator) and the header itself are dropped by the
    callers below on the shape of their own first cell, so this stays a plain
    split with no knowledge of which table it is reading."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line[1:-1].split("|")]
        if all(set(c) <= set("- :") for c in cells):
            continue
        out.append(cells)
    return out


def load_field_table(path=None):
    """[(json_path, type_word, required_word, question)] from the document's
    field table: every row whose first cell is a backquoted path and whose
    third cell is 'yes' or 'per element'. Raises OSError or ValueError rather
    than returning an empty list, because an empty required list is a check
    that passes by proving nothing."""
    # Resolved at CALL time, never bound as a default: a default argument
    # binds when the function object is created, which would freeze the
    # document path before any caller could point it elsewhere.
    path = path or DOC_PATH
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    fields = []
    for cells in _rows(text):
        if len(cells) < 4:
            continue
        name = _CELL.match(cells[0])
        if not name or cells[2] not in ("yes", "per element"):
            continue
        fields.append((name.group(1), cells[1], cells[2], cells[3]))
    if not fields:
        raise ValueError("%s names no required fields; an empty contract "
                         "asserts nothing" % path)
    return fields


def load_absent_table(path=None):
    """[(field_name, container_path, question)] from the document's absence
    table: every row whose FIRST TWO cells are both backquoted. The field
    table's own rows never match, because their second cell is a bare type
    word. An empty result is legal here and means every question has a field
    today."""
    path = path or DOC_PATH
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    absent = []
    for cells in _rows(text):
        if len(cells) < 3:
            continue
        field, container = _CELL.match(cells[0]), _CELL.match(cells[1])
        if not field or not container:
            continue
        absent.append((field.group(1), container.group(1), cells[2]))
    return absent


#: The exact phrase the page uses to claim a question has no field. It is
#: RESERVED for that claim: the page says so itself, and says it without
#: spelling the phrase, so this marker never matches a sentence merely
#: describing the mechanism. Contract 1.1 answers every question and the page
#: therefore carries none of these.
CLAIM_MARKER = "NOT YET WRITTEN"


def claimed_unanswered(path=None):
    """{question number, as the string the page writes} the page's own prose
    still claims has no field: for every blank-line paragraph carrying
    CLAIM_MARKER, the question numbers that paragraph names.

    Whitespace inside a paragraph is collapsed before the marker is looked
    for, because the page is hard wrapped and the phrase can straddle a line
    break. A plain `text.count(CLAIM_MARKER)` misses exactly that case, and
    a claim that hides from its own check is the failure this file exists to
    prevent."""
    path = path or DOC_PATH
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    claimed = set()
    for para in re.split(r"\n\s*\n", text):
        flat = " ".join(para.split())
        if CLAIM_MARKER not in flat:
            continue
        claimed.update(re.findall(r"[Qq]uestion (\d+)", flat))
    return claimed


def resolve(doc, path):
    """[(where, value), ...] for every place `path` lands in `doc`, or None
    when the path does not resolve at all. A '[]' segment fans out over an
    array, so 'evidence[].id' returns one pair per element and an empty
    'evidence' returns []. `where` is the concrete path, for a failure message
    that names the element rather than the pattern."""
    places = [("", doc)]
    if path == ".":
        return places
    for raw in path.split("."):
        fan = raw.endswith("[]")
        key = raw[:-2] if fan else raw
        nxt = []
        for where, node in places:
            if not isinstance(node, dict) or key not in node:
                return None
            value = node[key]
            here = "%s.%s" % (where, key) if where else key
            if not fan:
                nxt.append((here, value))
                continue
            if not isinstance(value, list):
                return None
            for i, item in enumerate(value):
                nxt.append(("%s[%d]" % (here, i), item))
        places = nxt
    return places


class TheDocumentParsesIntoAContract(unittest.TestCase):
    """Before anything is asserted against a receipt: the page itself has to
    yield a contract. A parser that silently reads zero rows would make every
    test below pass over nothing."""

    def test_the_field_table_yields_rows_of_known_types(self):
        fields = load_field_table()
        self.assertGreater(len(fields), 40, "the field table shrank")
        for path, type_word, required, question in fields:
            self.assertIn(type_word, TYPE_CHECKS,
                          "%s declares type %r, which is not a type word this "
                          "check knows" % (path, type_word))
            self.assertTrue(question.strip(),
                            "%s names no question it answers" % path)

    def test_the_prose_claim_and_the_absence_table_agree(self):
        """The page's prose and its absence table have to say the same thing
        in both directions: a claimed-unanswered question with no absence row
        is prose nobody enforces, and an absence row with no claim behind it
        forbids a field the page never explained.

        AT 1.0 THIS ASSERTED "AT LEAST ONE", which was true only while some
        question was unanswered. E115 answered the last two, so the honest
        invariant is agreement, not presence: both sides empty is the state a
        complete contract is supposed to reach."""
        claimed = claimed_unanswered()
        enforced = {q for _f, _c, q in load_absent_table()}
        self.assertEqual(
            bool(claimed), bool(enforced),
            "the page's prose and its absence table disagree: prose claims "
            "questions %s have no field, the absence table enforces %s"
            % (sorted(claimed) or "none", sorted(enforced) or "none"))

    def test_no_field_answers_a_question_the_page_calls_unanswered(self):
        """The durable half of the mechanism, added by E115 after the 1.0
        absence table failed to catch that row's own change.

        That table refused by FIELD NAME. It forbade `dependency_note`, and
        the field that answered question 6 is `dependency_check`, so it would
        have stayed green through E115 on its own; only question 10, whose
        fields kept their names, turned it red. A question number cannot be
        renamed, so this refuses on that instead, whatever the field is
        called."""
        claimed = claimed_unanswered()
        answered = {q for _p, _t, _r, q in load_field_table()}
        both = sorted(claimed & answered)
        self.assertEqual(
            [], both,
            "the field table says question(s) %s are answered and the page's "
            "prose still claims they have no field" % ", ".join(both))


class TheUnansweredClaimIsDrivenBothWays(unittest.TestCase):
    """The two checks above pass over an EMPTY claim set at contract 1.1,
    because 1.1 answers every question. A check that can only be observed
    passing over nothing proves nothing, so both are driven backwards here on
    a COPY of the page carrying a re-added claim for question 6, which 1.1
    answers with `evidence[].dependency_check`. RECEIPT_CONTRACT_DOC exists
    for exactly this and no run reads the copy as its contract."""

    CLAIM = ("**Question 6, did a dependency revert or counterfactual check "
             "run.** The engine spends it on prose. " + CLAIM_MARKER + ".")

    def _page_with_a_re_added_claim(self, tmp):
        with open(DOC_PATH, encoding="utf-8") as fh:
            text = fh.read()
        path = os.path.join(tmp, "page-with-claim.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n\n" + self.CLAIM + "\n")
        return path

    def test_the_marker_is_read_off_the_page_even_when_it_wraps(self):
        """The page is hard wrapped, so the claim phrase can straddle a line
        break. Written here as two lines on purpose: a detector that only
        matched the phrase on one line would read this page as claim free,
        which is the silent-pass this check refuses."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wrapped.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("**Question 6, a claim.** This one is NOT YET\n"
                         "WRITTEN as a field.\n")
            self.assertEqual({"6"}, claimed_unanswered(path))

    def test_a_re_added_claim_is_caught_against_the_field_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._page_with_a_re_added_claim(tmp)
            claimed = claimed_unanswered(path)
            self.assertIn("6", claimed, "the driver's own claim did not parse")
            answered = {q for _p, _t, _r, q in load_field_table(path)}
            self.assertIn("6", answered,
                          "the page stopped answering question 6, so this "
                          "driver no longer drives anything")
            self.assertEqual({"6"}, claimed & answered)

    def test_a_re_added_claim_with_no_absence_row_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._page_with_a_re_added_claim(tmp)
            self.assertTrue(claimed_unanswered(path))
            self.assertFalse([q for _f, _c, q in load_absent_table(path)])

    def test_the_live_page_leaves_both_sides_empty(self):
        """The positive control's mirror: on the real page today, both sides
        are empty and agree. Stated as its own case so the state the two
        checks above are asserting is visible, never inferred."""
        self.assertEqual(set(), claimed_unanswered())
        self.assertEqual([], load_absent_table())


class AGeneratedReceiptMatchesTheContract(unittest.TestCase):
    """One real run of the engine, its receipt read off disk, asserted field
    by field against the page. The run is the README's own toy delivery: two
    units, two changed files, one that its check proves and one that a
    dependency revert disproves, so the receipt carries a non empty
    scope.changed, a non empty evidence list and a non empty unproven list and
    every 'per element' row is exercised for real."""

    receipt = None
    setup_error = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.receipt = cls._generate()
        except (OSError, ValueError, AssertionError, IndexError) as exc:
            cls.setup_error = "%s: %s" % (type(exc).__name__, exc)

    @classmethod
    def _generate(cls):
        tmp = tempfile.mkdtemp(prefix="s8-receipt-contract-")
        repo = TBR.make_repo(tmp)
        for name, body in (
                ("mathlib.py", "def add(a, b):\n    return a + b\n"),
                ("test_mathlib.py",
                 "from mathlib import add\n\n\n"
                 "def test_add():\n    assert add(1, 2) == 3\n")):
            with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        TBR.sh(["git", "add", "-A"], cwd=repo)
        TBR.sh(["git", "commit", "-q", "-m", "toy"], cwd=repo)
        decomposer = TBR.write_stub(tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "G1", "objective": "guard add()",
                 "done_check": "grep -q 'stub model' mathlib.py",
                 "writes": ["mathlib.py"], "deps": []},
                {"id": "G2", "objective": "cover the guard",
                 "done_check": "grep -q 'stub model' test_mathlib.py",
                 "writes": ["test_mathlib.py"], "deps": ["G1"],
                 "depends_on": ["G1"]},
            ]))
        """)
        model = TBR.write_stub(tmp, "writer_model.py", TBR.WRITER_MODEL)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
        proc = TBR.sh([sys.executable, TBR.BROTHER_RUN,
                       "make add() refuse non-numeric input and cover it",
                       "--cwd", repo, "--runs-root", tmp], env=env)
        if proc.returncode != 0:
            raise AssertionError("brother_run exited %d:\n%s"
                                 % (proc.returncode,
                                    proc.stdout + proc.stderr))
        runs = os.path.join(tmp, "docs", "plan", "runs")
        names = sorted(os.listdir(runs))
        if len(names) != 1:
            raise AssertionError("expected one run directory, found %r"
                                 % (names,))
        path = os.path.join(runs, names[0], "receipt", "receipt.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def setUp(self):
        if self.setup_error:
            self.fail("no receipt was generated, so nothing here could be "
                      "checked: %s" % self.setup_error)

    def test_the_run_produced_the_three_non_empty_lists(self):
        """Guard on the guard: if this toy run ever stopped producing a
        changed file, a verified unit and an unproven one, every 'per element'
        row below would pass over an empty list and prove nothing."""
        self.assertTrue(self.receipt["scope"]["changed"])
        self.assertTrue(self.receipt["evidence"])
        self.assertTrue(self.receipt["unproven"])

    def test_every_required_field_is_present_with_its_documented_type(self):
        missing, wrong = [], []
        for path, type_word, required, _question in load_field_table():
            places = resolve(self.receipt, path)
            if places is None:
                missing.append("%s (%s)" % (path, required))
                continue
            accepts = TYPE_CHECKS[type_word]
            for where, value in places:
                if not accepts(value):
                    wrong.append("%s is %s, the page says %s"
                                 % (where, type(value).__name__, type_word))
        self.assertEqual([], missing,
                         "the page requires fields this receipt does not "
                         "carry: %s" % ", ".join(missing))
        self.assertEqual([], wrong,
                         "fields carry a type the page does not declare: %s"
                         % "; ".join(wrong))

    def test_every_not_yet_written_field_is_absent(self):
        """The day someone answers question 6 or question 10 with a real
        field, this fails and the page has to move with the code. That is the
        mechanism, not a side effect."""
        present = []
        for field, container, question in load_absent_table():
            places = resolve(self.receipt, container)
            if places is None:
                continue
            for where, node in places:
                if isinstance(node, dict) and field in node:
                    present.append("%s.%s answers question %s and the page "
                                   "still says NOT YET WRITTEN"
                                   % (where or ".", field, question))
        self.assertEqual([], present, "; ".join(present))


if __name__ == "__main__":
    unittest.main(verbosity=2)
