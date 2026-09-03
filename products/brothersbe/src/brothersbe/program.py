"""One truthful, program-wide answer to "where does the whole program stand",
built the same way `status.py` answers that question for a single change: by
reading state other commands and humans already recorded, never by inventing
a new verdict.

THE KILL CRITERION THIS MODULE IS BUILT AROUND, adopted verbatim in spirit
from `status.py`: if a truthful program summary cannot be produced without
running the suites, starting a subprocess, or computing a NEW verdict over
source code, this module stops and says so rather than doing any of those.

THE SOURCES, and only these:

  1. `program/PROGRAM.yaml`: the program record (objective, version target,
     token budget, milestones, waves, release gates).
  2. `program/work-items/*.yaml`: one file per work item.
  3. Nothing else. Not git, not the evidence store, not the task registry. A
     future item may add those; this one must not, because each added source
     is another thing that can be silently absent and read as clean.

THE LOAD-BEARING HONESTY RULE OF THIS LANE: a status word never becomes a
percentage. Progress comes from exactly one of three sources, in this order,
and the source is always named in the output:

  1. `percent_complete` when explicitly recorded: reported as `declared`.
  2. Otherwise, when `acceptance[]` is non-empty AND the item records
     `acceptance_met[]`: `met / total` as a percentage, reported as
     `derived from acceptance`.
  3. Otherwise: NOT MEASURED. The item contributes to no percentage
     aggregate, and every aggregate states how many items it covered out of
     how many exist.

`in_progress` does not mean 50 percent. Code that makes it mean 50 percent is
a defect this module refuses to contain.

THE PARSER: PyYAML is not available in this environment (standard library
only, per the floor below), and the ledger files are YAML. Rather than guess
at a full YAML grammar, this module ships a small, strict, documented subset
parser (`_yaml_load`) that handles exactly the shapes the shipped ledger
files use: nested maps, lists of scalars, lists of maps, block scalars (`>`
folded and `|` literal), single- and double-quoted scalars (including ones
that wrap across lines), plain scalars that wrap across lines, and full-line
or trailing comments. Anything outside that subset is a named PARSE ERROR,
never a guess.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only. No network. No subprocess. Maturity: INTERNAL-EVAL, exercised
on this repository's own ledger and on no other estate.
"""
import os
import re


SCHEMA_VERSION = "1.0"

PROGRAM_REL = os.path.join("program", "PROGRAM.yaml")
WORK_ITEMS_REL = os.path.join("program", "work-items")
STATUS_MD_REL = os.path.join("program", "STATUS.md")

BEGIN_MARKER = "<!-- BEGIN GENERATED PROGRAM STATUS -->"
END_MARKER = "<!-- END GENERATED PROGRAM STATUS -->"

#: Status vocabulary this module recognizes, case and separator insensitive.
#: `partially done`, `partially_done` and `PARTIALLY DONE` all normalize to
#: the canonical spelling below. An unrecognized status is a PARSE ERROR
#: naming the file and the value, never coerced to a neighbour.
STATUS_VALUES = (
    "not_started", "ready", "in_progress", "partially_done", "blocked",
    "done", "deferred", "cancelled",
)

_SEVERITY_VALUES = ("high", "medium", "low")


class ProgramParseError(Exception):
    """A ledger file could not be read as the documented YAML subset, or its
    content violates this lane's schema in a way that must never be silently
    coerced. Always carries the offending path and a plain-language reason,
    because "some file somewhere is wrong" protects nobody."""


# ---------------------------------------------------------------------------
# The YAML subset parser
# ---------------------------------------------------------------------------

_BLOCK_INDICATORS = ("|", ">", "|-", "|+", ">-", ">+")

_PLAIN_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*):(?:\s(.*)|)$")


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _is_skippable(line):
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")


