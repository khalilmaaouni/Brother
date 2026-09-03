"""The data contract registry: one file per table, so "a data contract must be
registered before anything is built" is a check that runs and not a policy on
a slide.

NOT TO BE CONFUSED WITH `brothersbe.contracts` (LP-0201). That module is this
project's OWN internal JSON schema registry, for five surfaces THIS TOOL
emits (`sbe status --json`, the task registry, the work brief, the handover
record, `sbe status --team --json`). This module is a different, external
capability: a registry of DATA TABLE contracts, in a tiny YAML subset, that
any repository using this tool can keep under `contracts/` for the tables ITS
OWN pipelines write. The two share the word "contract" and nothing else; this
module imports nothing from `brothersbe.contracts` and `brothersbe.contracts`
imports nothing from here.

THE THREE PARTS.

1. A contract file. One file per table, under `contracts/` at the repository
   root, named `<fully qualified table name>.yaml` (dots in the table name
   stay in the filename: `analytics.events.users.yaml` describes the table
   `analytics.events.users`). Format is a strict, tiny YAML subset this
   module parses itself (`read_contract_text`): flat `key: value` pairs,
   quoted or bare scalar strings, and simple `- item` lists one level deep.
   Nothing else. A construct outside that subset (a nested mapping, an
   anchor, a multi-line block scalar, a flow collection, a YAML tag, a
   document marker, tab indentation) is REFUSED BY NAME: `ContractFormatError`
   names the exact line and the construct, never guessed at and never
   silently dropped. `brothersbe.program`'s own YAML-subset reader
   (`load_yaml_file`) is not reused here because it deliberately reads MORE of
   real YAML than this format allows (nested mappings included, for
   `PROGRAM.yaml`'s own shape): reusing it would make every construct this
   module must refuse parse successfully instead.

2. Whether a contract counts as REGISTERED (`validate_contract_fields`).
   Seven required fields, all of them, in snake_case:

     row_meaning          what one row of this table represents
     keys                 the column(s) that together identify one row
     schema               the table's columns, one list item per column
     freshness             how stale this table may be before it is late
     quality_thresholds   the measurable quality rules this table must hold
     allowed_readers      who (roles, not people) may read this table
     change_notice        the notice period owed before a breaking change

   A contract missing any of these is refused by name, naming EVERY missing
   field in one sentence, not just the first found. A field that is present
   but blank or a placeholder (empty string, empty list, "TODO", "N/A", the
   same vocabulary `sbe_checks.answered()` already refuses for every other
   check in this project) is refused the same way, named separately from a
   field that is simply absent, because "nobody wrote this yet" and "somebody
   typed a value and it says nothing" are different findings.

3. Whether a CHANGE respects the registry (`check_diff`). Given a git range,
   this walks the changed files' ADDED lines (`brothersbe.impact.added_lines`,
   not reinvented here), extracts the table names a small, named set of SQL
   constructs defines or writes (`CREATE TABLE`, `INSERT INTO`, `MERGE INTO`,
   `UPDATE ... SET`, `COPY INTO`), and checks each against the registry. A
   touched table with NO registered contract, or a registered contract that
   itself fails `validate_contract_fields`, REPORTS LOUDLY: the table is
   named, "no contract is registered" (or the specific missing/blank fields)
   is stated, and the exact command to register one is printed. It NEVER
   changes `check_diff`'s verdict to something that could fail a pipeline:
   `check_diff` returns "FAIL" only for a genuine execution problem (the
   registry directory exists but cannot be listed, the diff range cannot be
   resolved), never for "this table has no contract". A registered, complete
   contract is additionally compared against what a `CREATE TABLE` statement
   in the diff implies, FIELD BY FIELD, for the two fields a diff can
   genuinely support: `keys` (against a primary key stated either as a
   table-level `PRIMARY KEY (...)` clause, named or not, or as an inline
   column-level constraint) and `schema` (the column NAME set, against the
   statement's column list, a table-level constraint clause distinguished
   from a column definition by its own parenthesized list rather than by
   its keyword alone). The
   other five required fields (`row_meaning`, `freshness`,
   `quality_thresholds`, `allowed_readers`, `change_notice`) cannot be
   derived from a diff at all and are NEVER compared or claimed to be: a
   check that implied it covered them would be a control that oversells
   itself.

THE HOUSE THREE-TUPLE. Every function below whose job is to report a finding
returns `(verdict, evidence, problems)`: `verdict` is one of `"PASS"`,
`"FAIL"`, `"NO-DATA"`; `evidence` is one human-readable summary line;
`problems` is a tuple of named, individual findings, empty on `"PASS"`. An
absent or empty `contracts/` directory is `"NO-DATA"`, never `"PASS"`: no
registry means nothing was checked, not that nothing is wrong. Every one of
these returns is a 3-tuple on purpose, never a bare 2-tuple: this project's
own honesty sweep (`evals/test_no_data_class.py`, `tool_sources()`) reads
every 2-element tuple a `tools/` function can return as a candidate verdict
pair it must prove is never `"PASS"`, and a 3-tuple is outside that shape by
construction, the same convention `brothersbe.contracts`'s own module
docstring already states for its five validators.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only: the YAML subset reader is this module's own, and the git-diff
reading reuses `brothersbe.impact`'s already-tested subprocess wrapper rather
than a second one.
"""
import io
import os
import re

