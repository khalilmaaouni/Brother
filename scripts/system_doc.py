"""system_doc: a description of this system that cannot drift, because it is generated.

TEAM COMPLAINT P12, from a reviewer: fifty designs after a year and none of them
describes the system. Point-in-time designs, no living record.

The tempting answer is to write a good architecture document. That answer is
what produced the fifty. Each of them was a good architecture document on the day
it was written, and each became wrong quietly, and nobody could tell which of the
fifty was still true. Writing a fifty-first is not a fix, it is the disease.

SO THIS IS GENERATED FROM THE CODE AND CHECKED IN CI. `--check` regenerates and
compares; a difference is a FAILURE, which means the document cannot be wrong for
longer than it takes somebody to run the battery. That is the whole design, and
it is the only property that distinguishes this from design fifty-one.

WHAT IT DESCRIBES IS WHAT EXISTS, never what was intended. Each part is named by
its own first docstring line, so the description is written by whoever wrote the
part, in the file, where they will see it again. A part with no docstring is
listed as NO-DATA rather than quietly omitted: a system record that hides the
undocumented corners is worse than none, because it looks complete.

EACH PART IS PAIRED WITH WHAT PROVES IT. A module with a suite wired into the
battery says so; a module with none says so too. That pairing is the thing a
reviewer actually wants and no hand-written design has ever kept current.

BORROWED: docs-as-code, and the living-documentation idea behind architecture
decision records that are indexed and superseded rather than accumulated. The
adaptation is that this estate already refuses claims without evidence
everywhere else, so its system description is held to the same rule.

Python 3, standard library only. No network.
"""
import argparse
import ast
import difflib
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
OUT = os.path.join(ROOT, "SYSTEM.md")
BATTERY = os.path.join(SCRIPTS, "check_all.sh")
NODATA = "NO-DATA"


