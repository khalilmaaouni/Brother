#!/usr/bin/env python3
"""intake_measure: Intake V2's closing measure against the plan's own targets.

The plan (docs/plan/WBS-TODAY.md, Intake V2) names three numbers: the first
request under 30,000 tokens; intake to an accepted plan in 3 turns for a
developer persona and 6 for an analyst; and the intake skill stack at or
under half of what scripts/intake_cost.py measured on 2026-09-06 (the
baseline this tool reads, docs/plan/intake-benchmark-2026-09-06/). That tool
was cut with PR 485; its baseline survives as a JSON file and this tool
re-measures BYTES by the SAME METHOD (copied from its skill_stack /
ENTRY_POINTS, not reinvented) so the two numbers stay comparable.

THREE VERDICT LINES, each PASS, FAIL or NO-DATA with the number beside it:

  BYTES  the current intake skill stack, summed the way intake_cost.py summed
         it (each ENTRY_POINT's SKILL.md plus its resolved references,
         highest cached plugin version only), against the SAME SUM taken over
         the baseline JSON's own "intake_stacks" list. The plan speaks of
         "the intake skill stack" as one thing; the baseline records it as
         five entry-point rows, so the comparable single number on each side
         is the total across all of them, not any one entry alone. PASS at or
         under 50 percent of that baseline total, FAIL above, NO-DATA when
         the baseline file cannot be read (names the path) or is missing the
         "intake_stacks" key. By default this reads the INSTALLED plugin
         cache under --claude-dir, which a cut made only in this repository
         cannot move until a release installs it; --tree ROOT resolves the
         same five entry points against a repository checkout instead
         (ROOT/bundle/skills/using-brother, ROOT/products/<plugin>/skills/
         <name> for brothermode and brothersbe), and the printed line names
         which source produced the number.

         Each reference a SKILL.md cites is UNCONDITIONAL or READ-WHEN
         (docs/decisions/intake-byte-floor-2026-09-09.json option A): a
         citation on a line starting "Read when <condition>:" (after an
         optional list marker) is read-when, any other citation is
         unconditional, and one cited both ways counts as unconditional.
         The number judged against the 50 percent bar is the UNCONDITIONAL
         total only; the read-when total is named beside it, never added in.
         The 2026-09-06 baseline predates this convention, so every one of
         its rows is unconditional by construction; it is not re-cut here.

  TURNS  counted from a Claude Code session transcript (JSONL, one JSON
         object per line). A HUMAN TURN is a line with "type": "user" and
         "origin": {"kind": "human"}: verified against a real transcript
         (2026-09-08), 83 of 85 "type":"user" lines in one session were tool
         results or slash-command expansions with no such origin, and the 2
         that were real human prompts both carried it. The record reaches
         ACCEPTED STATE at the first "type": "assistant" message whose text
         content contains the literal marker below (a convention this tool
         defines, since no such marker exists in the wild yet; the fixture
         transcripts under scripts/fixtures/intake_measure/ carry it). TURNS
         is the count of human turns seen up to and including that point.
         PASS at or under 3 for --persona developer, 6 for analyst; FAIL
         above; NO-DATA without --transcript, without --persona (the two
         personas have different bars, so there is nothing to compare
         against without one), or when the marker never appears.

  TOKENS the first "usage" object's "input_tokens" field encountered while
         reading the transcript in order (in this transcript shape, usage
         only appears on assistant messages). PASS under 30,000, FAIL at or
         above, NO-DATA without --transcript or when no usage field is ever
         found.

Exit 0 when every line is PASS. Exit 1 when any line is FAIL. Exit 2 when
none FAIL and at least one is NO-DATA (never a pass).

Standard library only, Python 3.9 floor.
"""
import argparse
import glob
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
DEFAULT_CLAUDE_DIR = os.path.join(HOME, ".claude")
NODATA = "NO-DATA"
ACCEPTANCE_MARKER = "[INTAKE-ACCEPTED]"
BYTES_PASS_FRACTION = 0.5
TOKENS_PASS_LIMIT = 30000
TURNS_PASS_LIMIT = {"developer": 3, "analyst": 6}