from .impact import DiffUnavailable, added_lines, changed_files, resolve_range

from ._toolspath import mount
mount()
from sbe_checks import answered  # noqa: E402

#: Where a registered contract lives, relative to the repository root.
CONTRACTS_DIR_REL = "contracts"

#: The extension a contract file must carry. Anything else under `contracts/`
#: is not a contract file and is silently not counted as one (a README, a
#: `.gitkeep`); it is not, however, a reason to call the directory empty if a
#: real `.yaml` sits beside it.
CONTRACT_EXT = ".yaml"

#: The seven required fields, in the order this module always reports them.
REQUIRED_FIELDS = ("row_meaning", "keys", "schema", "freshness",
                   "quality_thresholds", "allowed_readers", "change_notice")

#: One-line meaning of each required field, for error messages and docs.
FIELD_MEANING = {
    "row_meaning": "what one row of this table represents",
    "keys": "the column(s) that together identify one row",
    "schema": "the table's columns, one list item per column",
    "freshness": "how stale this table may be before it is late",
    "quality_thresholds": "the measurable quality rules this table must hold",
    "allowed_readers": "who (roles, not people) may read this table",
    "change_notice": "the notice period owed before a breaking change",
}

#: Fields whose value must be a list (`- item` lines).
LIST_FIELDS = ("keys", "schema", "quality_thresholds", "allowed_readers")

#: Fields whose value must be a single scalar string.
SCALAR_FIELDS = ("row_meaning", "freshness", "change_notice")

assert set(LIST_FIELDS) | set(SCALAR_FIELDS) == set(REQUIRED_FIELDS)


class ContractFormatError(Exception):
    """A contract file, or a git diff's SQL, could not be read as the
    documented subset. Always names the line and the construct: refused by
    name, never guessed at and never silently ignored."""


# ---------------------------------------------------------------------------
# 1. The tiny YAML subset reader
# ---------------------------------------------------------------------------

#: The line, verbatim, kept beside every raised error so a caller building its
#: own sentence has it without re-deriving it from the message text.

_TOP_KEY_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):(.*)$')
_LIST_ITEM_RE = re.compile(r'^- ?(.*)$')
_MAPPING_SHAPED_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*:(\s|$)')

#: A sentinel distinguishing "this key has been declared and awaits `- item`
#: lines" from "this key already holds a real (possibly empty) list": a key
#: declared with no items at all resolves to `[]`, the same as one written
#: with items and later emptied would not, but nothing here ever needs that
#: second case, so the sentinel only has to survive to end-of-file.
_PENDING_LIST = object()


def _parse_value(text, lineno, raw_line):
    """One scalar string from `text` (already stripped), or raise naming the
    construct `text` opens that this subset does not support."""
    if text == "":
        return ""
    ch = text[0]
    if ch in "&*":
        raise ContractFormatError(
            "line %d: YAML anchor/alias is not supported (%r)" % (lineno, raw_line))
    if ch in "|>":
        raise ContractFormatError(
            "line %d: multi-line block scalar is not supported (%r)" % (lineno, raw_line))
    if ch in "[{":
        raise ContractFormatError(
            "line %d: flow collection is not supported (%r)" % (lineno, raw_line))
    if ch == "!":
        raise ContractFormatError(
            "line %d: YAML tag is not supported (%r)" % (lineno, raw_line))
    if ch in ("'", '"'):
        return _parse_quoted(text, lineno, ch, raw_line)
    return text