def _strip_trailing_comment(text):
    """Strip a trailing `# comment` from a plain (unquoted) scalar fragment.
    A `#` only starts a comment when it is at the start of the fragment or is
    preceded by whitespace, matching the one YAML comment rule this subset
    honours."""
    in_squote = False
    in_dquote = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "'" and not in_dquote:
            in_squote = not in_squote
        elif ch == '"' and not in_squote:
            in_dquote = not in_dquote
        elif ch == "#" and not in_squote and not in_dquote:
            if i == 0 or text[i - 1] in " \t":
                return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _scan_quoted(line, quote_char):
    """Scan a quoted scalar starting at index 0 of `line` (the opening quote
    IS `line[0]`). Returns (content, remainder) if the quote closes on this
    line, else None. Double-quoted scalars honour `\\"` and `\\\\`; single
    quoted scalars honour `''` as a literal single quote, the two escaping
    rules this subset supports."""
    i = 1
    n = len(line)
    out = []
    while i < n:
        ch = line[i]
        if quote_char == '"':
            if ch == "\\" and i + 1 < n:
                nxt = line[i + 1]
                if nxt == '"':
                    out.append('"')
                elif nxt == "\\":
                    out.append("\\")
                elif nxt == "n":
                    out.append("\n")
                elif nxt == "t":
                    out.append("\t")
                else:
                    out.append("\\")
                    out.append(nxt)
                i += 2
                continue
            if ch == '"':
                return "".join(out), line[i + 1:]
            out.append(ch)
            i += 1
        else:
            if ch == "'" and i + 1 < n and line[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            if ch == "'":
                return "".join(out), line[i + 1:]
            out.append(ch)
            i += 1
    return None


class _Cursor:
    """A position in the raw line list. Kept as an object (not a plain int)
    so every parse function shares the one advancing pointer rather than
    threading an index through every call."""

    def __init__(self, lines):
        self.lines = lines
        self.i = 0
        self.n = len(lines)

    def skip_skippable(self):
        while self.i < self.n and _is_skippable(self.lines[self.i]):
            self.i += 1

    def peek(self):
        return self.lines[self.i] if self.i < self.n else None

    def peek_next_content(self):
        """The next non-blank, non-comment line without consuming it, or
        None at end of input."""
        j = self.i
        while j < self.n and _is_skippable(self.lines[j]):
            j += 1
        return self.lines[j] if j < self.n else None

    def advance(self):
        self.i += 1


def _parse_block_scalar(cur, header_indent, indicator):
    """A `>` folded or `|` literal block scalar. Content lines are every
    following line indented MORE than `header_indent`; the block ends at the
    first line indented at or below `header_indent` (or end of input).
    Folded (`>`) scalars join lines with a single space, a blank line
    becomes a newline; literal (`|`) scalars keep newlines as written. Both
    strip the single trailing newline a plain YAML block carries, matching
    every author's expectation of `>` and `|` without a chomping suffix; a
    `-` or `+` chomping indicator strips or keeps trailing blank lines
    respectively, which is the one refinement this subset supports beyond
    the bare indicators."""
    is_folded = indicator[0] == ">"
    strip_chomp = indicator.endswith("-")
    keep_chomp = indicator.endswith("+")
    content_lines = []
    content_indent = None
    while cur.i < cur.n:
        line = cur.lines[cur.i]
        if line.strip() == "":
            content_lines.append("")
            cur.advance()
            continue
        ind = _indent_of(line)
        if ind <= header_indent:
            break
        if content_indent is None:
            content_indent = ind
        content_lines.append(line[content_indent:] if len(line) >= content_indent else line.lstrip(" "))
        cur.advance()
    # Drop trailing blank lines gathered speculatively before a dedent.
    while content_lines and content_lines[-1] == "":
        content_lines.pop()
    if is_folded:
        parts = []
        blank_run = 0
        for idx, raw in enumerate(content_lines):
            if raw == "":
                blank_run += 1
                continue
            if parts and blank_run == 0:
                parts.append(" ")
            elif parts and blank_run > 0:
                parts.append("\n" * blank_run)
            parts.append(raw)
            blank_run = 0
        text = "".join(parts)
    else:
        text = "\n".join(content_lines)
    if keep_chomp:
        text += "\n"
    elif not strip_chomp and content_lines:
        text += "\n"
    return text


def _parse_scalar_continuation(cur, first_fragment, item_indent):
    """A plain or quoted scalar that may wrap across following lines, the
    shape every multi-line `acceptance[]` and `evidence[]` entry in the
    shipped ledger uses. `item_indent` is the column the scalar's own line
    started at (the dash column for list items); continuation lines must be
    indented strictly more than that and must not themselves look like a new
    list item or mapping key, or they belong to the next entry instead."""
    fragment = first_fragment.strip()
    if fragment[:1] in ("'", '"'):
        quote_char = fragment[0]
        scanned = _scan_quoted(fragment, quote_char)
        if scanned is not None:
            content, remainder = scanned
            if _strip_trailing_comment(remainder).strip():
                raise ProgramParseError(
                    "trailing content after closed quoted scalar: %r" % remainder)
            return content
        # Unterminated on this line: fold continuation lines (a single
        # space per join) until the closing quote appears.
        parts = [fragment[1:]]
        while cur.i < cur.n:
            line = cur.lines[cur.i]
            cur.advance()
            scanned = _scan_quoted_continuation(line, quote_char)
            if scanned is None:
                parts.append(line.strip())
                continue
            content, remainder = scanned
            parts.append(content)
            if _strip_trailing_comment(remainder).strip():
                raise ProgramParseError(
                    "trailing content after closed quoted scalar: %r" % remainder)
            return " ".join(p for p in parts if p != "")
        raise ProgramParseError("unterminated quoted scalar starting %r" % first_fragment)
    # Plain scalar: comment-strip the first line, then fold any deeper
    # indented continuation lines that are not themselves a new item.
    value = _strip_trailing_comment(fragment)
    parts = [value]
    while cur.i < cur.n:
        nxt = cur.lines[cur.i]
        if nxt.strip() == "":
            break
        ind = _indent_of(nxt)
        if ind <= item_indent:
            break
        stripped = nxt.strip()
        if stripped.startswith("- "):
            break
        if _PLAIN_KEY_RE.match(stripped):
            break
        parts.append(_strip_trailing_comment(stripped))
        cur.advance()
    return " ".join(p for p in parts if p != "")


def _scan_quoted_continuation(line, quote_char):
    """Like `_scan_quoted`, but for a continuation line of an already-open
    quoted scalar: the whole line is content until an unescaped closing
    quote, with no opening quote to skip."""
    i = 0
    n = len(line)
    out = []
    stripped_line = line.lstrip(" ")
    offset = len(line) - len(stripped_line)
    i = offset
    while i < n:
        ch = line[i]
        if quote_char == '"':
            if ch == "\\" and i + 1 < n:
                nxt = line[i + 1]
                if nxt == '"':
                    out.append('"')
                elif nxt == "\\":
                    out.append("\\")
                else:
                    out.append("\\")
                    out.append(nxt)
                i += 2
                continue
            if ch == '"':
                return "".join(out), line[i + 1:]
            out.append(ch)
            i += 1
        else:
            if ch == "'" and i + 1 < n and line[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            if ch == "'":
                return "".join(out), line[i + 1:]
            out.append(ch)
            i += 1
    return None


def _coerce_scalar(text):
    """A bare (unquoted) scalar's typed value: null, bool, int, or string.
    Quoted scalars never pass through here, so a quoted `"null"` stays the
    string `null`, exactly as YAML itself distinguishes the two. The only
    flow-collection syntax this subset accepts is the empty flow list `[]`,
    the one shape the shipped ledger uses for an empty `depends_on`,
    `evidence`, `alerts` or `risks`; any other flow list or flow mapping is
    outside the subset and is refused by the caller before it ever reaches
    here (see `_parse_scalar_continuation`, which never emits `[` or `{`
    text for anything but this exact empty case)."""
    if text == "[]":
        return []
    if text[:1] in ("[", "{") and text != "[]":
        raise ProgramParseError(
            "flow collections are outside the supported subset: %r" % text)
    if text == "" or text == "~" or text == "null" or text == "Null" or text == "NULL":
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if re.match(r"^-?[0-9]+$", text):
        try:
            return int(text)
        except ValueError:
            return text
    return text


def _parse_mapping(cur, indent):
    result = {}
    while True:
        cur.skip_skippable()
        line = cur.peek()
        if line is None:
            break
        ind = _indent_of(line)
        if ind < indent:
            break
        if ind > indent:
            raise ProgramParseError("unexpected indent at line %r" % line)
        content = line[indent:]
        if content.startswith("- "):
            break
        m = _PLAIN_KEY_RE.match(content.rstrip("\n"))
        if not m:
            raise ProgramParseError("expected a mapping key, found %r" % line)
        key, rest = m.group(1), (m.group(2) or "")
        cur.advance()
        rest_stripped = rest.strip()
        if rest_stripped == "":
            nxt = cur.peek_next_content()
            if nxt is None or _indent_of(nxt) <= indent:
                value = None
            else:
                child_indent = _indent_of(nxt)
                if nxt[child_indent:].startswith("- "):
                    value = _parse_list(cur, child_indent)
                else:
                    value = _parse_mapping(cur, child_indent)
        elif rest_stripped in _BLOCK_INDICATORS:
            value = _parse_block_scalar(cur, indent, rest_stripped)
        elif rest_stripped[:1] in ("'", '"'):
            value = _parse_scalar_continuation(cur, rest_stripped, indent)
        else:
            value = _coerce_scalar(
                _parse_scalar_continuation(cur, rest_stripped, indent))
        result[key] = value
    return result


def _parse_list(cur, indent):
    result = []
    while True:
        cur.skip_skippable()
        line = cur.peek()
        if line is None:
            break
        ind = _indent_of(line)
        if ind < indent:
            break
        if ind > indent:
            raise ProgramParseError("unexpected indent in list at line %r" % line)
        content = line[indent:]
        if not content.startswith("- "):
            break
        after_dash = content[2:]
        cur.advance()
        after_stripped = after_dash.strip()
        child_col = indent + 2
        if after_stripped == "":
            nxt = cur.peek_next_content()
            if nxt is None or _indent_of(nxt) <= indent:
                result.append(None)
                continue
            gi = _indent_of(nxt)
            if nxt[gi:].startswith("- "):
                result.append(_parse_list(cur, gi))
            else:
                result.append(_parse_mapping(cur, gi))
            continue
        if after_stripped in _BLOCK_INDICATORS:
            result.append(_parse_block_scalar(cur, indent, after_stripped))
            continue
        key_match = _PLAIN_KEY_RE.match(after_stripped)
        looks_quoted = after_stripped[:1] in ("'", '"')
        if key_match and not looks_quoted:
            item = {}
            key, rest = key_match.group(1), (key_match.group(2) or "")
            rest_stripped = rest.strip()
            if rest_stripped == "":
                nxt = cur.peek_next_content()
                if nxt is None or _indent_of(nxt) <= child_col:
                    item[key] = None
                else:
                    gi = _indent_of(nxt)
                    if nxt[gi:].startswith("- "):
                        item[key] = _parse_list(cur, gi)
                    else:
                        item[key] = _parse_mapping(cur, gi)
            elif rest_stripped in _BLOCK_INDICATORS:
                item[key] = _parse_block_scalar(cur, child_col, rest_stripped)
            elif rest_stripped[:1] in ("'", '"'):
                item[key] = _parse_scalar_continuation(cur, rest_stripped, child_col)
            else:
                item[key] = _coerce_scalar(
                    _parse_scalar_continuation(cur, rest_stripped, child_col))
            rest_of_item = _parse_mapping(cur, child_col)
            item.update(rest_of_item)
            result.append(item)
            continue
        if looks_quoted:
            result.append(_parse_scalar_continuation(cur, after_stripped, indent))
        else:
            result.append(_coerce_scalar(
                _parse_scalar_continuation(cur, after_stripped, indent)))
    return result


def _yaml_load(text, source_name):
    """Parse the documented YAML subset into plain dicts, lists, strings,
    ints, bools and None. Raises `ProgramParseError` (named with
    `source_name`) on anything outside that subset, rather than guessing."""
    raw_lines = text.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    lines = [ln.rstrip("\r") for ln in raw_lines]
    cur = _Cursor(lines)
    cur.skip_skippable()
    if cur.peek() is None:
        return {}
    try:
        top_indent = _indent_of(cur.peek())
        if top_indent != 0:
            raise ProgramParseError("top-level content must start at column 0")
        value = _parse_mapping(cur, 0)
        cur.skip_skippable()
        if cur.i < cur.n:
            raise ProgramParseError("trailing content after top-level mapping: %r" % cur.peek())
        return value
    except ProgramParseError as exc:
        raise ProgramParseError("%s: %s" % (source_name, exc))


def load_yaml_file(path):
    """Read and parse one YAML-subset file from disk. Raises
    `ProgramParseError` naming `path` on any I/O or parse failure, so every
    caller can decide file by file whether one bad file should hide its
    siblings."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise ProgramParseError("%s: could not read file (%s)" % (path, exc))
    return _yaml_load(text, path)


# ---------------------------------------------------------------------------
# Work-item normalization
# ---------------------------------------------------------------------------

def _normalize_status(raw, path):
    if not isinstance(raw, str) or not raw.strip():
        raise ProgramParseError("%s: missing or empty status" % path)
    canon = re.sub(r"[\s_]+", "_", raw.strip().lower())
    if canon not in STATUS_VALUES:
        raise ProgramParseError(
            "%s: unrecognized status %r (allowed: %s)" % (path, raw, ", ".join(STATUS_VALUES)))
    return canon


def _normalize_risks(raw, path):
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProgramParseError("%s: risks must be a list" % path)
    out = []
    for idx, entry in enumerate(raw):
        if isinstance(entry, str):
            out.append({"risk": entry, "mitigation": None, "severity": None})
            continue
        if isinstance(entry, dict):
            risk_text = entry.get("risk")
            if not isinstance(risk_text, str) or not risk_text.strip():
                raise ProgramParseError(
                    "%s: risks[%d] mapping is missing required 'risk'" % (path, idx))
            severity = entry.get("severity")
            if severity is not None:
                if not isinstance(severity, str) or severity.lower() not in _SEVERITY_VALUES:
                    raise ProgramParseError(
                        "%s: risks[%d] has unrecognized severity %r" % (path, idx, severity))
                severity = severity.lower()
            mitigation = entry.get("mitigation")
            out.append({"risk": risk_text, "mitigation": mitigation, "severity": severity})
            continue
        raise ProgramParseError("%s: risks[%d] is neither a string nor a mapping" % (path, idx))
    return out


def _as_str_list(raw, field, path):
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProgramParseError("%s: %s must be a list" % (path, field))
    for entry in raw:
        if not isinstance(entry, str):
            raise ProgramParseError("%s: %s entries must be strings" % (path, field))
    return list(raw)


def _normalize_percent(raw, path):
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ProgramParseError("%s: percent_complete must be an integer" % path)
    if raw < 0 or raw > 100:
        raise ProgramParseError("%s: percent_complete out of range 0..100: %r" % (path, raw))
    return raw


def _validate_acceptance_met(acceptance, acceptance_met, path):
    """An `acceptance_met` entry that names nothing real must never be
    silently dropped into a fabricated percentage. Only checked when
    `acceptance` is non-empty and `acceptance_met` is recorded, the exact
    condition under which `_progress_for` derives a percentage from them;
    outside that condition the entries drive no computation, so validating
    them would refuse data this module never reads."""
    if not acceptance or acceptance_met is None:
        return
    total = len(acceptance)
    for entry in acceptance_met:
        if isinstance(entry, bool):
            raise ProgramParseError(
                "%s: acceptance_met entry %r is neither an acceptance index nor "
                "an acceptance string" % (path, entry))
        if isinstance(entry, int):
            if not (0 <= entry < total):
                raise ProgramParseError(
                    "%s: acceptance_met entry %r is out of range for %d "
                    "recorded acceptance items" % (path, entry, total))
            continue
        if isinstance(entry, str):
            if entry not in acceptance:
                raise ProgramParseError(
                    "%s: acceptance_met entry %r does not match any recorded "
                    "acceptance item" % (path, entry))
            continue
        raise ProgramParseError(
            "%s: acceptance_met entry %r is neither an acceptance index nor "
            "an acceptance string" % (path, entry))


def _normalize_work_item(raw, path):
    if not isinstance(raw, dict):
        raise ProgramParseError("%s: work item must be a mapping" % path)
    item_id = raw.get("id")
    title = raw.get("title")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ProgramParseError("%s: missing required 'id'" % path)
    if not isinstance(title, str) or not title.strip():
        raise ProgramParseError("%s: missing required 'title'" % path)
    status = _normalize_status(raw.get("status"), path)
    acceptance = _as_str_list(raw.get("acceptance"), "acceptance", path)
    acceptance_met_raw = raw.get("acceptance_met")
    acceptance_met = None
    if acceptance_met_raw is not None:
        if not isinstance(acceptance_met_raw, list):
            raise ProgramParseError("%s: acceptance_met must be a list" % path)
        acceptance_met = list(acceptance_met_raw)
    _validate_acceptance_met(acceptance, acceptance_met, path)
    percent_complete = _normalize_percent(raw.get("percent_complete"), path)
    risks = _normalize_risks(raw.get("risks"), path)
    blocked_by = _as_str_list(raw.get("blocked_by"), "blocked_by", path)
    depends_on = _as_str_list(raw.get("depends_on"), "depends_on", path)
    evidence = _as_str_list(raw.get("evidence"), "evidence", path)
    alerts = _as_str_list(raw.get("alerts"), "alerts", path)

    return {
        "id": item_id,
        "title": title,
        "why": raw.get("why"),
        "owner": raw.get("owner"),
        "reviewer": raw.get("reviewer"),
        "status": status,
        "wave": raw.get("wave"),
        "depends_on": depends_on,
        "spec": raw.get("spec"),
        "acceptance": acceptance,
        "acceptance_met": acceptance_met,
        "estimated_days": raw.get("estimated_days"),
        "token_budget": raw.get("token_budget"),
        "tokens_used": raw.get("tokens_used"),
        "started_at": raw.get("started_at"),
        "completed_at": raw.get("completed_at"),
        "evidence": evidence,
        "risks": risks,
        "blocked_by": blocked_by,
        "alerts": alerts,
        "percent_complete": percent_complete,
        "_path": path,
    }


def _progress_for(item):
    """The one place progress is computed. Returns
    (percent_or_None, source_label). `source_label` is always one of
    `declared`, `derived from acceptance`, or `not measured`; a status word
    alone never produces a percentage here."""
    if item["percent_complete"] is not None:
        return item["percent_complete"], "declared"
    acceptance = item["acceptance"]
    met = item["acceptance_met"]
    if acceptance and met is not None:
        total = len(acceptance)
        matched = 0
        for entry in met:
            if isinstance(entry, int) and 0 <= entry < total:
                matched += 1
            elif isinstance(entry, str) and entry in acceptance:
                matched += 1
        if total > 0:
            pct = int(round((matched / float(total)) * 100))
            return pct, "derived from acceptance"
    return None, "not measured"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

#: The marker every NO-DATA reason string carries, so a caller can recognize
#: "the source itself is absent" without inspecting which module raised it.
#: An absent source is never read as a clean, empty result: see
#: `build_program_report` (which surfaces it in `parseErrors`) and
#: `check_status_md` (which refuses to confirm freshness while one is
#: present).
NO_DATA_MARKER = "NO-DATA"


class ProgramData(object):
    """The whole program ledger as loaded: the program record, every work
    item that parsed cleanly, every per-item parse failure named by path and
    reason, and every source-level failure (a source that is entirely
    absent, as opposed to one bad item within it). A malformed item never
    hides its siblings; it only excludes itself and is recorded in
    `parse_errors`. A missing source excludes nothing (there was nothing to
    exclude) and is recorded in `source_errors` instead, so it is never
    mistaken for an item that failed to parse."""

    def __init__(self, root, program, items, parse_errors, source_errors):
        self.root = root
        self.program = program
        self.items = items
        self.parse_errors = parse_errors
        self.source_errors = source_errors


def load_program(root):
    """Load `program/PROGRAM.yaml` and every `program/work-items/*.yaml`
    file under `root`. The program record itself must parse: a broken
    `PROGRAM.yaml` raises `ProgramParseError`, because without it there is no
    program to report on. A broken or invalid individual work item does NOT
    raise: it is recorded in `ProgramData.parse_errors` by path and reason,
    and every other item still loads.

    The work-items directory itself is a different failure class. An absent
    directory is NO-DATA, not an empty program: `program/work-items` missing
    entirely means nobody can say whether the program has zero items or the
    reporter simply never saw them. That is recorded in
    `ProgramData.source_errors`, never treated as "zero items, no errors"."""
    program_path = os.path.join(root, PROGRAM_REL)
    if not os.path.isfile(program_path):
        raise ProgramParseError("%s: PROGRAM.yaml is missing" % program_path)
    program = load_yaml_file(program_path)
    if not isinstance(program, dict):
        raise ProgramParseError("%s: must parse to a mapping" % program_path)

    items_dir = os.path.join(root, WORK_ITEMS_REL)
    items = []
    parse_errors = []
    source_errors = []
    if os.path.isdir(items_dir):
        # os.path.isdir is True for a directory this process cannot READ, so the
        # listing is a boundary call like any other and gets an explicit failure
        # path. Round 2 reached the same NO-DATA class through this door: an
        # unreadable directory raised PermissionError straight out of the
        # founder-facing gate instead of stating a reason.
        names = None
        try:
            names = sorted(os.listdir(items_dir))
        except OSError as exc:
            source_errors.append({
                "path": WORK_ITEMS_REL,
                "reason": "%s: work-items directory cannot be read (%s): NO-DATA, "
                          "not an empty program" % (NO_DATA_MARKER,
                                                    exc.strerror or exc.__class__.__name__),
            })
        for name in (names or []):
            if not name.endswith(".yaml"):
                continue
            path = os.path.join(items_dir, name)
            rel = os.path.relpath(path, root)
            try:
                raw = load_yaml_file(path)
                item = _normalize_work_item(raw, rel)
            except ProgramParseError as exc:
                parse_errors.append({"path": rel, "reason": str(exc)})
                continue
            items.append(item)
    elif os.path.exists(items_dir):
        # Refused correctly before this branch existed, but described as absent,
        # which is a different fact from "present and the wrong kind of thing".
        source_errors.append({
            "path": WORK_ITEMS_REL,
            "reason": "%s: work-items exists but is not a directory: NO-DATA, not "
                      "an empty program" % NO_DATA_MARKER,
        })
    else:
        source_errors.append({
            "path": WORK_ITEMS_REL,
            "reason": "%s: work-items directory is absent: NO-DATA, not an "
                      "empty program" % NO_DATA_MARKER,
        })
    return ProgramData(root=root, program=program, items=items,
                        parse_errors=parse_errors, source_errors=source_errors)


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def _status_counts(items):
    counts = {}
    for value in STATUS_VALUES:
        counts[value] = 0
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def _aggregate(items):
    measured = []
    for item in items:
        pct, source = _progress_for(item)
        if pct is not None:
            measured.append(pct)
    total = len(items)
    measured_count = len(measured)
    aggregate_percent = None
    if measured_count > 0:
        aggregate_percent = int(round(sum(measured) / float(measured_count)))
    return measured_count, total, aggregate_percent


def _spec_docs(program, items, root):
    seen = []
    paths = []
    plan = program.get("plan")
    if isinstance(plan, str) and plan.strip():
        paths.append(("program/%s" % plan) if not os.path.isabs(plan) else plan)
    for item in items:
        spec = item.get("spec")
        if isinstance(spec, str) and spec.strip() and spec not in seen:
            seen.append(spec)
            paths.append(spec)
    docs = []
    for p in paths:
        if p in [d["path"] for d in docs]:
            continue
        full = os.path.join(root, p)
        docs.append({"path": p, "exists": os.path.isfile(full)})
    return docs


def _budget(program, items):
    token_budget = program.get("token_budget")
    declared = None
    if isinstance(token_budget, dict):
        declared = token_budget.get("planned_total")
    used_recorded = 0
    not_recorded_count = 0
    for item in items:
        tokens_used = item.get("tokens_used")
        if isinstance(tokens_used, int) and not isinstance(tokens_used, bool):
            used_recorded += tokens_used
        else:
            not_recorded_count += 1
    return {
        "declared": declared,
        "used_recorded": used_recorded,
        "not_recorded_count": not_recorded_count,
    }


def _blockers(items):
    ids = set(item["id"] for item in items)
    done_ids = set(item["id"] for item in items if item["status"] == "done")
    blockers = []
    for item in items:
        for dep in item["depends_on"]:
            if dep not in done_ids:
                reason = "not recorded as done" if dep in ids else "unknown work item"
                blockers.append({
                    "item": item["id"],
                    "blocked_on": dep,
                    "kind": "depends_on",
                    "reason": reason,
                })
        for entry in item["blocked_by"]:
            blockers.append({
                "item": item["id"],
                "blocked_on": entry,
                "kind": "blocked_by",
                "reason": "free-text blocker",
            })
    return blockers


def _risks(items):
    out = []
    for item in items:
        for risk in item["risks"]:
            out.append({
                "item": item["id"],
                "risk": risk["risk"],
                "severity": risk["severity"],
                "mitigation": risk["mitigation"] if risk["mitigation"] else "no mitigation recorded",
            })
    return out


def _declared_groupings(program):
    """The groupings the program record itself declares: every entry across
    `waves`, `plan_waves` and `delivery_groupings`, in that order, each
    contributing its `id` and recorded `name`. An id declared more than once
    keeps the name from its first appearance. This is the one source of
    truth for which grouping ids are known; an item naming any other value
    in its `wave` field is undeclared, never silently accepted (see
    `_wave_sections`)."""
    groupings = []
    seen = set()
    for key in ("waves", "plan_waves", "delivery_groupings"):
        raw = program.get(key)
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            gid = entry.get("id")
            if not isinstance(gid, str) or not gid.strip() or gid in seen:
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                name = gid
            seen.add(gid)
            groupings.append({"id": gid, "name": name})
    return groupings


def _wave_sections(items, declared_groupings):
    """Bucket `items` into gantt/report sections. Returns
    `(order, by_section, undeclared_waves)`:

    - `order`: section keys in render order, declared groupings first (in
      their declared order, only when at least one item names them), then
      undeclared wave values (sorted, for determinism), then `unassigned`
      last (items with no recorded wave at all).
    - `by_section`: section key -> list of items.
    - `undeclared_waves`: sorted list of `{wave, itemIds}`, one entry per
      distinct `wave` value that names no declared grouping id. An item
      with NO wave recorded is not "undeclared": it has no wave value to
      name a section after, so it renders under `unassigned` instead and is
      never reported as an undeclared wave."""
    declared_ids = set(g["id"] for g in declared_groupings)
    declared_names = dict((g["id"], g["name"]) for g in declared_groupings)
    by_section = {}
    undeclared_items = {}
    for item in items:
        wave = item["wave"]
        if isinstance(wave, str) and wave.strip():
            key = wave if wave in declared_ids else ("undeclared:%s" % wave)
            if key.startswith("undeclared:"):
                undeclared_items.setdefault(wave, []).append(item["id"])
        else:
            key = "unassigned"
        by_section.setdefault(key, []).append(item)

    order = []
    for g in declared_groupings:
        if g["id"] in by_section:
            order.append(g["id"])
    for wave in sorted(undeclared_items):
        order.append("undeclared:%s" % wave)
    if "unassigned" in by_section:
        order.append("unassigned")

    section_names = dict(declared_names)
    for wave in undeclared_items:
        section_names["undeclared:%s" % wave] = wave
    section_names["unassigned"] = "unassigned"

    undeclared_waves = [
        {"wave": wave, "itemIds": sorted(undeclared_items[wave])}
        for wave in sorted(undeclared_items)
    ]
    return order, by_section, section_names, undeclared_waves


def build_program_report(root):
    """The JSON envelope: `schemaVersion`, `program`, `milestones`, `waves`,
    `items`, `summary`, `risks`, `blockers`, `docs`, `budget`,
    `parseErrors`, `undeclaredWaves`. Deterministic key order is not
    meaningful for a Python dict (callers that need stable serialization
    pass `sort_keys=True` to `json.dumps`); what is deterministic here is
    that identical ledger input always produces an identical dict, with no
    timestamps or randomness of this module's own invention."""
    data = load_program(root)
    program = data.program
    items = data.items
    # `excluded_count` in the summary must count only items that failed to
    # load (data.parse_errors), never a dependency-naming error on an item
    # that DID load and IS included in every aggregate, and never a
    # source-level NO-DATA error (which excludes no item, because there was
    # no item to exclude). All three land in `parseErrors` so nothing is
    # silent, but only the first kind shrinks the item count.
    item_parse_errors = list(data.parse_errors)
    dependency_errors = []

    report_items = []
    ids = set(item["id"] for item in items)
    for item in items:
        pct, source = _progress_for(item)
        for dep in item["depends_on"]:
            if dep not in ids:
                dependency_errors.append({
                    "path": item["_path"],
                    "reason": "%s: depends_on names unknown work item %r" % (item["id"], dep),
                })
        report_items.append({
            "id": item["id"],
            "title": item["title"],
            "why": item["why"],
            "owner": item["owner"],
            "reviewer": item["reviewer"],
            "status": item["status"],
            "wave": item["wave"],
            "depends_on": item["depends_on"],
            "blocked_by": item["blocked_by"],
            "spec": item["spec"],
            "acceptance": item["acceptance"],
            "acceptance_met": item["acceptance_met"],
            "estimated_days": item["estimated_days"],
            "token_budget": item["token_budget"],
            "tokens_used": item["tokens_used"],
            "started_at": item["started_at"],
            "completed_at": item["completed_at"],
            "evidence": item["evidence"],
            "risks": item["risks"],
            "alerts": item["alerts"],
            "progress_percent": pct,
            "progress_source": source,
        })

    measured_count, total_count, aggregate_percent = _aggregate(items)
    counts = _status_counts(items)

    milestones = program.get("milestones")
    if not isinstance(milestones, list):
        milestones = []
    waves = program.get("waves")
    if not isinstance(waves, list):
        waves = []

    declared_groupings = _declared_groupings(program)
    section_order, sections_by_key, section_names, undeclared_waves = \
        _wave_sections(items, declared_groupings)

    # Tagged by `kind` so a consumer (the board, specifically) can single out
    # "a work-item file could not be read or was missing a required field"
    # from a dependency-naming error on an item that DID load, and from a
    # whole-source NO-DATA error. The tag changes nothing about the existing
    # `path`/`reason` shape every caller already reads; it only adds a way
    # to filter without inventing a second vocabulary for the same facts.
    def _tag_errors(entries, kind):
        tagged = []
        for entry in entries:
            tagged_entry = dict(entry)
            tagged_entry["kind"] = kind
            tagged.append(tagged_entry)
        return tagged

    all_parse_errors = (_tag_errors(item_parse_errors, "item")
                         + _tag_errors(dependency_errors, "dependency")
                         + _tag_errors(data.source_errors, "source"))

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "program": {
            "program": program.get("program"),
            "objective": program.get("objective"),
            "version_target": program.get("version_target"),
            "product_owner": program.get("product_owner"),
            "status": program.get("status"),
            "plan": program.get("plan"),
        },
        "milestones": milestones,
        "waves": waves,
        "items": report_items,
        "summary": {
            "counts": counts,
            "measured_count": measured_count,
            "total_count": total_count,
            "excluded_count": len(item_parse_errors),
            "aggregate_percent": aggregate_percent,
        },
        "risks": _risks(items),
        "blockers": _blockers(items),
        "docs": _spec_docs(program, items, root),
        "budget": _budget(program, items),
        "parseErrors": all_parse_errors,
        "undeclaredWaves": undeclared_waves,
        # Internal-only, consumed by `render_gantt` so it never has to
        # recompute the declared-grouping bucketing itself; not part of the
        # documented envelope (mirrors the item-level `_path` convention).
        "_sectionOrder": section_order,
        "_sectionsByKey": sections_by_key,
        "_sectionNames": section_names,
    }
    return report


# ---------------------------------------------------------------------------
# Rendering: the gantt
# ---------------------------------------------------------------------------

def _gantt_id(raw_id):
    return re.sub(r"[^A-Za-z0-9_\-]", "_", raw_id)


def _gantt_task_name(title):
    """Mermaid splits a gantt task line on its first colon (name : meta).
    A work-item title containing a colon must never be handed to mermaid
    raw, or the colon is misread as that split and the rendered name and
    metadata both come out wrong."""
    return title.replace(":", " ")


def render_gantt(report):
    """A fenced mermaid `gantt` block, deterministic for identical input.
    Sections come from the groupings the program record itself declares
    (`waves`, `plan_waves`, `delivery_groupings` in PROGRAM.yaml), rendered
    in that declared order under each grouping's recorded name; an item
    whose `wave` names no declared grouping still renders (never dropped),
    in a section named by its own wave value, placed after every declared
    section (see `_wave_sections`, which also produces the report's
    `undeclaredWaves` field so a mistyped or stale wave id is never
    invisible). `done` items get the `done` tag, `in_progress` and
    `partially_done` get `active`, items with any open blocker get `crit`,
    everything else is untagged. Durations come from `estimated_days` when
    recorded, else `1d`. Bars show sequence and recorded estimates, never
    calendar promises: that sentence is baked into the block itself as a
    comment so a reader of the raw text sees it too."""
    items = report["items"]
    blocked_ids = set(b["item"] for b in report["blockers"])
    section_order = report.get("_sectionOrder")
    sections_by_key = report.get("_sectionsByKey")
    section_names = report.get("_sectionNames")
    if section_order is None or sections_by_key is None or section_names is None:
        section_order, sections_by_key, section_names, _ = _wave_sections(items, [])

    lines = []
    lines.append("```mermaid")
    lines.append("gantt")
    lines.append("    title %s" % (report["program"].get("program") or "program"))
    lines.append("    dateFormat  YYYY-MM-DD")
    lines.append("    %% Bars show sequence and recorded estimates, never calendar promises.")
    ids_present = set(item["id"] for item in items)
    for key in section_order:
        lines.append("    section %s" % section_names.get(key, key))
        for item in sections_by_key[key]:
            tag = ""
            if item["status"] == "done":
                tag = "done, "
            elif item["status"] in ("in_progress", "partially_done"):
                tag = "active, "
            elif item["id"] in blocked_ids or item["status"] == "blocked":
                tag = "crit, "
            gid = _gantt_id(item["id"])
            duration = "%sd" % item["estimated_days"] if isinstance(item["estimated_days"], int) else "1d"
            deps = [d for d in item["depends_on"] if d in ids_present]
            after = ""
            if deps:
                after = "after %s, " % " ".join(_gantt_id(d) for d in deps)
            lines.append("    %s :%s%s, %s%s" % (
                _gantt_task_name(item["title"]), tag, gid, after, duration))
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rendering: STATUS.md
# ---------------------------------------------------------------------------

def _fmt_progress(pct, source):
    if source == "not measured":
        return "not measured"
    return "%d%% (%s)" % (pct, source)


def _md_table_cell(text):
    """Normalize any value bound for a markdown table cell: a `|` inside
    recorded text (risk, mitigation, severity, or item id) must never widen
    the table with an extra column, and an embedded newline must never break
    the row onto a new line."""
    return str(text).replace("\n", " ").replace("|", "\\|")


def render_status_md(report):
    """The generated block only: everything between (but not including) the
    BEGIN/END markers, so a caller can splice it into `program/STATUS.md`
    without disturbing hand-written prose outside those markers."""
    lines = []
    summary = report["summary"]
    program = report["program"]

    # 1. Headline
    lines.append("## Program status")
    lines.append("")
    name = program.get("program") or "(unnamed program)"
    version_target = program.get("version_target") or "(no version target recorded)"
    lines.append("Program: %s" % name)
    lines.append("Version target: %s" % version_target)
    if summary["aggregate_percent"] is not None:
        lines.append("Overall position: %d%% across measured items." % summary["aggregate_percent"])
    else:
        lines.append("Overall position: not measured (no item records a declared or derived progress figure).")
    lines.append("%d of %d items measured." % (summary["measured_count"], summary["total_count"]))
    no_data_errors = [pe for pe in report["parseErrors"] if NO_DATA_MARKER in pe.get("reason", "")]
    for pe in no_data_errors:
        lines.append("NO-DATA: %s (%s)" % (pe["path"], pe["reason"]))
    for uw in report["undeclaredWaves"]:
        lines.append("Undeclared wave %r: %s" % (uw["wave"], ", ".join(uw["itemIds"])))
    lines.append("")

    # 2. Gantt
    lines.append(render_gantt(report))
    lines.append("")

    items = report["items"]

    # 3. Finished
    lines.append("### Finished")
    done = [i for i in items if i["status"] == "done"]
    if not done:
        lines.append("none recorded")
    else:
        for i in done:
            when = i["completed_at"] or "completion date not recorded"
            lines.append("- %s: %s (%s)" % (i["id"], i["title"], when))
    lines.append("")

    # 4. In flight
    lines.append("### In flight")
    flight = [i for i in items if i["status"] in ("in_progress", "partially_done", "ready")]
    if not flight:
        lines.append("none recorded")
    else:
        for i in flight:
            owner = i["owner"] or "owner not recorded"
            pct, source = i["progress_percent"], i["progress_source"]
            lines.append("- %s: %s (owner: %s, progress: %s)" % (
                i["id"], i["title"], owner, _fmt_progress(pct, source)))
    lines.append("")

    # 5. Still to do
    lines.append("### Still to do")
    todo = [i for i in items if i["status"] == "not_started"]
    if not todo:
        lines.append("none recorded")
    else:
        for i in todo:
            waits = ", ".join(i["depends_on"]) if i["depends_on"] else "nothing recorded"
            lines.append("- %s: %s (waits on: %s)" % (i["id"], i["title"], waits))
    lines.append("")

    # 6. Blocked
    lines.append("### Blocked")
    blockers = report["blockers"]
    if not blockers:
        lines.append("none recorded")
    else:
        for b in blockers:
            lines.append("- %s is blocked on %s (%s: %s)" % (
                b["item"], b["blocked_on"], b["kind"], b["reason"]))
    lines.append("")

    # 7. Risks and mitigations
    lines.append("### Risks and mitigations")
    risks = report["risks"]
    if not risks:
        lines.append("none recorded")
    else:
        lines.append("| item | risk | severity | mitigation |")
        lines.append("| --- | --- | --- | --- |")
        for r in risks:
            severity = r["severity"] or "not recorded"
            mitigation = r["mitigation"].strip() if isinstance(r["mitigation"], str) else r["mitigation"]
            item_cell = _md_table_cell(r["item"])
            risk_cell = _md_table_cell(r["risk"].strip())
            severity_cell = _md_table_cell(severity)
            mitigation_cell = _md_table_cell(mitigation)
            lines.append("| %s | %s | %s | %s |" % (item_cell, risk_cell, severity_cell, mitigation_cell))
    lines.append("")

    # 8. Documentation
    lines.append("### Documentation")
    docs = report["docs"]
    if not docs:
        lines.append("none recorded")
    else:
        for d in docs:
            lines.append("- %s: %s" % (d["path"], "exists" if d["exists"] else "MISSING"))
    lines.append("")

    # 9. Budget
    lines.append("### Budget")
    budget = report["budget"]
    declared = budget["declared"] if budget["declared"] is not None else "not recorded"
    lines.append("Declared total: %s" % declared)
    lines.append("Recorded usage: %s" % budget["used_recorded"])
    lines.append("Items with no recorded usage: %d" % budget["not_recorded_count"])
    lines.append("")

    # 10. Parse errors
    parse_errors = report["parseErrors"]
    if parse_errors:
        lines.append("### Parse errors")
        for pe in parse_errors:
            lines.append("- %s: %s" % (pe["path"], pe["reason"]))
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def apply_generated_block(existing_text, generated_block):
    """Splice `generated_block` between the BEGIN/END markers in
    `existing_text`, leaving every byte outside the markers untouched. When
    neither marker is present, the block is appended at the end of the file
    (preceded by the markers) so a first write always succeeds; when the
    file is empty or absent (`existing_text` is None), the markers and block
    are the whole file.

    A file whose markers are DISORDERED (an END before a BEGIN) is refused by
    name rather than appended to. Appending to it never converged: `find`
    returns the FIRST occurrence of each marker, so a stale out-of-order END
    kept preceding the first BEGIN and every regeneration appended one more
    whole block, measured at 839, 1596, 2353, 3110 and 3867 bytes over five
    writes. Guessing which pair of markers a corrupted file meant would risk
    deleting the prose between them, and this module never guesses at content
    it did not generate. The one honest move is to stop and say so.

    DUPLICATED markers are deliberately NOT refused. A first version of this
    guard refused them too and broke the self-healing path: a file carrying a
    truncated BEGIN with no END legitimately gains a second BEGIN when the
    first write appends a fresh block, and the write after that splices from
    the first BEGIN to the first END and absorbs the stranded text. Duplicates
    reach a fixed point rather than growing, so refusing them fixed nothing and
    broke recovery. The measured defect was the disorder, and only the measured
    defect is refused."""
    body = "%s\n%s%s\n" % (BEGIN_MARKER, generated_block, END_MARKER)
    if existing_text is None:
        return body
    begin_idx = existing_text.find(BEGIN_MARKER)
    end_idx = existing_text.find(END_MARKER)
    if begin_idx != -1 and end_idx != -1 and end_idx < begin_idx:
        raise ProgramParseError(
            "the generated block's markers are out of order (END appears before BEGIN); "
            "appending to such a file never converges, so it is refused rather than grown "
            "on every regeneration. Repair or remove the marker pair by hand")
    if begin_idx == -1 or end_idx == -1:
        sep = "" if existing_text.endswith("\n") or existing_text == "" else "\n"
        return existing_text + sep + body
    before = existing_text[:begin_idx]
    after = existing_text[end_idx + len(END_MARKER):]
    # `body` already ends in exactly one newline after END_MARKER, and a previous
    # write left that same newline at the head of `after`. Re-attaching both grew
    # the file by one blank line on every regeneration, unbounded, and the gate
    # could not see it because it compares only the text BETWEEN the markers.
    # Dropping exactly one leading newline makes splice a fixed point while
    # leaving deliberate blank lines in the prose below it untouched.
    if after.startswith("\n"):
        after = after[1:]
    return before + body + after


def write_status_md(root, report=None):
    """Regenerate `program/STATUS.md` between the markers, preserving any
    hand-written prose outside them. Returns the full file text written."""
    if report is None:
        report = build_program_report(root)
    block = render_status_md(report)
    path = os.path.join(root, STATUS_MD_REL)
    existing = None
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    new_text = apply_generated_block(existing, block)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return new_text


def check_status_md(root):
    """True when `program/STATUS.md` already matches a fresh render (the
    generated block only; prose outside the markers is not compared because
    this module never owns it) AND the ledger it was rendered from carries
    no NO-DATA source error. False when the file is missing, has no
    markers, its generated block differs from a fresh render, OR a required
    source (for example `program/work-items`) is absent: a clean-looking
    match against an unmeasurable ledger is exactly the false-positive this
    check exists to prevent, so it refuses to confirm freshness while one is
    present rather than reporting True on a program nobody could see."""
    path = os.path.join(root, STATUS_MD_REL)
    if not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8") as fh:
        existing = fh.read()
    begin_idx = existing.find(BEGIN_MARKER)
    end_idx = existing.find(END_MARKER)
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        return False
    current_block = existing[begin_idx + len(BEGIN_MARKER) + 1:end_idx]
    report = build_program_report(root)
    if any(NO_DATA_MARKER in pe.get("reason", "") for pe in report["parseErrors"]):
        return False
    fresh_block = render_status_md(report)
    return current_block == fresh_block


# ---------------------------------------------------------------------------
# Rendering: the board (one self-contained HTML file)
# ---------------------------------------------------------------------------

#: Every file this module writes as a board carries this exact comment, so a
#: later write can tell "a file this tool generated, safe to regenerate"
#: from "a file this tool has never seen", and refuse the second case rather
#: than overwrite content it did not write. Never change this string once
#: shipped: a changed marker makes every previously written board unrecognized
#: and therefore unwritable-over.
BOARD_MARKER = "<!-- brothersbe-program-board:v1 -->"

_NO_DATA = "NO-DATA"


def _html_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _acceptance_met_indices(acceptance, acceptance_met):
    """The set of `acceptance` indices `acceptance_met` marks as met, using
    the identical matching rule `_progress_for` uses (an int index in range,
    or a string equal to the criterion's own text) so the board's ticks and
    the status line's percentage can never disagree about which criteria
    count as met. Returns `None`, never a guess, when `acceptance_met` was
    never recorded at all: that is a different fact from "recorded as
    matching nothing", and the board renders the two differently."""
    if acceptance_met is None:
        return None
    total = len(acceptance)
    matched = set()
    for entry in acceptance_met:
        if isinstance(entry, int) and 0 <= entry < total:
            matched.add(entry)
        elif isinstance(entry, str) and entry in acceptance:
            matched.add(acceptance.index(entry))
    return matched


def _board_state_label(status):
    return status.replace("_", " ") if isinstance(status, str) and status.strip() else _NO_DATA


def _render_board_subitems(item):
    """One `<ul>` of the item's acceptance criteria, a tick per index
    `acceptance_met` names, an empty box for one it does not, and `NO-DATA`
    for the whole list when either `acceptance` is empty or `acceptance_met`
    was never recorded: both are read as "cannot say", never as zero met."""
    acceptance = item["acceptance"]
    if not acceptance:
        return '<p class="board-nodata">%s (no acceptance criteria recorded)</p>' % _NO_DATA
    matched = _acceptance_met_indices(acceptance, item["acceptance_met"])
    rows = []
    for i, criterion in enumerate(acceptance):
        if matched is None:
            mark = '<span class="board-nodata">%s</span>' % _NO_DATA
        elif i in matched:
            mark = '<span class="board-tick" aria-hidden="true">&#10003;</span>'
        else:
            mark = '<span class="board-untick" aria-hidden="true">&#9744;</span>'
        rows.append('<li>%s <span class="board-subitem-text">%s</span></li>' % (
            mark, _html_escape(criterion)))
    return "<ul class=\"board-subitems\">%s</ul>" % "".join(rows)


def _render_board_evidence(item):
    evidence = item["evidence"]
    if not evidence:
        return '<p class="board-nodata">%s</p>' % _NO_DATA
    return "<ul class=\"board-evidence\">%s</ul>" % "".join(
        "<li>%s</li>" % _html_escape(e) for e in evidence)


_BOARD_CSS = """
:root {
  --board-bg: #f7f7f8;
  --board-fg: #1a1a1e;
  --board-muted: #5a5a63;
  --board-card-bg: #ffffff;
  --board-border: #d8d8dd;
  --board-accent: #2b6cb0;
  --board-done: #1a7f37;
  --board-progress: #9a6700;
  --board-blocked: #b42318;
  --board-nodata: #8a8a92;
  --board-tick: #1a7f37;
}
@media (prefers-color-scheme: dark) {
  :root {
    --board-bg: #16161a;
    --board-fg: #e8e8ec;
    --board-muted: #a3a3ad;
    --board-card-bg: #202024;
    --board-border: #37373f;
    --board-accent: #6fa8dc;
    --board-done: #4caf50;
    --board-progress: #d9a441;
    --board-blocked: #e5787a;
    --board-nodata: #7d7d86;
    --board-tick: #4caf50;
  }
}
:root[data-theme="dark"] {
  --board-bg: #16161a;
  --board-fg: #e8e8ec;
  --board-muted: #a3a3ad;
  --board-card-bg: #202024;
  --board-border: #37373f;
  --board-accent: #6fa8dc;
  --board-done: #4caf50;
  --board-progress: #d9a441;
  --board-blocked: #e5787a;
  --board-nodata: #7d7d86;
  --board-tick: #4caf50;
}
:root[data-theme="light"] {
  --board-bg: #f7f7f8;
  --board-fg: #1a1a1e;
  --board-muted: #5a5a63;
  --board-card-bg: #ffffff;
  --board-border: #d8d8dd;
  --board-accent: #2b6cb0;
  --board-done: #1a7f37;
  --board-progress: #9a6700;
  --board-blocked: #b42318;
  --board-nodata: #8a8a92;
  --board-tick: #1a7f37;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px;
  background: var(--board-bg);
  color: var(--board-fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.45;
}
.board-header {
  border-bottom: 1px solid var(--board-border);
  padding-bottom: 16px;
  margin-bottom: 20px;
}
.board-header h1 { margin: 0 0 6px 0; font-size: 1.5rem; }
.board-header .board-sub { color: var(--board-muted); font-size: 0.95rem; }
.board-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
.board-count {
  background: var(--board-card-bg);
  border: 1px solid var(--board-border);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 0.9rem;
}
.board-count b { display: block; font-size: 1.2rem; }
.board-loop {
  background: var(--board-card-bg);
  border: 1px solid var(--board-border);
  border-radius: 8px;
  padding: 16px 18px;
  margin-bottom: 14px;
}
.board-loop h2 { margin: 0 0 8px 0; font-size: 1.1rem; }
.board-item { margin-bottom: 14px; }
.board-item:last-child { margin-bottom: 0; }
.board-item-title { font-weight: 600; }
.board-item-id { color: var(--board-muted); font-weight: 400; margin-right: 6px; }
.board-state {
  display: inline-block;
  border-radius: 4px;
  padding: 1px 8px;
  font-size: 0.8rem;
  margin-left: 8px;
  border: 1px solid var(--board-border);
}
.board-state-done { color: var(--board-done); border-color: var(--board-done); }
.board-state-in_progress, .board-state-ready { color: var(--board-accent); border-color: var(--board-accent); }
.board-state-partially_done { color: var(--board-progress); border-color: var(--board-progress); }
.board-state-blocked { color: var(--board-blocked); border-color: var(--board-blocked); }
.board-subitems, .board-evidence { margin: 6px 0 0 0; padding-left: 0; list-style: none; }
.board-subitems li { margin: 3px 0; }
.board-evidence { padding-left: 20px; list-style: disc; color: var(--board-muted); }
.board-evidence li { margin: 3px 0; }
.board-subitem-text { color: var(--board-fg); }
.board-tick { color: var(--board-tick); font-weight: 700; margin-right: 4px; }
.board-untick { color: var(--board-muted); margin-right: 4px; }
.board-nodata { color: var(--board-nodata); font-style: italic; }
.board-evidence-label { color: var(--board-muted); font-size: 0.85rem; margin: 6px 0 2px 0; }
"""


def render_board_html(report):
    """One self-contained HTML file rendering `report`, the same program
    report `build_program_report` already produces: no field on it is
    recomputed here, only formatted. Per loop (the record's own declared
    wave/plan_wave/delivery_grouping sections, identical to `render_gantt`'s
    sections so the board and the gantt never disagree about grouping): the
    section name, and per item its title, its recorded `status` (never a
    percentage standing in for it), a tick per acceptance criterion
    `acceptance_met` names as met, and its recorded `evidence` lines. A
    missing field renders the literal text NO-DATA, never a blank and never
    a guess. Carries `BOARD_MARKER` so a second render of the same path can
    be told apart from a file this tool never wrote. Zero network
    references: every byte of CSS is inlined below, nothing is fetched."""
    program = report["program"]
    summary = report["summary"]
    name = program.get("program") or _NO_DATA
    version_target = program.get("version_target") or _NO_DATA
    objective = program.get("objective")

    section_order = report["_sectionOrder"]
    sections_by_key = report["_sectionsByKey"]
    section_names = report["_sectionNames"]
    # EVERY parse error is named here, not one kind of them. These must never
    # simply vanish from the board: a shrunk item count with no trace of why is
    # the exact failure this product exists to refuse, so each one gets its own
    # NO-DATA row below, naming the file and the reason, in the same words
    # `sbe program status` already uses for the identical fact.
    #
    # This filtered on `kind == "item"` and dropped the other two, which an
    # independent clean-room review reproduced and blocked the release over. A
    # work-items directory that cannot be read is a `source` error, and that
    # rendered as `0 of 0 items measured` with no trace: an unreadable program
    # presented as an empty one, on the very surface added to stop that. A
    # `dependency` error naming an unknown work item disappeared the same way.
    # Reading the kinds from the tagging site rather than naming one of them
    # means a kind added later is reported by default instead of silently
    # dropped, which is the failure mode that produced this bug.
    unreadable_items = list(report["parseErrors"])

    parts = []
    parts.append("<!doctype html>")
    parts.append(BOARD_MARKER)
    parts.append(
        "<!-- Generated by `sbe program board`. Regenerate, do not hand-edit: "
        "hand edits are lost on the next run. -->")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>%s: program board</title>" % _html_escape(name))
    parts.append("<style>%s</style>" % _BOARD_CSS)
    parts.append("</head><body>")

    parts.append('<div class="board-header">')
    parts.append("<h1>%s</h1>" % _html_escape(name))
    sub_bits = ["version target: %s" % _html_escape(version_target)]
    if isinstance(objective, str) and objective.strip():
        sub_bits.append(_html_escape(objective.strip()))
    parts.append('<div class="board-sub">%s</div>' % " &middot; ".join(sub_bits))

    aggregate = summary["aggregate_percent"]
    aggregate_text = ("%d%%" % aggregate) if aggregate is not None else _NO_DATA
    parts.append('<div class="board-counts">')
    parts.append('<div class="board-count"><b>%s</b>overall position</div>' % aggregate_text)
    parts.append('<div class="board-count"><b>%d of %d</b>items measured</div>' % (
        summary["measured_count"], summary["total_count"]))
    if unreadable_items:
        # The header count on its own ("N of M measured") would let a
        # reader believe the program has exactly M items. It does not: it
        # has M items that could be read, plus however many below could
        # not. This chip is the one place that makes that gap visible
        # before a reader ever reaches the NO-DATA section.
        parts.append(
            '<div class="board-count board-count-nodata"><b>%d</b>item(s) could not '
            'be read (see NO-DATA below)</div>' % len(unreadable_items))
    counts = summary["counts"]
    for status in STATUS_VALUES:
        n = counts.get(status, 0)
        if n:
            parts.append('<div class="board-count"><b>%d</b>%s</div>' % (
                n, _html_escape(status.replace("_", " "))))
    parts.append("</div>")  # board-counts
    parts.append("</div>")  # board-header

    if unreadable_items:
        # Never dropped silently: one visible row per file that could not
        # be read or normalized, naming the file and the reason, mirroring
        # `sbe program status`'s own "### Parse errors" wording rather than
        # inventing a second vocabulary for the same fact.
        parts.append('<section class="board-loop board-nodata-section">')
        parts.append("<h2>%s: items that could not be read</h2>" % _NO_DATA)
        for pe in unreadable_items:
            parts.append('<div class="board-item board-item-nodata">')
            parts.append(
                '<div><span class="board-item-id">%s</span>'
                '<span class="board-item-title">%s</span></div>' % (
                    _NO_DATA, _html_escape(pe["path"])))
            parts.append('<p class="board-nodata">%s</p>' % _html_escape(pe["reason"]))
            parts.append("</div>")  # board-item
        parts.append("</section>")

    for key in section_order:
        section_title = section_names.get(key, key)
        parts.append('<section class="board-loop">')
        parts.append("<h2>%s</h2>" % _html_escape(section_title))
        for item in sections_by_key[key]:
            parts.append('<div class="board-item">')
            parts.append(
                '<div><span class="board-item-id">%s</span>'
                '<span class="board-item-title">%s</span>'
                '<span class="board-state board-state-%s">%s</span></div>' % (
                    _html_escape(item["id"]), _html_escape(item["title"]),
                    _html_escape(item["status"]), _html_escape(_board_state_label(item["status"]))))
            parts.append(_render_board_subitems(item))
            parts.append('<div class="board-evidence-label">evidence</div>')
            parts.append(_render_board_evidence(item))
            parts.append("</div>")  # board-item
        parts.append("</section>")

    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def write_program_board(root, out_path, report=None):
    """Write `render_board_html`'s output to `out_path`. Refuses rather than
    overwriting a file `out_path` already names when that file does not
    carry `BOARD_MARKER`: that file was not written by this tool, and
    silently replacing it would destroy content this module has no way to
    reconstruct. A file that already carries the marker (a previous board
    render, at this same path) is fair game to regenerate. Raises
    `ProgramParseError` on refusal, the same exception class every other
    refusal in this module raises, so a caller has one exception type to
    catch."""
    if report is None:
        report = build_program_report(root)
    html = render_board_html(report)
    if os.path.exists(out_path):
        if os.path.isdir(out_path):
            raise ProgramParseError(
                "%s: refusing to write the board there, a directory already exists at that "
                "path" % out_path)
        # A FILE WE CANNOT READ IS A FILE WE MUST NOT OVERWRITE. Both of these
        # used to escape as a raw traceback: an existing file that is not UTF-8
        # raised UnicodeDecodeError, and one whose permissions deny reading
        # raised PermissionError. Each exited 1, which is this CLI's code for a
        # control that refused, so a crash was indistinguishable from a
        # principled refusal to anything reading the exit status. It also read
        # badly to a person: a stack trace where the tool has a perfectly good
        # sentence to say. The refusal is the same either way, because the guard
        # cannot confirm this file is one of ours, and the rule that follows from
        # not knowing is do not touch it.
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                existing = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise ProgramParseError(
                "%s: refusing to overwrite. That file already exists and could not be read "
                "(%s), so this tool cannot confirm it is a board it wrote. Move or delete it "
                "by hand, or point --out at a different path" % (out_path, exc))
        if BOARD_MARKER not in existing:
            raise ProgramParseError(
                "%s: refusing to overwrite. That file already exists and does not carry "
                "%r, the marker this tool stamps on every board it writes, so it was not "
                "written by `sbe program board`. Move or delete it by hand, or point --out "
                "at a different path" % (out_path, BOARD_MARKER))
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return html