# Copied from the cut scripts/intake_cost.py (git show d9ab95e2:scripts/intake_cost.py)
# so BYTES stays measured the same way the baseline was produced.
ENTRY_POINTS = [
    ("brother", "using-brother", None),
    ("brothermode", "brotherme", None),
    ("brothermode", "start", "brotherme-start"),
    ("brothersbe", "start", None),
    ("brothersbe", "kickoff", None),
]

# Where --tree resolves each ENTRY_POINT's plugin_name against a repository
# checkout instead of the installed plugin cache: brother's skill lives at
# the bundle root, brothermode and brothersbe each under their own
# products/<plugin> root (root SKILL.md and references/ included).
_TREE_PRODUCT_ROOT = {
    "brother": "bundle",
    "brothermode": "products/brothermode",
    "brothersbe": "products/brothersbe",
}

_REF_TOKEN = re.compile(r"references/[\w.\-/]+\.\w+")
_ROOT_SKILL_REF = re.compile(r"\$\{[A-Z_]+\}/SKILL\.md")
_VERSION_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)")


def _version_key(version):
    m = _VERSION_PREFIX.match(version)
    nums = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    return (nums, version)


_LIST_MARKER_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*")


def _line_containing(text, pos):
    """The line of text surrounding character offset pos, no trailing
    newline."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end == -1:
        end = len(text)
    return text[start:end]


def _is_read_when_line(line):
    """True when a references/... citation on this line sits behind the
    convention two other lanes are writing into skill files as of
    2026-09-09: a line starting "Read when <condition>: references/..."
    (after an optional list marker: "-", "*", "+", "1.", "2)")."""
    prefix_end = _LIST_MARKER_PREFIX.match(line).end()
    return line[prefix_end:].startswith("Read when")


def _classify_references(text):
    """(unconditional_tokens, read_when_tokens): the references/... token
    strings _REF_TOKEN finds in text, split by the line each citation sits
    on. A token cited on at least one non-"Read when" line is unconditional
    even if another line cites it as read-when (a reference cited both ways
    counts as unconditional)."""
    unconditional = set()
    read_when = set()
    for m in _REF_TOKEN.finditer(text):
        token = m.group(0)
        line = _line_containing(text, m.start())
        if _is_read_when_line(line):
            read_when.add(token)
        else:
            unconditional.add(token)
    return unconditional, read_when


def _read_stack_files(skill_md, skill_dir, product_root):
    """(used, read_when): two path->bytes dicts for one skill's stack, given
    a resolved SKILL.md, its directory, and the root to resolve
    references/... and a root-level SKILL.md against (a plugin's cached
    version directory, or a product's directory in the tree). Shared by the
    cache and tree resolvers so both walk the same rule: SKILL.md, its local
    references/ dir, and any references/... or root SKILL.md the text
    itself points at.

    Each references/... citation is classified UNCONDITIONAL or READ-WHEN
    (see _classify_references); a file cited both ways, or never cited by
    the text at all (present only via the local references/ walk), stays
    unconditional. `used` is the figure BYTES judges against the bar;
    `read_when` is reported beside it and is never added to the total."""
    used = {}
    read_when = {}

    def add(path, bucket):
        ap = os.path.abspath(path)
        if ap in used:
            return
        if bucket == "read_when" and ap in read_when:
            return
        try:
            with open(ap, "rb") as f2:
                data = f2.read()
        except OSError:
            return
        if bucket == "read_when":
            read_when[ap] = len(data)
        else:
            read_when.pop(ap, None)
            used[ap] = len(data)

    add(skill_md, "unconditional")

    try:
        with open(skill_md, encoding="utf-8", errors="replace") as f3:
            text = f3.read()
    except OSError:
        text = ""

    unconditional_tokens, read_when_tokens = _classify_references(text)

    def classify(token):
        if token in unconditional_tokens:
            return "unconditional"
        if token in read_when_tokens:
            return "read_when"
        return "unconditional"

    local_refs = os.path.join(skill_dir, "references")
    if os.path.isdir(local_refs):
        for dirpath, _dirnames, filenames in os.walk(local_refs):
            for name in filenames:
                fpath = os.path.join(dirpath, name)
                rel_token = os.path.relpath(fpath, skill_dir).replace(os.sep, "/")
                add(fpath, classify(rel_token))

    for token in unconditional_tokens | read_when_tokens:
        local_candidate = os.path.join(skill_dir, token)
        root_candidate = os.path.join(product_root, token)
        bucket = classify(token)
        if os.path.isfile(local_candidate):
            add(local_candidate, bucket)
        elif os.path.isfile(root_candidate):
            add(root_candidate, bucket)

    if _ROOT_SKILL_REF.search(text) or "SKILL.md at that root" in text:
        root_skill = os.path.join(product_root, "SKILL.md")
        if os.path.isfile(root_skill):
            add(root_skill, "unconditional")

    return used, read_when


def _skill_stack(claude_dir, plugin_name, skill_name):
    """Bytes and file list for one intake skill's stack, or None if absent.

    Copied from the cut intake_cost.py's skill_stack(): highest cached
    version only, SKILL.md plus its local references/ dir plus any
    references/... or plugin-root SKILL.md the text itself points at.
    """
    pattern = os.path.join(claude_dir, "plugins", "cache", "brother", plugin_name,
                            "*", "skills", skill_name, "SKILL.md")
    matches = glob.glob(pattern)
    if not matches:
        pattern = os.path.join(claude_dir, "plugins", "cache", "*", plugin_name,
                                "*", "skills", skill_name, "SKILL.md")
        matches = glob.glob(pattern)
    if not matches:
        return None

    def version_of(skill_md_path):
        version_dir = os.path.dirname(os.path.dirname(os.path.dirname(skill_md_path)))
        return os.path.basename(version_dir)

    best = max(matches, key=lambda p: _version_key(version_of(p)))
    version = version_of(best)
    skill_dir = os.path.dirname(best)
    plugin_root = os.path.dirname(os.path.dirname(skill_dir))

    used, read_when = _read_stack_files(best, skill_dir, plugin_root)
    total_bytes = sum(used.values())
    return {
        "skill": "%s/%s" % (plugin_name, skill_name),
        "version": version,
        "files": sorted(used.keys()),
        "bytes": total_bytes,
        "read_when_files": sorted(read_when.keys()),
        "read_when_bytes": sum(read_when.values()),
    }


def _skill_stack_tree(tree_root, plugin_name, skill_name):
    """Bytes and file list for one intake skill's stack read from a
    repository TREE instead of the installed plugin cache, or None if
    absent. Mirrors _skill_stack's walk over the tree layout: brother's
    skill lives under ROOT/bundle/skills/<name>; brothermode and
    brothersbe live under ROOT/products/<plugin>/skills/<name>, each with
    its own product-root SKILL.md and references/. A plugin_name outside
    _TREE_PRODUCT_ROOT, or a product directory absent from this tree,
    both resolve to None (the caller reports that as a NO-DATA row)."""
    product_root_name = _TREE_PRODUCT_ROOT.get(plugin_name)
    if product_root_name is None:
        return None
    product_root = os.path.join(tree_root, product_root_name)
    skill_dir = os.path.join(product_root, "skills", skill_name)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None

    used, read_when = _read_stack_files(skill_md, skill_dir, product_root)
    total_bytes = sum(used.values())
    return {
        "skill": "%s/%s" % (plugin_name, skill_name),
        "version": "tree",
        "files": sorted(used.keys()),
        "bytes": total_bytes,
        "read_when_files": sorted(read_when.keys()),
        "read_when_bytes": sum(read_when.values()),
    }


def measure_intake_bytes(claude_dir, entry_points=ENTRY_POINTS, tree_root=None):
    """(total_bytes, rows) for every ENTRY_POINT, resolved under claude_dir
    (the installed plugin cache) unless tree_root is given, in which case
    every entry is resolved against that repository checkout instead (see
    _skill_stack_tree / _TREE_PRODUCT_ROOT).

    A missing entry point contributes 0 to the total and is reported as its
    own NO-DATA row; the total is what BYTES compares to the baseline.
    """
    rows = []
    total = 0
    for plugin_name, skill_name, alternate in entry_points:
        if tree_root is not None:
            stack = _skill_stack_tree(tree_root, plugin_name, skill_name)
            if stack is None and alternate:
                stack = _skill_stack_tree(tree_root, plugin_name, alternate)
        else:
            stack = _skill_stack(claude_dir, plugin_name, skill_name)
            if stack is None and alternate:
                stack = _skill_stack(claude_dir, plugin_name, alternate)
        key = "entry:%s/%s" % (plugin_name, skill_name)
        if stack is None:
            rows.append({"item": "%s/%s" % (plugin_name, skill_name), "bytes": 0,
                         "read_when_bytes": 0, "note": NODATA, "key": key})
        else:
            total += stack["bytes"]
            rows.append({"item": stack["skill"], "bytes": stack["bytes"],
                         "read_when_bytes": stack["read_when_bytes"], "key": key,
                         "version": stack["version"], "files": stack["files"],
                         "read_when_files": stack["read_when_files"]})
    return total, rows


def _read_json(path):
    with open(path, encoding="utf-8") as f4:
        return json.load(f4)


def _bytes_source_label(rows, tree_root):
    """The BYTES line's source tag: "tree ROOT" when reading a checkout,
    else "installed V1/V2/V3" naming each distinct plugin's resolved
    version, in ENTRY_POINTS order, so a cut made in the tree and a cut
    made to the installed plugin are never mistaken for the same reading."""
    if tree_root is not None:
        return "tree %s" % tree_root
    versions = []
    seen = set()
    for row in rows:
        plugin_name = row["item"].split("/", 1)[0]
        if plugin_name in seen:
            continue
        seen.add(plugin_name)
        versions.append(row.get("version", "?"))
    return "installed %s" % "/".join(versions)


def evaluate_bytes(claude_dir, baseline_path, tree_root=None):
    """(status, line) for the BYTES verdict. tree_root=None (default) reads
    the installed plugin cache under claude_dir, exactly as before; a
    tree_root reads the same five entry points from that repository
    checkout instead (scripts/intake_measure.py --tree).

    The baseline JSON (docs/plan/intake-benchmark-2026-09-06/...) was cut by
    scripts/intake_cost.py before the unconditional/read-when convention
    existed, so every one of its rows is unconditional by construction; it
    carries no read-when figure and is never re-cut here. Only the CURRENT
    side's total is split; the read-when portion is reported, never judged
    against the bar or added into baseline_total."""
    try:
        baseline = _read_json(baseline_path)
    except (OSError, ValueError) as exc:
        return NODATA, "BYTES: %s: could not read baseline %s (%s)" % (NODATA, baseline_path, exc)

    stacks = baseline.get("intake_stacks")
    if not stacks:
        return NODATA, "BYTES: %s: baseline %s has no intake_stacks" % (NODATA, baseline_path)
    baseline_total = sum(row.get("bytes", 0) for row in stacks)
    if baseline_total <= 0:
        return NODATA, "BYTES: %s: baseline intake_stacks total is 0" % NODATA

    source = ("tree %s" % tree_root) if tree_root is not None else claude_dir
    current_total, rows = measure_intake_bytes(claude_dir, tree_root=tree_root)
    nodata_rows = [r for r in rows if r.get("note") == NODATA]
    if current_total == 0 and len(nodata_rows) == len(rows):
        return NODATA, ("BYTES: %s: every entry point (%d of %d) is missing under %s"
                         % (NODATA, len(nodata_rows), len(rows), source))
    if nodata_rows:
        # A PARTIAL stack (some entry points present, some missing) must
        # never compute a fraction over the shrunk population: that lets a
        # measured total pass a bar it was never actually compared against
        # in full. Only a fully measured stack (nodata_rows empty) reaches
        # the fraction below.
        return NODATA, ("BYTES: %s: %d of %d entry-point rows missing under %s: %s"
                         % (NODATA, len(nodata_rows), len(rows), source,
                            ", ".join(r["key"] for r in nodata_rows)))

    read_when_total = sum(r.get("read_when_bytes", 0) for r in rows)
    fraction = current_total / float(baseline_total)
    status = "PASS" if fraction <= BYTES_PASS_FRACTION else "FAIL"
    label = _bytes_source_label(rows, tree_root)
    line = ("BYTES (%s): %s %d bytes unconditional vs baseline %d bytes "
            "(%.0f%%, bar is at or under %d%%); read-when %d bytes not counted"
            % (label, status, current_total, baseline_total, fraction * 100,
               int(BYTES_PASS_FRACTION * 100), read_when_total))
    return status, line


def _iter_transcript(transcript_path):
    with open(transcript_path, encoding="utf-8", errors="replace") as f5:
        for raw_line in f5:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                yield json.loads(raw_line)
            except ValueError:  # sbe: allow-silent a non-JSON transcript line has no message record to measure, so iteration resumes at the next line
                continue


def _assistant_text(record):
    message = record.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts)
    return ""


def count_turns_to_acceptance(transcript_path):
    """Human turns seen up to and including the ACCEPTANCE_MARKER, or None
    if the transcript has no assistant message carrying that marker."""
    count = 0
    for record in _iter_transcript(transcript_path):
        rtype = record.get("type")
        if rtype == "user" and (record.get("origin") or {}).get("kind") == "human":
            count += 1
        elif rtype == "assistant" and ACCEPTANCE_MARKER in _assistant_text(record):
            return count
    return None


def first_usage_input_tokens(transcript_path):
    """The transcript's first usage object's input_tokens, or None."""
    for record in _iter_transcript(transcript_path):
        message = record.get("message") or {}
        usage = message.get("usage")
        if isinstance(usage, dict) and "input_tokens" in usage:
            return usage["input_tokens"]
    return None


def evaluate_turns(transcript_path, persona):
    if not transcript_path:
        return NODATA, "TURNS: %s: no --transcript given" % NODATA
    if not persona:
        return NODATA, "TURNS: %s: no --persona given (developer bar is 3, analyst is 6)" % NODATA
    if persona not in TURNS_PASS_LIMIT:
        return NODATA, "TURNS: %s: unknown persona %r" % (NODATA, persona)
    turns = count_turns_to_acceptance(transcript_path)
    if turns is None:
        return NODATA, ("TURNS: %s: acceptance marker %s never appears in %s"
                         % (NODATA, ACCEPTANCE_MARKER, transcript_path))
    limit = TURNS_PASS_LIMIT[persona]
    status = "PASS" if turns <= limit else "FAIL"
    line = "TURNS: %s %d turn(s) to accepted plan (persona %s, bar is at or under %d)" % (
        status, turns, persona, limit)
    return status, line


def evaluate_tokens(transcript_path):
    if not transcript_path:
        return NODATA, "TOKENS: %s: no --transcript given" % NODATA
    tokens = first_usage_input_tokens(transcript_path)
    if tokens is None:
        return NODATA, "TOKENS: %s: no usage field found in %s" % (NODATA, transcript_path)
    status = "PASS" if tokens < TOKENS_PASS_LIMIT else "FAIL"
    line = "TOKENS: %s %d input tokens on the first request (bar is under %d)" % (
        status, tokens, TOKENS_PASS_LIMIT)
    return status, line


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--baseline", required=True, help="path to an intake_cost.py-shaped baseline JSON")
    ap.add_argument("--transcript", help="path to a Claude Code session transcript (JSONL)")
    ap.add_argument("--persona", choices=sorted(TURNS_PASS_LIMIT), help="developer or analyst")
    ap.add_argument("--claude-dir", default=DEFAULT_CLAUDE_DIR,
                     help="~/.claude to measure the intake skill stack in (override for testing)")
    ap.add_argument("--tree", help=("repository root to resolve the intake skill stack against "
                                     "instead of the installed plugin cache under --claude-dir "
                                     "(a working-tree cut cannot move BYTES until it ships, unless "
                                     "this is given)"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    bytes_status, bytes_line = evaluate_bytes(args.claude_dir, args.baseline, tree_root=args.tree)
    turns_status, turns_line = evaluate_turns(args.transcript, args.persona)
    tokens_status, tokens_line = evaluate_tokens(args.transcript)

    statuses = [bytes_status, turns_status, tokens_status]
    if "FAIL" in statuses:
        code = 1
    elif NODATA in statuses:
        code = 2
    else:
        code = 0

    if args.json:
        _, bytes_rows = measure_intake_bytes(args.claude_dir, tree_root=args.tree)
        print(json.dumps({
            "bytes": {"status": bytes_status, "line": bytes_line, "rows": bytes_rows},
            "turns": {"status": turns_status, "line": turns_line},
            "tokens": {"status": tokens_status, "line": tokens_line},
            "exit_code": code,
        }, indent=2, sort_keys=True))
    else:
        print(bytes_line)
        print(turns_line)
        print(tokens_line)
    return code


if __name__ == "__main__":
    sys.exit(main())