def _parse_quoted(text, lineno, quote, raw_line):
    if len(text) < 2 or not text.endswith(quote):
        raise ContractFormatError(
            "line %d: unterminated quoted string (%r)" % (lineno, raw_line))
    inner = text[1:-1]
    if quote == "'":
        if "'" in inner:
            raise ContractFormatError(
                "line %d: an escaped quote inside a single-quoted string is not supported "
                "(%r)" % (lineno, raw_line))
        return inner
    out, i = [], 0
    while i < len(inner):
        c = inner[i]
        if c == "\\" and i + 1 < len(inner) and inner[i + 1] in ('"', "\\"):
            out.append(inner[i + 1])
            i += 2
        elif c == "\\":
            raise ContractFormatError(
                "line %d: unsupported escape sequence in a double-quoted string (%r)"
                % (lineno, raw_line))
        elif c == '"':
            raise ContractFormatError(
                "line %d: an unescaped '\"' inside a double-quoted string (%r)"
                % (lineno, raw_line))
        else:
            out.append(c)
            i += 1
    return "".join(out)


def read_contract_text(text, source_name="<contract>"):
    """The parsed contract, `{field: str}` or `{field: [str, ...]}`, from
    `text` written in the documented subset. Raises `ContractFormatError`,
    naming the line and the construct, for anything outside it: a nested
    mapping, an anchor, a multi-line block scalar, a flow collection, a YAML
    tag, a document marker, tab indentation, or a duplicate key.

    The subset, exactly: a line is blank, a whole-line comment (`#` as the
    first non-space character), a top-level `key: value` pair (indent 0), or
    a list item (`- value`, indent exactly 2, following a key declared with
    no inline value). Nothing else parses; everything else is named and
    refused, never approximated.

    Lines are split on `"\\n"` only (after normalizing `"\\r\\n"` and lone
    `"\\r"` to `"\\n"`), never with `str.splitlines()`: that method also
    breaks on U+2028 LINE SEPARATOR, U+0085 NEXT LINE and several other
    Unicode separators, so a scalar value carrying one of those characters
    used to be silently cut into two "lines" this reader then parsed
    separately, MANUFACTURING a field (or a parse error) out of a single
    logical line, and shifting every later line number the caller sees.
    Splitting on `"\\n"` alone means a value containing U+2028 stays one
    physical line, and the line numbers this function reports match the
    caller's own count of real newlines.
    """
    result = {}
    pending_key = None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for lineno, raw_line in enumerate(normalized.split("\n"), start=1):
        if "\t" in raw_line[:len(raw_line) - len(raw_line.lstrip(" \t"))]:
            raise ContractFormatError(
                "line %d: tab indentation is not supported (%r)" % (lineno, raw_line))
        stripped = raw_line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line[indent:]
        if content.rstrip() in ("---", "..."):
            raise ContractFormatError(
                "line %d: a YAML document marker is not supported (%r)" % (lineno, raw_line))
        if indent == 0:
            m = _TOP_KEY_RE.match(content)
            if not m:
                raise ContractFormatError(
                    "line %d: %r is not a supported top-level 'key: value' pair"
                    % (lineno, raw_line))
            key, rest = m.group(1), m.group(2)
            if key in result:
                raise ContractFormatError(
                    "line %d: duplicate key %r (%r)" % (lineno, key, raw_line))
            if rest and not rest.startswith(" "):
                raise ContractFormatError(
                    "line %d: missing space after ':' (%r)" % (lineno, raw_line))
            value_part = rest.strip()
            if value_part == "":
                result[key] = _PENDING_LIST
                pending_key = key
            else:
                result[key] = _parse_value(value_part, lineno, raw_line)
                pending_key = None
        elif indent == 2:
            if pending_key is None:
                if _MAPPING_SHAPED_RE.match(content):
                    raise ContractFormatError(
                        "line %d: nested mapping under %r is not supported (%r)"
                        % (lineno, pending_key or "the previous key", raw_line))
                raise ContractFormatError(
                    "line %d: unexpected indentation; a list item must directly follow a key "
                    "with no inline value (%r)" % (lineno, raw_line))
            m = _LIST_ITEM_RE.match(content)
            if not m:
                if _MAPPING_SHAPED_RE.match(content):
                    raise ContractFormatError(
                        "line %d: nested mapping under %r is not supported (%r)"
                        % (lineno, pending_key, raw_line))
                raise ContractFormatError(
                    "line %d: %r is not a supported list item ('- value') (%r)"
                    % (lineno, content, raw_line))
            item = _parse_value(m.group(1).strip(), lineno, raw_line)
            if result[pending_key] is _PENDING_LIST:
                result[pending_key] = []
            result[pending_key].append(item)
        else:
            raise ContractFormatError(
                "line %d: indentation of %d space(s) is not supported (only 0 or 2); nested "
                "mapping (%r)" % (lineno, indent, raw_line))
    for key, value in result.items():
        if value is _PENDING_LIST:
            result[key] = []
    return result


