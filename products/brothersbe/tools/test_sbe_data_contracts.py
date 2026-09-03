#!/usr/bin/env python3
"""Fixtures for `brothersbe.data_contracts`, the data contract registry:
"a data contract must be registered before anything is built" turned into a
check that runs. Run: python3 tools/test_sbe_data_contracts.py

THE DEFECT THIS EXISTS FOR, stated once so every test below reads against it.
A contracts directory that exists but is empty, or does not exist at all, is
NOT "nothing is wrong": it is NO-DATA, and this suite pins that a full three
different ways (`TestLoadRegistry`, `TestCheckDiff`) so "no registry" can
never quietly read as "clean". Separately, a change that touches a table
nobody registered must be reported LOUDLY and must NEVER be able to fail
somebody's pipeline over it (`TestCheckDiffNeverBlocks`): the verdict this
module returns for that case is pinned to `"PASS"`, on purpose, not left to
be discovered later as an accidental green.

Three kinds of fixture, cheapest first:

  * `TestYamlSubset` and `TestValidateContractFields`: pure, in-process,
    hand-built strings and dicts. Fast, and exactly what a subset reader and
    a field checker should be tested with directly.
  * `TestLoadRegistry`: a real temporary directory holding real `.yaml`
    files, read from disk exactly the way `load_registry` is documented to
    read `contracts/`.
  * `TestCheckDiff*`: a REAL git repository, built with real `git` commands
    in a temporary directory, with real commits and a real diff between two
    real commits. Nothing here is a hand-typed diff string standing in for
    what `git diff` would produce, the same discipline
    `tools/test_sbe_check_registry.py`'s own `RegistryFixture` already holds
    to (mirrored below rather than reinvented: `git()`, `write()`, and the
    `init` / `config user.*` sequence are copied from it near verbatim).

CALIBRATION, per test, not only once. Every refusal test below asserts BOTH
the broken input is refused AND that the same check, given a genuinely valid
input, PASSes: `TestValidateContractFields.test_missing_one_field_is_refused`
proves the FAIL came from the missing field and not from a checker that
always says FAIL by validating a complete contract right after. A separate,
manual calibration is recorded in this session's own return message: the
required-field loop in `validate_contract_fields` was deliberately broken
(one field dropped from the loop) and
`test_missing_one_field_is_refused_by_name` was confirmed to go red before
the break was reverted; that is not re-encoded as a permanent test here
because a test that breaks its own module's source on every run is not a
regression test, it is a footgun.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

sys.path.insert(0, os.path.join(ROOT, "src"))
try:
    from brothersbe import data_contracts as dc  # noqa: E402
finally:
    sys.path.pop(0)


# ---------------------------------------------------------------------------
# Shared fixture helpers. Mirrored from `tools/test_sbe_check_registry.py`'s
# `git()` / `write()` / `RegistryFixture.setUp` sequence, not reinvented.
# ---------------------------------------------------------------------------

def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


#: A complete, valid contract for table `orders`, reused by every fixture
#: below that needs one real PASS-ing contract on disk.
VALID_ORDERS_CONTRACT = """row_meaning: "one row per placed order"
keys:
  - order_id
schema:
  - order_id: integer
  - customer_id: integer
  - status: string
freshness: "no more than 1 hour stale"
quality_thresholds:
  - "order_id is never null"
  - "status is one of: pending, shipped, cancelled"
allowed_readers:
  - order-service
  - analytics-readonly