def purpose(path):
    """The first docstring line, written by whoever wrote the file.

    A path that is not a non empty string is refused with ValueError, never a
    raw TypeError. A missing, unreadable, non utf-8 or unparseable file is
    NO-DATA (None), never a raw UnicodeDecodeError or OSError."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non empty string")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    doc = ast.get_docstring(tree)
    if not doc:
        return None
    first = doc.strip().splitlines()[0].strip()
    return re.sub(r"^%s[:\s]*" % re.escape(os.path.basename(path)[:-3]), "", first)


def battery_checks(path=None):
    """(name, command) for every check the battery declares.

    RESOLVED AT CALL TIME, not at definition time. Written first as
    `path=BATTERY`, which binds the module constant into the default when the
    function object is created, so overriding BATTERY afterwards silently had no
    effect and the NO-DATA branch below could never be reached. This estate had
    already documented that exact bug in gen_readiness_board.load(), and it was
    repeated here anyway; the test that catches it is the only reason it did not
    ship. 2026-08-29."""
    path = BATTERY if path is None else path
    if not os.path.isfile(path):
        return None
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r'\s*run_check\s+"([^"]+)"\s+(.*)', line)
            if m:
                out.append((m.group(1), m.group(2).strip()))
    return out


def parts(scripts_dir=None):
    """Every module of this system, with what it is for and what proves it.

    A scripts_dir that is not a non empty string and not None is refused with
    ValueError: hostile input BLOCKS, it is never the safe case. A directory
    that does not exist yields no rows, never an OSError."""
    if scripts_dir is not None and (not isinstance(scripts_dir, str) or not scripts_dir):
        raise ValueError("scripts_dir must be a non empty string or None")
    scripts_dir = SCRIPTS if scripts_dir is None else scripts_dir
    if not os.path.isdir(scripts_dir):
        return []
    checks = battery_checks() or []
    proven = {}
    direct = {}
    for name, cmd in checks:
        for mod in re.findall(r"scripts[./]([a-z0-9_]+)\.py", cmd):
            direct.setdefault(mod.replace("test_", ""), []).append(name)
        for mod in re.findall(r"scripts\.([a-z0-9_]+)", cmd):
            direct.setdefault(mod.replace("test_", ""), []).append(name)
    for mod, names in direct.items():
        proven.setdefault(mod, []).extend(names)
    # TRANSITIVE CREDIT, one level, added 2026-09-01. A runner the battery names can
    # invoke sibling scripts, and until now those siblings were credited to nobody and
    # printed "NO-DATA, nothing in the battery runs it". Eleven capability-area scripts
    # read that way while `python3 scripts/acceptance.py` passed all eleven in a live
    # run, so the generated map and the harness disagreed and nothing could adjudicate.
    # The credit is earned from the RUNNER'S OWN SOURCE, never asserted: the runner has
    # to build the sibling's filename in code. acceptance.py does it with
    # "acceptance_{}.py".format(area["id"]), which no literal-path parser can see, so
    # the prefix form is read as well as the literal one. A module the runner merely
    # mentions in prose is NOT credited, because a comment is not an invocation.
    for mod, names in list(direct.items()):
        src_path = os.path.join(scripts_dir, "%s.py" % mod)
        try:
            with io.open(src_path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue  # sbe: allow-silent a runner outside scripts/ credits nothing transitively, which is the safe direction
        invoked = set(re.findall(r'"([a-z0-9_]+)_\{\}\.py"', src))
        invoked |= set(re.findall(r"'([a-z0-9_]+)_\{\}\.py'", src))
        literal = set(re.findall(r'["\']([a-z0-9_]+)\.py["\']', src))
        for sibling in sorted(os.listdir(scripts_dir)):
            if not sibling.endswith(".py") or sibling.startswith("test_"):
                continue
            smod = sibling[:-3]
            if smod == mod:
                continue
            prefix_hit = any(smod.startswith(p + "_") and smod[len(p) + 1:].isalnum()
                             for p in invoked)
            if prefix_hit or smod in literal:
                proven.setdefault(smod, []).extend(names)
    rows = []
    for fn in sorted(os.listdir(scripts_dir)):
        if not fn.endswith(".py") or fn.startswith("test_"):
            continue
        mod = fn[:-3]
        rows.append({
            "module": mod,
            "purpose": purpose(os.path.join(scripts_dir, fn)),
            "proven_by": sorted(set(proven.get(mod, []))),
            "has_tests": os.path.isfile(os.path.join(scripts_dir, "test_%s.py" % mod)),
        })
    return rows


def render(rows, checks):
    if rows is None or isinstance(rows, (str, bytes, dict)):
        raise ValueError("rows must be a sequence of row mappings")
    if checks is None or isinstance(checks, (str, bytes, dict)):
        raise ValueError("checks must be a sequence of (name, command) pairs")
    rows = list(rows)
    checks = list(checks)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each row must be a mapping, got %s" % type(row).__name__)
        module = row.get("module")
        if not isinstance(module, str) or not module:
            raise ValueError("each row must carry a non empty string module")
        if module in seen:
            raise ValueError("duplicate module in rows: %s" % module)
        seen.add(module)
        proven = row.get("proven_by")
        if proven is not None:
            if not isinstance(proven, (list, tuple)):
                raise ValueError("proven_by must be a sequence of strings")
            for entry in proven:
                if not isinstance(entry, str):
                    raise ValueError("proven_by entries must be strings")
    for check in checks:
        if not isinstance(check, tuple) or len(check) != 2:
            raise ValueError("each check must be a (name, command) pair")
        if not isinstance(check[0], str) or not isinstance(check[1], str):
            raise ValueError("each check name and command must be a string")
    undocumented = [r for r in rows if not r.get("purpose")]
    unproven = [r for r in rows if not r.get("proven_by")]
    L = []
    A = L.append
    A("# What this system is, right now")
    A("")
    A("GENERATED by `scripts/system_doc.py`. Do not edit this file by hand: the")
    A("next `--check` will overwrite your edit and fail the battery.")
    A("")
    A("It exists because of a reviewer's complaint that there were fifty designs")
    A("after a year and none of them described the system. Writing a fifty first")
    A("good design is what produced the fifty. This one is generated from the")
    A("code and checked, so it cannot be wrong for longer than it takes somebody")
    A("to run the battery.")
    A("")
    A("## The shape, in counts")
    A("")
    A("| | |")
    A("|---|---:|")
    A("| Parts | %d |" % len(rows))
    A("| Parts with a purpose written in the file | %d |" % (len(rows) - len(undocumented)))
    A("| Parts with a suite wired into the battery | %d |" % (len(rows) - len(unproven)))
    A("| Checks in the battery | %d |" % len(checks))
    A("")
    if undocumented:
        A("%d part(s) carry no purpose line and are listed as %s below. They are"
          % (len(undocumented), NODATA))
        A("shown rather than omitted, because a system record that hides its")
        A("undocumented corners looks complete and is not.")
        A("")
    A("## Every part, what it is for, and what proves it")
    A("")
    A("| Part | What it is for | What proves it |")
    A("|---|---|---|")
    for r in rows:
        proof_list = r.get("proven_by") or []
        proof = ", ".join("`%s`" % p for p in proof_list) if proof_list else (
            "**%s**, nothing in the battery runs it" % NODATA)
        A("| `%s` | %s | %s |"
          % (r["module"], r.get("purpose") or "**%s**, no docstring" % NODATA, proof))
    A("")
    A("## What the battery actually runs")
    A("")
    for name, cmd in checks:
        A("- `%s`: `%s`" % (name, cmd))
    A("")
    return "\n".join(L) + "\n"


def build():
    checks = battery_checks()
    if checks is None:
        return None
    return render(parts(), checks)


def docs_state_line(scripts_dir):
    """The gate ordering state line the generated record must carry.

    Ordering is allowed only within one independent group, so the line says
    exactly that and refuses to claim a live gain before an authorized canary
    line exists. Missing scripts_dir is NO-DATA, never a silent pass."""
    if not isinstance(scripts_dir, str) or not scripts_dir:
        raise ValueError("scripts_dir must be a non empty string")
    if not os.path.isdir(scripts_dir):
        return "Gate ordering: NO-DATA, scripts directory %s is missing" % scripts_dir
    return ("Gate ordering: permutes only within independent groups; "
            "live improvement NO-DATA until an authorized canary line records evidence")


def check_system_doc_uptodate(out_path):
    """True only when out_path is byte identical to what build() generates.

    A missing battery makes build() return None, which is NO-DATA and blocks
    the pass. A stale, missing or unreadable file blocks too. The comparison is
    bytes, so a newline or encoding drift cannot pass."""
    if not isinstance(out_path, str) or not out_path:
        raise ValueError("out_path must be a non empty string")
    body = build()
    if body is None:
        return False
    try:
        with open(out_path, "rb") as fh:
            current = fh.read()
    except OSError:
        return False
    return current == body.encode("utf-8")


def _blocked_diff(reason):
    """A non empty unified diff that blocks the pass and names the reason."""
    return "\n".join([
        "--- regenerated/SYSTEM.md",
        "+++ checked-in/SYSTEM.md",
        "@@ -0,0 +1 @@",
        "-BLOCKED: %s" % (reason,),
        "",
    ])


def compute_system_doc_diff(repo_root):
    """Return unified diff between regenerated and checked-in SYSTEM.md.

    RQ-1: regenerate SYSTEM.md by running the real generator for repo_root and
    compare its output to the checked-in file. RQ-2: an empty string means the
    checked-in file is current; any non-empty string is a block. A missing
    generator, a missing or empty SYSTEM.md, unreadable or non utf-8 content, a
    generator that fails or returns no data, and a stale checked-in file all
    block with a non-empty diff. A repo_root that is not a non empty string, or
    that is not a directory, is refused with ValueError."""
    global ROOT, SCRIPTS, BATTERY, OUT
    if not isinstance(repo_root, str) or not repo_root:
        raise ValueError("repo_root must be a non empty string")
    if not os.path.isdir(repo_root):
        raise ValueError("repo_root is not a directory: %r" % (repo_root,))
    tool = os.path.join(repo_root, "scripts", "system_doc.py")
    checked = os.path.join(repo_root, "SYSTEM.md")
    if not os.path.isfile(tool):
        return _blocked_diff("generator missing at %s" % tool)
    if not os.path.isfile(checked):
        return _blocked_diff("SYSTEM.md missing at %s" % checked)
    try:
        with open(checked, "rb") as fh:
            checked_bytes = fh.read()
    except OSError as exc:
        return _blocked_diff("SYSTEM.md unreadable: %s" % exc)
    if not checked_bytes:
        return _blocked_diff("SYSTEM.md is empty")
    saved = (ROOT, SCRIPTS, BATTERY, OUT)
    try:
        ROOT = repo_root
        SCRIPTS = os.path.join(repo_root, "scripts")
        BATTERY = os.path.join(repo_root, "scripts", "check_all.sh")
        OUT = checked
        body = build()
    except Exception as exc:
        return _blocked_diff("generator failed: %s" % exc)
    finally:
        ROOT, SCRIPTS, BATTERY, OUT = saved
    if body is None:
        return _blocked_diff("generator returned no data")
    if not isinstance(body, str) or not body:
        return _blocked_diff("generator produced no usable output")
    try:
        checked_text = checked_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _blocked_diff("checked-in SYSTEM.md is not utf-8: %s" % exc)
    diff_lines = list(difflib.unified_diff(
        body.splitlines(keepends=True),
        checked_text.splitlines(keepends=True),
        fromfile="regenerated/SYSTEM.md",
        tofile="checked-in/SYSTEM.md",
    ))
    return "".join(diff_lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the written file no longer matches the code")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    body = build()
    if body is None:
        print("%s: %s could not be read, so nothing was described. That is not a "
              "pass" % (NODATA, BATTERY), file=sys.stderr)
        return 2

    if args.check:
        if not os.path.isfile(args.out):
            print("%s does not exist. Run this without --check to write it."
                  % args.out, file=sys.stderr)
            return 1
        try:
            with open(args.out, encoding="utf-8") as fh:
                current = fh.read()
        except OSError as exc:
            print("%s could not be read: %s" % (args.out, exc), file=sys.stderr)
            return 1
        if current == body:
            print("SYSTEM.md still describes the code")
            return 0
        print("SYSTEM.md NO LONGER DESCRIBES THE CODE. Something was added, "
              "removed or renamed and the record did not follow. Regenerate it "
              "with: python3 scripts/system_doc.py", file=sys.stderr)
        return 1

    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError as exc:
        print("%s could not be written: %s" % (args.out, exc), file=sys.stderr)
        return 2
    print("wrote %s: %d part(s), %d check(s)"
          % (args.out, len(parts()), len(battery_checks() or [])))
    return 0


# --- L5e.4 audit doc assembly -------------------------------------------
#
# build_audit_doc assembles the L5e documentation accuracy audit document
# from section fragments. It is ADDITIVE: every name above this line keeps
# its behaviour, its output and its exit codes, and main() is untouched.

AUDIT_DOC_TITLE = "# L5e documentation accuracy audit"

AUDIT_DOC_SECTIONS = (
    "SYSTEM.md accuracy",
    "PARITY-MATRIX accuracy",
    "README install accuracy",
)

AUDIT_DOC_SCORE_SECTION = "Score"

AUDIT_DOC_REQUIRED_SECTIONS = AUDIT_DOC_SECTIONS + (AUDIT_DOC_SCORE_SECTION,)

AUDIT_DOC_MAX_SECTIONS = 32

AUDIT_DOC_MAX_BYTES = 512 * 1024

_AUDIT_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t]*$")

_AUDIT_SEPARATOR_RE = re.compile(r"^\|[\s:\-|]+\|$")


def _audit_section_heading(section):
    """Return the markdown heading of one section fragment, or None."""
    for line in section.splitlines():
        if not line.strip():
            continue
        match = _AUDIT_HEADING_RE.match(line.strip())
        if match:
            return match.group(2).strip()
        return None
    return None


def _audit_fences_closed(section):
    """Return True when every code fence in the fragment is closed."""
    fences = 0
    for line in section.splitlines():
        if line.lstrip().startswith("```"):
            fences += 1
    return fences % 2 == 0


def _audit_score_rows(section):
    """Return the data rows of the fragment's markdown score table."""
    rows = []
    saw_separator = False
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if _AUDIT_SEPARATOR_RE.match(stripped):
            saw_separator = True
            continue
        rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    if not saw_separator or len(rows) < 2:
        raise ValueError("score section has no score table")
    return rows[1:]