def read_contract_file(path):
    """The parsed contract at `path`, or `None` if no such file exists.
    Raises `ContractFormatError` (via `read_contract_text`) if the file
    exists and does not parse, and `OSError` if it exists and cannot be
    read (a permission problem): absence, a broken claim, and a format
    error are three different findings and this keeps them apart, the same
    split `tools/sbe_checks.py`'s own `evidence_problem` makes for evidence
    files."""
    if not os.path.lexists(path):
        return None
    with io.open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return read_contract_text(text, source_name=path)


# ---------------------------------------------------------------------------
# 2. Whether a contract counts as registered
# ---------------------------------------------------------------------------

def _blank_fields(parsed):
    """Every present required field whose value is blank or a placeholder,
    as `(field, reason)` pairs. `answered()` (`tools/sbe_checks.py`) is the
    shared vacuity test every other check in this project already uses, so a
    "TODO" or an empty string is refused here the same way it is refused
    everywhere else, and not through a second, private list this module
    would have to keep in sync with that one.

    For a list field, EVERY item must be answered, not just one. The
    earlier reading ("the field counts as answered if ANY item is") let one
    real value launder every placeholder sitting beside it: a contract
    declaring `keys: [TODO, order_id]` read as a fully answered `keys`
    field, because `order_id` alone satisfied "any". A placeholder is
    refused wherever it sits in the list, and the message says which ones
    (by position and value) so an author does not have to hunt for them.
    """
    blanks = []
    for field in REQUIRED_FIELDS:
        if field not in parsed:
            continue
        value = parsed[field]
        if field in LIST_FIELDS:
            items = value if isinstance(value, list) else [value]
            if not items:
                blanks.append((field, "an empty list"))
                continue
            unanswered = [(i, item) for i, item in enumerate(items) if answered(item) is None]
            if len(unanswered) == len(items):
                blanks.append((field, "a list of only blank or placeholder value(s)"))
            elif unanswered:
                blanks.append((field, "%d of %d item(s) blank or a placeholder value (position(s) "
                                      "%s: %s)"
                              % (len(unanswered), len(items),
                                 ", ".join(str(i) for i, _v in unanswered),
                                 ", ".join(repr(v) for _i, v in unanswered))))
        else:
            if answered(value) is None:
                blanks.append((field, "blank or a placeholder value"))
    return blanks


def validate_contract_fields(parsed):
    """`(verdict, evidence, problems)` for one already-parsed contract.

    `"NO-DATA"` when `parsed` is `None` (no document was given to validate).
    `"FAIL"` when `parsed` is not a mapping, or is missing one or more of the
    seven `REQUIRED_FIELDS`, or holds one blank or placeholder: EVERY such
    field is named in `problems`, not only the first. `"PASS"` only when
    every required field is present and holds a real, non-blank value.
    """
    if parsed is None:
        return "NO-DATA", "no contract document was given to validate", ()
    if not isinstance(parsed, dict):
        problem = ("this contract is a %s, not a mapping of field to value"
                  % type(parsed).__name__)
        return "FAIL", problem, (problem,)

    problems = []
    missing = sorted(f for f in REQUIRED_FIELDS if f not in parsed)
    if missing:
        problems.append("missing required field(s): %s" % ", ".join(missing))
    for field, reason in _blank_fields(parsed):
        problems.append("%s is present but %s" % (field, reason))

    if problems:
        return "FAIL", "%d problem(s): %s" % (len(problems), "; ".join(problems)), tuple(problems)
    return "PASS", ("all %d required field(s) present and answered: %s"
                    % (len(REQUIRED_FIELDS), ", ".join(REQUIRED_FIELDS))), ()


def register_command(table):
    """The exact command line an author is told to run to register `table`."""
    return ("create %s/%s%s with the seven required fields (%s)"
           % (CONTRACTS_DIR_REL, table, CONTRACT_EXT, ", ".join(REQUIRED_FIELDS)))