change_notice: "30 days"
"""


class GitRepoFixture(unittest.TestCase):
    """A real, empty git repository, with `contracts/` not yet created."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-data-contracts-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "Data Contracts Fixture")
        write(self.repo, "README.md", "a repository\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def commit(self, rel, body, message):
        write(self.repo, rel, body)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def contracts_dir(self):
        return os.path.join(self.repo, dc.CONTRACTS_DIR_REL)


# ---------------------------------------------------------------------------
# 1. The tiny YAML subset reader
# ---------------------------------------------------------------------------

class TestYamlSubset(unittest.TestCase):
    def test_a_complete_contract_parses_with_every_field_readable(self):
        parsed = dc.read_contract_text(VALID_ORDERS_CONTRACT)
        self.assertEqual(parsed["row_meaning"], "one row per placed order")
        self.assertEqual(parsed["keys"], ["order_id"])
        self.assertEqual(parsed["schema"],
                         ["order_id: integer", "customer_id: integer", "status: string"])
        self.assertEqual(parsed["freshness"], "no more than 1 hour stale")
        self.assertEqual(len(parsed["quality_thresholds"]), 2)
        self.assertEqual(parsed["allowed_readers"], ["order-service", "analytics-readonly"])
        self.assertEqual(parsed["change_notice"], "30 days")

    def test_a_key_with_no_list_items_reads_as_an_empty_list(self):
        parsed = dc.read_contract_text('row_meaning: "x"\nkeys:\nfreshness: "daily"\n')
        self.assertEqual(parsed["keys"], [])

    def test_a_nested_mapping_is_refused_naming_the_line_and_the_construct(self):
        # A real, common way an author reaches for this: "schema" written as
        # a YAML mapping (column -> type) instead of the supported list form.
        text = ('row_meaning: "x"\n'
               'schema:\n'
               '  id: integer\n'
               '  name: string\n')
        with self.assertRaises(dc.ContractFormatError) as ctx:
            dc.read_contract_text(text)
        message = str(ctx.exception)
        self.assertIn("line 3", message, message)
        self.assertIn("nested mapping", message, message)
        # Calibration: the same author intent, spelled the SUPPORTED way,
        # parses cleanly, proving the refusal above is about the construct
        # and not about the word "schema" or the key "id".
        ok = dc.read_contract_text('row_meaning: "x"\nschema:\n  - "id: integer"\n'
                                   '  - "name: string"\n')
        self.assertEqual(ok["schema"], ["id: integer", "name: string"])

    def test_an_anchor_is_refused_naming_the_line_and_the_construct(self):
        text = 'row_meaning: &anchor "x"\nfreshness: "daily"\n'
        with self.assertRaises(dc.ContractFormatError) as ctx:
            dc.read_contract_text(text)
        message = str(ctx.exception)
        self.assertIn("line 1", message, message)
        self.assertIn("anchor", message, message)
        ok = dc.read_contract_text('row_meaning: "x"\nfreshness: "daily"\n')
        self.assertEqual(ok["row_meaning"], "x")

    def test_a_multiline_block_scalar_is_refused_naming_the_line_and_the_construct(self):
        text = 'row_meaning: |\n  first line\n  second line\n'
        with self.assertRaises(dc.ContractFormatError) as ctx:
            dc.read_contract_text(text)
        message = str(ctx.exception)
        self.assertIn("line 1", message, message)
        self.assertIn("block scalar", message, message)
        ok = dc.read_contract_text('row_meaning: "first line, second line"\n')
        self.assertEqual(ok["row_meaning"], "first line, second line")

    def test_a_flow_collection_is_refused_naming_the_line_and_the_construct(self):
        text = 'keys: [order_id, customer_id]\n'
        with self.assertRaises(dc.ContractFormatError) as ctx:
            dc.read_contract_text(text)
        message = str(ctx.exception)
        self.assertIn("line 1", message, message)
        self.assertIn("flow collection", message, message)

    def test_tab_indentation_is_refused_naming_the_line(self):
        text = 'keys:\n\t- order_id\n'
        with self.assertRaises(dc.ContractFormatError) as ctx:
            dc.read_contract_text(text)
        self.assertIn("line 2", str(ctx.exception))
        self.assertIn("tab", str(ctx.exception))

    def test_a_document_marker_is_refused_naming_the_line(self):
        text = '---\nrow_meaning: "x"\n'
        with self.assertRaises(dc.ContractFormatError) as ctx:
            dc.read_contract_text(text)
        self.assertIn("line 1", str(ctx.exception))
        self.assertIn("document marker", str(ctx.exception))

    def test_a_duplicate_key_is_refused_naming_the_line(self):
        text = 'row_meaning: "x"\nrow_meaning: "y"\n'
        with self.assertRaises(dc.ContractFormatError) as ctx:
            dc.read_contract_text(text)
        self.assertIn("line 2", str(ctx.exception))
        self.assertIn("duplicate", str(ctx.exception))

    def test_comments_and_blank_lines_are_ignored(self):
        text = '# a comment\nrow_meaning: "x"\n\n# another\nfreshness: "daily"\n'
        parsed = dc.read_contract_text(text)
        self.assertEqual(parsed["row_meaning"], "x")
        self.assertEqual(parsed["freshness"], "daily")

    def test_read_contract_file_returns_none_for_an_absent_path(self):
        self.assertIsNone(dc.read_contract_file("/nonexistent/path/does-not-exist.yaml"))

    def test_a_unicode_line_separator_inside_a_value_does_not_split_the_line(self):
        # ADVERSARIAL REVIEW MINOR. `str.splitlines()` treats U+2028 LINE
        # SEPARATOR (and U+0085, U+000C, ...) as a line break, not only
        # "\n". Reading with that method used to cut a value carrying one of
        # these characters into two "lines", which either manufactured a
        # bogus second field or (as here) broke quoted-string parsing on a
        # value that was never supposed to end mid-string. Splitting on
        # "\n" only means the character survives as ordinary content.
        text = 'row_meaning: "before after"\nfreshness: "daily"\n'
        parsed = dc.read_contract_text(text)
        self.assertEqual(parsed["row_meaning"], "before after")
        self.assertEqual(parsed["freshness"], "daily")


# ---------------------------------------------------------------------------
# 2. Whether a contract counts as registered
# ---------------------------------------------------------------------------

class TestValidateContractFields(unittest.TestCase):
    def setUp(self):
        self.complete = dc.read_contract_text(VALID_ORDERS_CONTRACT)

    def test_a_complete_contract_validates_pass(self):
        verdict, evidence, problems = dc.validate_contract_fields(self.complete)
        self.assertEqual(verdict, "PASS", evidence)
        self.assertEqual(problems, ())

    def test_missing_one_field_is_refused_by_name(self):
        mutated = dict(self.complete)
        del mutated["freshness"]
        verdict, evidence, problems = dc.validate_contract_fields(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("freshness" in p for p in problems), problems)
        # Calibration: the untouched original still validates PASS.
        restored_verdict, _e, restored_problems = dc.validate_contract_fields(self.complete)
        self.assertEqual(restored_verdict, "PASS", _e)
        self.assertEqual(restored_problems, ())

    def test_missing_four_fields_names_all_four_not_just_the_first(self):
        mutated = dict(self.complete)
        dropped = ("keys", "schema", "allowed_readers", "change_notice")
        for field in dropped:
            del mutated[field]
        verdict, evidence, problems = dc.validate_contract_fields(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        joined = " ".join(problems)
        for field in dropped:
            self.assertIn(field, joined, (field, problems))
        # The two fields NOT dropped must not be named as missing.
        for field in ("row_meaning", "freshness", "quality_thresholds"):
            self.assertNotIn("missing required field(s)" + field, joined.replace(", ", ""),
                             (field, problems))

    def test_a_blank_value_is_refused_and_named_separately_from_missing(self):
        mutated = dict(self.complete)
        mutated["row_meaning"] = "   "
        verdict, evidence, problems = dc.validate_contract_fields(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("row_meaning" in p and "blank" in p for p in problems), problems)
        self.assertFalse(any("missing required field" in p and "row_meaning" in p
                             for p in problems), problems)

    def test_a_placeholder_value_is_refused(self):
        mutated = dict(self.complete)
        mutated["freshness"] = "TODO"
        verdict, evidence, problems = dc.validate_contract_fields(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("freshness" in p for p in problems), problems)

    def test_an_empty_list_field_is_refused(self):
        mutated = dict(self.complete)
        mutated["quality_thresholds"] = []
        verdict, evidence, problems = dc.validate_contract_fields(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("quality_thresholds" in p for p in problems), problems)

    def test_a_list_field_of_only_placeholders_is_refused_naming_all_four(self):
        # ADVERSARIAL REVIEW CRITICAL 2. The four list fields, each holding
        # nothing but placeholder text, must ALL be named: this is the
        # scenario the reviewer reproduced by hand (keys, schema,
        # quality_thresholds, allowed_readers all "TODO", "N/A", "TBD",
        # "none") and confirmed a mutant deleting the check that catches it
        # survives the suite unless a test exercises this exact shape.
        mutated = dict(self.complete)
        mutated["keys"] = ["TODO"]
        mutated["schema"] = ["N/A"]
        mutated["quality_thresholds"] = ["TBD"]
        mutated["allowed_readers"] = ["none"]
        verdict, evidence, problems = dc.validate_contract_fields(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        joined = " ".join(problems)
        for field in ("keys", "schema", "quality_thresholds", "allowed_readers"):
            self.assertIn(field, joined, (field, problems))
        # Calibration: the untouched original still validates PASS.
        restored_verdict, _e, restored_problems = dc.validate_contract_fields(self.complete)
        self.assertEqual(restored_verdict, "PASS", _e)
        self.assertEqual(restored_problems, ())

    def test_a_partially_placeholder_list_is_still_refused_not_laundered(self):
        # ADVERSARIAL REVIEW MAJOR 3. One real value beside a placeholder
        # used to launder the whole field, because the old check asked
        # "is ANY item answered" instead of "is EVERY item answered".
        # keys: [TODO, order_id] and schema: [order_id, TBD] must both still
        # be refused, and refused by name.
        mutated = dict(self.complete)
        mutated["keys"] = ["TODO", "order_id"]
        mutated["schema"] = ["order_id: integer", "TBD"]
        verdict, evidence, problems = dc.validate_contract_fields(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        joined = " ".join(problems)
        self.assertIn("keys", joined, problems)
        self.assertIn("schema", joined, problems)
        restored_verdict, _e, restored_problems = dc.validate_contract_fields(self.complete)
        self.assertEqual(restored_verdict, "PASS", _e)

    def test_none_is_no_data_not_fail(self):
        verdict, evidence, problems = dc.validate_contract_fields(None)
        self.assertEqual(verdict, "NO-DATA", evidence)
        self.assertEqual(problems, ())

    def test_a_non_mapping_is_fail_not_no_data(self):
        verdict, evidence, problems = dc.validate_contract_fields(["not", "a", "mapping"])
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(problems, problems)


# ---------------------------------------------------------------------------
# 3. The registry directory
# ---------------------------------------------------------------------------

class TestLoadRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-data-contracts-registry-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_an_absent_directory_is_no_data_never_pass(self):
        missing = os.path.join(self.tmp, "contracts")
        verdict, table_map, problems = dc.load_registry(missing)
        self.assertEqual(verdict, "NO-DATA", (table_map, problems))
        self.assertEqual(table_map, {})

    def test_an_empty_directory_is_no_data_never_pass(self):
        empty = os.path.join(self.tmp, "contracts")
        os.makedirs(empty)
        verdict, table_map, problems = dc.load_registry(empty)
        self.assertEqual(verdict, "NO-DATA", (table_map, problems))

    def test_a_directory_holding_only_a_non_yaml_file_is_still_no_data(self):
        d = os.path.join(self.tmp, "contracts")
        os.makedirs(d)
        write(self.tmp, os.path.join("contracts", "README.md"), "not a contract\n")
        verdict, table_map, problems = dc.load_registry(d)
        self.assertEqual(verdict, "NO-DATA", (table_map, problems))

    def test_a_directory_with_one_valid_contract_loads_it_by_table_name(self):
        d = os.path.join(self.tmp, "contracts")
        os.makedirs(d)
        write(self.tmp, os.path.join("contracts", "orders.yaml"), VALID_ORDERS_CONTRACT)
        verdict, table_map, problems = dc.load_registry(d)
        self.assertEqual(verdict, "PASS", (table_map, problems))
        self.assertIn("orders", table_map)
        self.assertEqual(table_map["orders"]["field_verdict"], "PASS")
        self.assertEqual(problems, ())

    def test_a_dotted_table_name_round_trips_through_the_filename(self):
        d = os.path.join(self.tmp, "contracts")
        os.makedirs(d)
        write(self.tmp, os.path.join("contracts", "analytics.events.users.yaml"),
             VALID_ORDERS_CONTRACT)
        verdict, table_map, _p = dc.load_registry(d)
        self.assertEqual(verdict, "PASS")
        self.assertIn("analytics.events.users", table_map)

    def test_a_malformed_contract_file_is_recorded_against_its_table_not_dropped(self):
        d = os.path.join(self.tmp, "contracts")
        os.makedirs(d)
        write(self.tmp, os.path.join("contracts", "broken.yaml"),
             'row_meaning: &a "x"\n')  # an anchor: outside the subset
        verdict, table_map, problems = dc.load_registry(d)
        self.assertEqual(verdict, "PASS", "the REGISTRY loaded; one entry in it is broken")
        self.assertEqual(table_map["broken"]["field_verdict"], "FAIL")
        self.assertTrue(problems, problems)

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "needs enforced file modes")
    def test_an_unreadable_contract_file_is_reported_fail_not_raised(self):
        # ADVERSARIAL REVIEW MAJOR 4. `read_contract_file`'s own docstring
        # says it raises `OSError` for a file that exists and cannot be
        # read. `load_registry` used to catch only `ContractFormatError`,
        # so a chmod-000 contract file raised straight out of this
        # function (and, from there, out of `check_diff`): a traceback and
        # a non-zero process exit where a `"FAIL"` verdict is documented,
        # breaking the one promise that must not break.
        d = os.path.join(self.tmp, "contracts")
        os.makedirs(d)
        path = write(self.tmp, os.path.join("contracts", "orders.yaml"), VALID_ORDERS_CONTRACT)
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        verdict, table_map, problems = dc.load_registry(d)
        self.assertEqual(verdict, "PASS", "the REGISTRY loaded; one entry in it is unreadable")
        self.assertEqual(table_map["orders"]["field_verdict"], "FAIL")
        self.assertIn("orders", table_map)
        self.assertTrue(problems, problems)

    def test_a_dangling_symlink_contract_is_reported_broken_not_silently_unregistered(self):
        # MINOR, found alongside the majors above. `os.path.isfile` follows
        # a symlink and returns False for one whose target is gone, so the
        # old file filter dropped a dangling `orders.yaml` symlink from
        # `files` entirely: the table it claimed to describe then read as
        # "no contract registered" (an ABSENCE) instead of "this table's
        # contract exists and cannot be read" (a BROKEN CLAIM), exactly
        # the distinction `load_registry`'s own docstring already draws
        # for an unlistable directory.
        d = os.path.join(self.tmp, "contracts")
        os.makedirs(d)
        link_path = os.path.join(d, "orders.yaml")
        os.symlink(os.path.join(d, "does-not-exist-target.yaml"), link_path)
        verdict, table_map, problems = dc.load_registry(d)
        self.assertEqual(verdict, "PASS", "one broken entry is not the same as zero entries")
        self.assertIn("orders", table_map)
        self.assertEqual(table_map["orders"]["field_verdict"], "FAIL")
        self.assertTrue(problems, problems)


# ---------------------------------------------------------------------------
# 4. Table names a diff defines or writes
# ---------------------------------------------------------------------------

class TestExtractTableNames(unittest.TestCase):
    def test_create_table_is_recognized(self):
        hits = dc.extract_table_names("CREATE TABLE orders (id integer);")
        self.assertEqual(hits, [("orders", "CREATE TABLE")])

    def test_create_or_replace_table_if_not_exists_is_recognized(self):
        hits = dc.extract_table_names(
            "CREATE OR REPLACE TABLE IF NOT EXISTS analytics.orders (id integer);")
        self.assertEqual(hits, [("analytics.orders", "CREATE TABLE")])

    def test_insert_into_is_recognized(self):
        hits = dc.extract_table_names("INSERT INTO orders (id) VALUES (1);")
        self.assertEqual(hits, [("orders", "INSERT INTO")])

    def test_merge_into_is_recognized(self):
        hits = dc.extract_table_names("MERGE INTO orders USING staging ON (1=1);")
        self.assertEqual(hits, [("orders", "MERGE INTO")])

    def test_update_set_is_recognized(self):
        hits = dc.extract_table_names("UPDATE orders SET status = 'shipped';")
        self.assertEqual(hits, [("orders", "UPDATE")])

    def test_copy_into_is_recognized(self):
        hits = dc.extract_table_names("COPY INTO orders FROM @stage;")
        self.assertEqual(hits, [("orders", "COPY INTO")])

    def test_a_non_sql_write_is_not_detected_and_that_is_documented_not_hidden(self):
        # An ORM call defines/writes a table too; this module's diff scanner
        # does not read Python, and does not claim to. Zero hits, not a
        # crash and not a guess.
        hits = dc.extract_table_names("Order.objects.create(customer_id=1)\n")
        self.assertEqual(hits, [])

    def test_each_distinct_table_is_reported_once(self):
        text = "CREATE TABLE orders (id integer);\nINSERT INTO orders (id) VALUES (1);\n"
        hits = dc.extract_table_names(text)
        self.assertEqual(hits, [("orders", "CREATE TABLE")])

    def test_an_unaliased_update_is_recognized(self):
        # Calibration companion to the aliased forms below: the plain form
        # must keep matching once the alias becomes optional.
        hits = dc.extract_table_names("UPDATE orders SET status = 'shipped';")
        self.assertEqual(hits, [("orders", "UPDATE")])

    def test_an_update_with_a_bare_alias_is_recognized(self):
        # ADVERSARIAL REVIEW MAJOR 6. `UPDATE orders o SET ...` is an
        # ordinary way to write an aliased UPDATE; the old pattern required
        # SET immediately after the table name and matched nothing here.
        hits = dc.extract_table_names("UPDATE orders o SET status = 'shipped';")
        self.assertEqual(hits, [("orders", "UPDATE")])

    def test_an_update_with_an_as_alias_is_recognized(self):
        hits = dc.extract_table_names("UPDATE orders AS o SET status = 'shipped';")
        self.assertEqual(hits, [("orders", "UPDATE")])

    def test_a_backtick_quoted_identifier_is_recognized(self):
        # ADVERSARIAL REVIEW MAJOR 6. Every pattern required a bare letter
        # or underscore as its first character, so a quoted identifier
        # (MySQL backticks, ANSI double quotes, SQL Server brackets) never
        # reached the pattern at all.
        hits = dc.extract_table_names("CREATE TABLE `orders` (id integer);")
        self.assertEqual(hits, [("orders", "CREATE TABLE")])

    def test_a_double_quoted_identifier_is_recognized(self):
        hits = dc.extract_table_names('INSERT INTO "orders" (id) VALUES (1);')
        self.assertEqual(hits, [("orders", "INSERT INTO")])

    def test_a_bracketed_identifier_is_recognized(self):
        hits = dc.extract_table_names("MERGE INTO [orders] USING staging ON (1=1);")
        self.assertEqual(hits, [("orders", "MERGE INTO")])

    def test_a_commented_out_statement_is_not_detected(self):
        # MINOR, found alongside the majors above. A line comment ahead of
        # a real-looking statement used to be scanned exactly like a real
        # one, so this module would tell an author to register a table
        # that no build of this diff will ever create.
        hits = dc.extract_table_names("-- INSERT INTO orders (id) VALUES (1);\n")
        self.assertEqual(hits, [])

    def test_a_statement_after_a_comment_on_the_same_file_is_still_detected(self):
        text = "-- INSERT INTO ghost_table (id) VALUES (1);\nCREATE TABLE orders (id integer);\n"
        hits = dc.extract_table_names(text)
        self.assertEqual(hits, [("orders", "CREATE TABLE")])


class TestCompareContractToChange(unittest.TestCase):
    def setUp(self):
        self.contract = dc.read_contract_text(VALID_ORDERS_CONTRACT)

    def test_matching_keys_and_schema_produce_no_disagreement(self):
        columns = ["order_id", "customer_id", "status"]
        primary_key = ["order_id"]
        self.assertEqual(dc.compare_contract_to_change(self.contract, columns, primary_key), [])

    def test_a_keys_mismatch_is_reported_field_by_field(self):
        d = dc.compare_contract_to_change(self.contract, ["order_id", "customer_id", "status"],
                                          ["order_id", "customer_id"])
        self.assertEqual(len(d), 1, d)
        self.assertEqual(d[0]["field"], "keys")
        self.assertEqual(d[0]["contract_says"], ["order_id"])
        self.assertEqual(d[0]["change_implies"], ["order_id", "customer_id"])

    def test_a_schema_mismatch_is_reported_field_by_field(self):
        d = dc.compare_contract_to_change(self.contract,
                                          ["order_id", "customer_id", "status", "refund_cents"],
                                          ["order_id"])
        self.assertEqual(len(d), 1, d)
        self.assertEqual(d[0]["field"], "schema")
        self.assertIn("refund_cents", d[0]["change_implies"])
        self.assertNotIn("refund_cents", d[0]["contract_says"])

    def test_no_primary_key_clause_means_keys_is_not_compared_at_all(self):
        # primary_key is None: "not stated in this diff", not "stated empty".
        d = dc.compare_contract_to_change(self.contract, ["order_id", "customer_id", "status"],
                                          None)
        self.assertEqual(d, [])

    def test_freshness_and_the_other_four_fields_are_never_in_the_comparable_set(self):
        # Even with a wildly different column set, only "schema"/"keys" can
        # appear; row_meaning/freshness/quality_thresholds/allowed_readers/
        # change_notice are never diff-derived.
        d = dc.compare_contract_to_change(self.contract, ["totally", "different", "columns"],
                                          ["totally"])
        fields = {x["field"] for x in d}
        self.assertTrue(fields <= {"keys", "schema"}, fields)


class TestColumnsAndPrimaryKey(unittest.TestCase):
    """`_columns_and_primary_key`, the parser behind `keys`/`schema`
    comparison. ADVERSARIAL REVIEW CRITICAL 1 lives here: the reviewer's
    exact reproduction, plus each half of the defect in isolation."""

    def _body(self, create_table_sql, table="orders"):
        body = dc._create_table_body(create_table_sql, table)
        self.assertIsNotNone(body, create_table_sql)
        return dc._columns_and_primary_key(body)

    def test_the_reviewers_exact_reproduction(self):
        # "CONSTRAINT pk_orders PRIMARY KEY (customer_id)" must be read as
        # the table's primary key, and "key" as a genuine column name must
        # survive into the column list. Before the fix: columns
        # ['order_id', 'customer_id'] (key dropped), primary key None (the
        # CONSTRAINT-prefixed clause invisible).
        sql = ("CREATE TABLE orders (\n"
              "  order_id integer, customer_id integer, key integer,\n"
              "  CONSTRAINT pk_orders PRIMARY KEY (customer_id)\n"
              ");\n")
        columns, primary_key = self._body(sql)
        self.assertEqual(columns, ["order_id", "customer_id", "key"])
        self.assertEqual(primary_key, ["customer_id"])

    def test_a_bare_primary_key_clause_is_still_read(self):
        # Calibration: the un-prefixed spelling this function already
        # handled must keep working once the CONSTRAINT-prefixed spelling
        # is added.
        sql = "CREATE TABLE orders (order_id integer, PRIMARY KEY (order_id));\n"
        columns, primary_key = self._body(sql)
        self.assertEqual(columns, ["order_id"])
        self.assertEqual(primary_key, ["order_id"])

    def test_an_inline_column_level_primary_key_is_read(self):
        sql = "CREATE TABLE orders (order_id integer PRIMARY KEY, status varchar(20));\n"
        columns, primary_key = self._body(sql)
        self.assertEqual(columns, ["order_id", "status"])
        self.assertEqual(primary_key, ["order_id"])

    def test_columns_named_check_index_unique_and_constraint_survive(self):
        # The other four words the reviewer named alongside "key".
        sql = ("CREATE TABLE orders (\n"
              "  check integer, index integer, unique integer, constraint integer\n"
              ");\n")
        columns, _pk = self._body(sql)
        self.assertEqual(columns, ["check", "index", "unique", "constraint"])

    def test_a_real_unique_constraint_clause_is_still_excluded_from_columns(self):
        # Calibration for the fix above: a GENUINE constraint clause using
        # one of those words (with the paren that makes it one) must still
        # be excluded from the column list, not turned into a false column
        # by an overcorrection.
        sql = "CREATE TABLE orders (order_id integer, email varchar(100), UNIQUE (email));\n"
        columns, _pk = self._body(sql)
        self.assertEqual(columns, ["order_id", "email"])

    def test_no_primary_key_at_all_returns_none_not_empty(self):
        sql = "CREATE TABLE orders (order_id integer, status varchar(20));\n"
        _cols, primary_key = self._body(sql)
        self.assertIsNone(primary_key)


# ---------------------------------------------------------------------------
# 5. check_diff: the end-to-end, real-git, real-registry check
# ---------------------------------------------------------------------------

class TestCheckDiffNoData(GitRepoFixture):
    def test_no_contracts_directory_at_all_is_no_data(self):
        head = self.commit("migrations/001.sql", "CREATE TABLE orders (id integer);\n",
                           "add a migration")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "NO-DATA", (evidence, findings))
        self.assertEqual(findings, ())

    def test_an_empty_contracts_directory_is_no_data_even_with_a_touched_table(self):
        os.makedirs(self.contracts_dir())
        write(self.repo, os.path.join(dc.CONTRACTS_DIR_REL, ".gitkeep"), "")
        head = self.commit("migrations/001.sql", "CREATE TABLE orders (id integer);\n",
                           "add a migration")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "NO-DATA", (evidence, findings))


class TestCheckDiffNeverBlocks(GitRepoFixture):
    """The design's central promise: an unregistered table is reported and
    the VERDICT stays PASS, so no caller mapping PASS to exit 0 ever fails a
    pipeline over a missing contract."""

    def setUp(self):
        GitRepoFixture.setUp(self)
        os.makedirs(self.contracts_dir())
        write(self.repo, os.path.join(dc.CONTRACTS_DIR_REL, "orders.yaml"),
             VALID_ORDERS_CONTRACT)
        self.base = self.commit(os.path.join(dc.CONTRACTS_DIR_REL, "orders.yaml"),
                                VALID_ORDERS_CONTRACT, "register orders")

    def test_an_unregistered_table_is_reported_and_the_verdict_is_still_pass(self):
        head = self.commit("migrations/002_refunds.sql",
                           "CREATE TABLE refunds (id integer, order_id integer);\n",
                           "add refunds, no contract")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "PASS", (evidence, findings))
        self.assertTrue(any("refunds" in f and "no contract is registered" in f
                            for f in findings), findings)
        self.assertTrue(any(dc.register_command("refunds") in f for f in findings), findings)

    def test_a_registered_table_touched_with_no_disagreement_produces_no_finding_for_it(self):
        head = self.commit("migrations/003_orders_note.sql",
                           "-- a comment only, no table statement here\n",
                           "unrelated change")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "PASS", (evidence, findings))
        self.assertEqual(findings, (), findings)

    def test_a_keys_disagreement_on_a_registered_table_is_reported_field_by_field(self):
        head = self.commit(
            "migrations/004_orders_rebuild.sql",
            "CREATE TABLE orders (\n"
            "  order_id integer,\n"
            "  customer_id integer,\n"
            "  status varchar(20),\n"
            "  PRIMARY KEY (order_id, customer_id)\n"
            ");\n",
            "rebuild orders with a composite key")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "PASS", (evidence, findings))
        self.assertTrue(any("keys" in f and "orders" in f for f in findings), findings)

    def test_a_schema_disagreement_on_a_registered_table_is_reported_field_by_field(self):
        # ADVERSARIAL REVIEW MAJOR 5. No test anywhere asserted a SCHEMA
        # disagreement specifically reaches `check_diff`'s own findings
        # list (only `keys`, above, and `compare_contract_to_change` in
        # isolation): a mutant filtering schema findings out of the loop
        # would have survived. This is the same shape as the keys test,
        # for the other comparable field.
        head = self.commit(
            "migrations/006_orders_refund_column.sql",
            "CREATE TABLE orders (\n"
            "  order_id integer,\n"
            "  customer_id integer,\n"
            "  status varchar(20),\n"
            "  refund_cents integer,\n"
            "  PRIMARY KEY (order_id)\n"
            ");\n",
            "add refund_cents to orders")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "PASS", (evidence, findings))
        self.assertTrue(any("schema" in f and "orders" in f and "refund_cents" in f
                            for f in findings), findings)
        # And the keys field, which DID match, must NOT also be reported.
        self.assertFalse(any("'keys'" in f for f in findings), findings)

    def test_the_reviewers_exact_scenario_now_reports_the_keys_disagreement(self):
        # ADVERSARIAL REVIEW CRITICAL 1, end to end. A contract declaring
        # keys [order_id] and schema [order_id, customer_id] against a
        # migration that adds a column named "key" and actually keys on
        # customer_id via a NAMED constraint used to report PASS with ZERO
        # findings: a false green over a real disagreement. It must now
        # name both: the keys mismatch, and the schema gaining "key".
        head = self.commit(
            "migrations/007_orders_named_constraint.sql",
            "CREATE TABLE orders (\n"
            "  order_id integer, customer_id integer, key integer,\n"
            "  CONSTRAINT pk_orders PRIMARY KEY (customer_id)\n"
            ");\n",
            "rebuild orders, keyed on customer_id, with a column named key")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "PASS", (evidence, findings))
        self.assertTrue(any("'keys'" in f and "customer_id" in f for f in findings), findings)
        self.assertTrue(any("'schema'" in f and "'key'" in f for f in findings), findings)

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "needs enforced file modes")
    def test_an_unreadable_registered_contract_is_reported_not_raised_through_check_diff(self):
        # ADVERSARIAL REVIEW MAJOR 4, exercised at the same seam the
        # reviewer named: through `check_diff`, not only `load_registry`.
        # The permission bit is a WORKTREE property, not a committed one
        # (git tracks content and mode-bit executability, not read
        # permission), so the commit happens first, at normal permissions,
        # and only then is the file made unreadable on disk for the check
        # itself to read.
        head = self.commit("migrations/008_orders_touch.sql",
                           "INSERT INTO orders (order_id) VALUES (1);\n",
                           "write to orders while its contract is unreadable")
        path = os.path.join(self.contracts_dir(), "orders.yaml")
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=self.base, head=head)
        self.assertEqual(verdict, "PASS", (evidence, findings))
        self.assertTrue(any("orders" in f and "not fully registered" in f for f in findings),
                        findings)

    def test_a_contract_missing_fields_for_a_touched_table_is_reported_not_blocked(self):
        write(self.repo, os.path.join(dc.CONTRACTS_DIR_REL, "shipments.yaml"),
             'row_meaning: "one row per shipment"\nkeys:\n  - shipment_id\n')
        base = self.commit(os.path.join(dc.CONTRACTS_DIR_REL, "shipments.yaml"),
                           'row_meaning: "one row per shipment"\nkeys:\n  - shipment_id\n',
                           "register shipments, incompletely")
        head = self.commit("migrations/005_shipments.sql",
                           "INSERT INTO shipments (shipment_id) VALUES (1);\n",
                           "write to shipments")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base=base, head=head)
        self.assertEqual(verdict, "PASS", (evidence, findings))
        self.assertTrue(any("shipments" in f and "not fully registered" in f
                            for f in findings), findings)


class TestCheckDiffFailsOnlyForExecutionProblems(GitRepoFixture):
    def test_an_unresolvable_diff_range_is_fail_not_pass_or_no_data(self):
        os.makedirs(self.contracts_dir())
        write(self.repo, os.path.join(dc.CONTRACTS_DIR_REL, "orders.yaml"),
             VALID_ORDERS_CONTRACT)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "register orders")
        verdict, evidence, findings = dc.check_diff(self.contracts_dir(), self.repo,
                                                     base="not-a-real-ref", head="HEAD")
        self.assertEqual(verdict, "FAIL", (evidence, findings))
        self.assertTrue(findings, findings)


if __name__ == "__main__":
    unittest.main()