def build_audit_doc(sections):
    """Return the full audit doc from section fragments.

    RQ-7: one section for SYSTEM.md accuracy, one for PARITY-MATRIX
    accuracy, one for README install accuracy, and one score table with one
    row per checked area. RQ-10: a wrong type, an empty list, a blank or
    headingless fragment, a duplicate heading, an unclosed code fence, a
    missing required section, and a score section without a score table
    each raise ValueError rather than crashing.
    """
    if not isinstance(sections, list):
        raise ValueError("sections must be a list of section fragments")
    if not sections:
        raise ValueError("sections must not be empty")
    if len(sections) > AUDIT_DOC_MAX_SECTIONS:
        raise ValueError("too many sections: %d" % len(sections))
    seen = {}
    order = []
    for index, section in enumerate(sections):
        if not isinstance(section, str):
            raise ValueError("section %d must be a str" % index)
        if not section.strip():
            raise ValueError("section %d must not be blank" % index)
        heading = _audit_section_heading(section)
        if heading is None:
            raise ValueError("section %d has no markdown heading" % index)
        if not _audit_fences_closed(section):
            raise ValueError("section %d has an unclosed code fence" % index)
        key = heading.lower()
        if key in seen:
            raise ValueError("duplicate heading: %s" % heading)
        seen[key] = section.strip()
        order.append(key)
    for required in AUDIT_DOC_REQUIRED_SECTIONS:
        if required.lower() not in seen:
            raise ValueError("missing required section: %s" % required)
    rows = _audit_score_rows(seen[AUDIT_DOC_SCORE_SECTION.lower()])
    labels = [row[0].lower() for row in rows if row and row[0]]
    for area in AUDIT_DOC_SECTIONS:
        if area.lower() not in labels:
            raise ValueError("score table has no row for: %s" % area)
    body = AUDIT_DOC_TITLE + "\n\n" + "\n\n".join(
        seen[key] for key in order) + "\n"
    if len(body.encode("utf-8")) > AUDIT_DOC_MAX_BYTES:
        raise ValueError("audit doc exceeds %d bytes" % AUDIT_DOC_MAX_BYTES)
    return body


if __name__ == "__main__":
    sys.exit(main())