def load_registry(contracts_dir):
    """`(verdict, table_map, problems)` for every contract file directly under
    `contracts_dir`.

    `"NO-DATA"` when `contracts_dir` does not exist, or exists and holds zero
    `.yaml` files: a registry directory that does not exist, or registers
    nothing, is NO-DATA, never "clean" and never a pass. `"FAIL"` when the
    directory exists but cannot be listed (a permission problem): present but
    unreadable is a broken claim, not an absence. Otherwise `"PASS"`, with
    `table_map` mapping every table name found to
    `{"path", "parsed", "field_verdict", "field_evidence", "field_problems"}`;
    a contract file that itself fails to parse or fails
    `validate_contract_fields` still gets an entry here (`parsed` is `None`
    and `field_verdict` is `"FAIL"` on a parse error), because "this table
    has a contract file and it is broken" is a different, more specific
    finding than "this table has no contract file", and `check_diff` reports
    them differently.
    """
    if not os.path.isdir(contracts_dir):
        return ("NO-DATA",
                {},
                ("%s does not exist; nothing is registered" % contracts_dir,))
    try:
        names = sorted(os.listdir(contracts_dir))
    except OSError as exc:
        problem = "%s exists but could not be listed (%s)" % (contracts_dir, exc)
        return "FAIL", {}, (problem,)
    # A dangling symlink named `*.yaml` (its target deleted or never valid)
    # fails `os.path.isfile` (which follows the link), so an `isfile`-only
    # filter used to drop it from `files` entirely: the table it claimed to
    # describe then read as "no contract registered" instead of "this
    # table's contract exists and cannot be read", a broken claim reported
    # as an absence. `os.path.islink` catches it whether or not it resolves;
    # the read attempt below (which DOES raise for a dangling target) is
    # what turns it into a named FAIL rather than a silent NO-DATA.
    files = [n for n in names if n.endswith(CONTRACT_EXT)
            and (os.path.isfile(os.path.join(contracts_dir, n))
                 or os.path.islink(os.path.join(contracts_dir, n)))]
    if not files:
        return ("NO-DATA",
                {},
                ("%s exists but holds zero %s contract file(s)"
                 % (contracts_dir, CONTRACT_EXT),))

    table_map = {}
    problems = []
    for name in files:
        table = name[:-len(CONTRACT_EXT)]
        path = os.path.join(contracts_dir, name)
        try:
            parsed = read_contract_file(path)
        except ContractFormatError as exc:
            table_map[table] = {"path": path, "parsed": None, "field_verdict": "FAIL",
                                "field_evidence": str(exc), "field_problems": (str(exc),)}
            problems.append("%s: %s" % (path, exc))
            continue
        except OSError as exc:
            # `read_contract_file`'s own docstring says it raises `OSError`
            # for a file that exists and cannot be read (permissions, a
            # dangling symlink target). This used to propagate straight out
            # of `load_registry` and, from there, out of `check_diff`: a
            # caller got a traceback and a non-zero process exit where this
            # module documents a `"FAIL"` verdict, which is exactly the
            # never-block promise breaking on the one path that must not.
            reason = "%s could not be read (%s)" % (path, exc)
            table_map[table] = {"path": path, "parsed": None, "field_verdict": "FAIL",
                                "field_evidence": reason, "field_problems": (reason,)}
            problems.append(reason)
            continue
        verdict, evidence, field_problems = validate_contract_fields(parsed)
        table_map[table] = {"path": path, "parsed": parsed, "field_verdict": verdict,
                            "field_evidence": evidence, "field_problems": field_problems}
        if verdict != "PASS":
            problems.append("%s (table %r): %s" % (path, table, evidence))
    return "PASS", table_map, tuple(problems)


# ---------------------------------------------------------------------------
# 3. Table names a diff defines or writes, and whether the change respects
#    the registry
# ---------------------------------------------------------------------------

#: One SQL identifier, optionally wrapped in the one quoting style each
#: dialect uses (backtick, double quote, or a bracket pair for SQL Server).
#: The wrapping character is matched but NOT captured: group 1 is always the
#: bare identifier text. An earlier version of every pattern below required
#: a bare letter or underscore as the very first character, so a quoted or
#: bracketed identifier (`` `orders` ``, `"orders"`, `[orders]`) never
#: reached the pattern at all, and `_clean_identifier`'s own quote-stripping
#: existed for nothing: nothing upstream of it ever handed it a quoted
#: name to strip.
_IDENT = r'(?:`([A-Za-z_][\w.$]*)`|"([A-Za-z_][\w.$]*)"|\[([A-Za-z_][\w.$]*)\]|([A-Za-z_][\w.$]*))'


def _ident_group(m, base):
    """The identifier text from a match built on `_IDENT` at capture-group
    offset `base` (`_IDENT` opens 4 alternative groups; exactly one of them
    is non-`None` for any match)."""
    for i in range(base, base + 4):
        if m.group(i) is not None:
            return m.group(i)
    return None


#: The SQL constructs this module recognizes as defining or writing a table,
#: each with the label `check_diff` reports it under. Deliberately narrow and
#: named here rather than approximated: an ORM call, a dbt model file named
#: after its table, or any non-SQL way of writing to a table is NOT detected,
#: and this module never claims otherwise.
_TABLE_PATTERNS = (
    ("CREATE TABLE", re.compile(
        r'\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?' + _IDENT,
        re.IGNORECASE), 1),
    ("INSERT INTO", re.compile(r'\bINSERT\s+INTO\s+' + _IDENT, re.IGNORECASE), 1),
    ("MERGE INTO", re.compile(r'\bMERGE\s+INTO\s+' + _IDENT, re.IGNORECASE), 1),
    # An alias between the table name and SET (`UPDATE orders o SET ...` or
    # `UPDATE orders AS o SET ...`) used to break this pattern entirely,
    # because it required SET immediately after the table name: any aliased
    # UPDATE, a completely ordinary way to write one, matched nothing and
    # the table it wrote went undetected. The alias is optional and
    # unnamed here (this pattern does not need it), and the regex engine's
    # own backtracking is what lets the SAME pattern still match the
    # un-aliased form (`UPDATE orders SET ...`): with no alias, the optional
    # group matches zero characters and `SET` is found right after the
    # table name, exactly as before.
    ("UPDATE", re.compile(
        r'\bUPDATE\s+' + _IDENT + r'\s+(?:AS\s+)?(?:[A-Za-z_]\w*\s+)?SET\b', re.IGNORECASE), 1),
    ("COPY INTO", re.compile(r'\bCOPY\s+INTO\s+' + _IDENT, re.IGNORECASE), 1),
)


def _clean_identifier(raw):
    return raw.strip("`\"[]();, \t")


def _strip_sql_line_comments(text):
    """`text` with everything from an unescaped `--` to the end of its line
    removed, line by line. A commented-out `INSERT INTO orders ...` used to
    be detected exactly like a real one, so this module would tell an
    author to register a table that does not exist (or would compare a
    contract against a statement nobody's build will ever run). This is a
    textual heuristic, not a SQL tokenizer: it does not know a `--` sitting
    inside a quoted string literal is not a comment opener, so a value
    genuinely containing `--` (`'a--b'`) has everything after it stripped
    too. That residual is stated here, in the docstring of the one function
    that has it, rather than silently accepted as complete comment
    handling.
    """
    return "\n".join(line[:line.find("--")] if "--" in line else line
                     for line in text.split("\n"))


def extract_table_names(text):
    """`[(table, construct)]`, one entry per distinct table name `text`
    defines or writes, in the order first seen. `construct` names which of
    `_TABLE_PATTERNS` matched (`"CREATE TABLE"`, `"INSERT INTO"`, ...):
    a caller that wants every construct that matched a given table, not just
    the first, should scan `_TABLE_PATTERNS` itself; this is the summary
    view `check_diff` needs for its report. SQL line comments (`-- ...`) are
    stripped first (see `_strip_sql_line_comments`), so a commented-out
    statement is never reported as a real one."""
    scanned = _strip_sql_line_comments(text)
    seen = {}
    for construct, pattern, base in _TABLE_PATTERNS:
        for m in pattern.finditer(scanned):
            table = _clean_identifier(_ident_group(m, base) or "")
            if table and table not in seen:
                seen[table] = construct
    return [(table, seen[table]) for table in seen]


def _matching_paren_body(text, open_idx):
    """The text strictly between `text[open_idx]` (a `'('`) and its matching
    `')'`, or `None` if the diff's added lines do not include the close (a
    `CREATE TABLE` whose column list was only partly added is not something
    this module guesses the rest of)."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return None


def _split_top_level(body):
    """`body` split on commas at parenthesis depth 0, so `NUMERIC(10,2)`
    inside one column definition is not mistaken for two items."""
    items, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        items.append("".join(current))
    return [i.strip() for i in items if i.strip()]


#: A table-level constraint CLAUSE, as opposed to a column definition that
#: merely happens to start with a word this set also uses. The distinguisher
#: is the parenthesized list every one of these constructs takes: a
#: `CREATE TABLE`'s constraint clauses are always `[CONSTRAINT <name>]
#: KEYWORD (...)`, never a bare keyword with no paren following it. This
#: matters because `KEY`, `CHECK`, `INDEX` and `UNIQUE` are also ordinary,
#: legal column names (`key integer` is a column definition, not a
#: constraint), and an earlier version of this function treated the WORD
#: alone as proof of a constraint clause: a column genuinely named `key`
#: (or `check`, `index`, `unique`, `constraint`) was silently dropped from
#: `columns` on every `CREATE TABLE` that had one. Requiring the paren fixes
#: that: a column definition's second token is a data type, never `(`.
_CONSTRAINT_CLAUSE_RE = re.compile(
    r'^(?:CONSTRAINT\s+\S+\s+)?(?:PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|INDEX|KEY)\s*\(',
    re.IGNORECASE)

_COLUMN_NAME_RE = re.compile(r'^[`"\[]?([A-Za-z_][\w]*)')
_PRIMARY_KEY_RE = re.compile(r'PRIMARY\s+KEY\s*\(([^)]*)\)', re.IGNORECASE)
#: An INLINE, column-level `PRIMARY KEY` with no parenthesized list at all
#: (`order_id INT PRIMARY KEY`), as opposed to the table-level clause
#: `_PRIMARY_KEY_RE` reads. Matched with `\b` boundaries only, deliberately
#: NOT anchored to the start of the item, because it can appear after other
#: column constraints (`order_id INT NOT NULL PRIMARY KEY`).
_INLINE_PRIMARY_KEY_RE = re.compile(r'\bPRIMARY\s+KEY\b', re.IGNORECASE)


def _columns_and_primary_key(body):
    """`(column_names, primary_key_columns_or_None)` from a `CREATE TABLE`'s
    parenthesized body. `primary_key_columns` is `None` when the body names
    no primary key at all, by either spelling below: "not stated in this
    diff" and "stated as empty" are different, and only the first is `None`
    here. Column TYPES are not read: matching column NAMES is the scoped,
    honest comparison this module makes against the contract's `schema`
    field.

    TWO spellings of a primary key are read, because an earlier version of
    this function read only the first and returned `None` for the second,
    which sent every comparison against `keys` silently uncompared for any
    migration written the second way:

    1. A table-level clause, with or without a name:
       `PRIMARY KEY (col1, col2)` or `CONSTRAINT pk_orders PRIMARY KEY
       (col1, col2)`. Read via `_PRIMARY_KEY_RE`, searched over the WHOLE
       item text (not gated behind the item's first word), so the
       `CONSTRAINT <name>` prefix does not hide the clause behind it.
    2. An inline, column-level constraint with no parenthesized list at all:
       `order_id INTEGER PRIMARY KEY`. Read as `[that column's name]` when
       no table-level clause was found.
    """
    columns = []
    primary_key = None
    inline_pk_column = None
    for item in _split_top_level(body):
        if _CONSTRAINT_CLAUSE_RE.match(item.strip()):
            m = _PRIMARY_KEY_RE.search(item)
            if m:
                primary_key = [_clean_identifier(c) for c in m.group(1).split(",") if c.strip()]
            continue
        m = _COLUMN_NAME_RE.match(item)
        if not m:
            continue
        colname = m.group(1)
        columns.append(colname)
        if inline_pk_column is None and _INLINE_PRIMARY_KEY_RE.search(item):
            inline_pk_column = colname
    if primary_key is None and inline_pk_column is not None:
        primary_key = [inline_pk_column]
    return columns, primary_key


def _create_table_body(text, table):
    """The parenthesized body of a `CREATE TABLE <table> (...)` statement in
    `text`, or `None` if none names `table` with a complete, balanced body.
    SQL line comments are stripped first, the same as `extract_table_names`,
    so a commented-out `CREATE TABLE` for `table` is never read as its real
    definition."""
    scanned = _strip_sql_line_comments(text)
    _construct, pattern, base = _TABLE_PATTERNS[0]  # CREATE TABLE
    for m in pattern.finditer(scanned):
        if _clean_identifier(_ident_group(m, base) or "") != table:
            continue
        open_idx = scanned.find("(", m.end())
        if open_idx == -1:
            continue
        body = _matching_paren_body(scanned, open_idx)
        if body is not None:
            return body
    return None


def _schema_column_name(item):
    m = _COLUMN_NAME_RE.match(item.strip())
    return m.group(1) if m else None


def compare_contract_to_change(parsed_contract, columns, primary_key):
    """`[{"field", "contract_says", "change_implies"}]`: every one of the TWO
    fields this module can genuinely compare against a `CREATE TABLE`
    statement (`keys` against a `PRIMARY KEY` clause, `schema` against the
    column name list) where the contract and the diff disagree. A field is
    compared only when BOTH sides are determinable; `row_meaning`,
    `freshness`, `quality_thresholds`, `allowed_readers` and `change_notice`
    are never in this list, on any input, because nothing in a diff says
    what they should be.
    """
    disagreements = []
    if primary_key is not None and "keys" in parsed_contract:
        contract_keys = parsed_contract["keys"]
        contract_keys = contract_keys if isinstance(contract_keys, list) else [contract_keys]
        if set(contract_keys) != set(primary_key):
            disagreements.append({"field": "keys", "contract_says": list(contract_keys),
                                  "change_implies": list(primary_key)})
    if columns and "schema" in parsed_contract:
        raw_schema = parsed_contract["schema"]
        raw_schema = raw_schema if isinstance(raw_schema, list) else [raw_schema]
        contract_cols = sorted({c for c in
                                (_schema_column_name(i) for i in raw_schema) if c})
        change_cols = sorted(set(columns))
        if contract_cols != change_cols:
            disagreements.append({"field": "schema", "contract_says": contract_cols,
                                  "change_implies": change_cols})
    return disagreements


def check_diff(contracts_dir, cwd, base=None, head="HEAD"):
    """`(verdict, evidence, findings)` for the tables the git range
    `base..head` touches, against the registry at `contracts_dir`.

    `"NO-DATA"` when the registry itself is NO-DATA (`load_registry`):
    absent, entirely, regardless of the diff. `"FAIL"` only for a genuine
    execution problem: the registry directory exists but cannot be listed,
    or the diff range cannot be resolved. Otherwise always `"PASS"`, even
    when `findings` names touched tables with no contract, a contract
    missing fields, or a `keys`/`schema` disagreement: this function REPORTS
    those loudly, in `findings`, and never turns them into a verdict a
    caller could wire to a failing exit code. That is the design, not an
    oversight: see the module docstring.
    """
    reg_verdict, table_map, reg_problems = load_registry(contracts_dir)
    if reg_verdict == "NO-DATA":
        return "NO-DATA", "; ".join(reg_problems), ()
    if reg_verdict == "FAIL":
        return "FAIL", "; ".join(reg_problems), reg_problems

    try:
        resolved_base, resolved_head = resolve_range(cwd, base, head)
        files = changed_files(cwd, resolved_base, resolved_head)
    except DiffUnavailable as exc:
        problem = "the diff could not be resolved: %s" % exc
        return "FAIL", problem, (problem,)

    tables_seen = {}
    for path in files:
        text = added_lines(cwd, resolved_base, resolved_head, path)
        if not text:
            continue
        for table, construct in extract_table_names(text):
            tables_seen.setdefault(table, []).append(
                {"path": path, "construct": construct, "text": text})

    findings = []
    for table in sorted(tables_seen):
        hits = tables_seen[table]
        entry = table_map.get(table)
        if entry is None:
            constructs = ", ".join(sorted({h["construct"] for h in hits}))
            findings.append("table %r touched (%s) and no contract is registered; %s"
                            % (table, constructs, register_command(table)))
            continue
        if entry["field_verdict"] != "PASS":
            findings.append(
                "table %r touched and its contract at %s is not fully registered (%s); %s"
                % (table, entry["path"], entry["field_evidence"], register_command(table)))
            continue
        for hit in hits:
            if hit["construct"] != "CREATE TABLE":
                continue
            body = _create_table_body(hit["text"], table)
            if body is None:
                continue
            columns, primary_key = _columns_and_primary_key(body)
            for d in compare_contract_to_change(entry["parsed"], columns, primary_key):
                findings.append(
                    "table %r: contract field %r says %r, the change in %s implies %r"
                    % (table, d["field"], d["contract_says"], hit["path"], d["change_implies"]))

    evidence = ("%d table(s) touched across %d changed file(s), %d finding(s)"
               % (len(tables_seen), len(files), len(findings)))
    return "PASS", evidence, tuple(findings)
