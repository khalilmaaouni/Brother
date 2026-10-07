#!/usr/bin/env python3
"""L4's done check: the L4.5 validator's verdict, plus the two parent guards it does not make.

usage (repo root): python3 -B scripts/donecheck_L4.py [--root DIR]
Exit 0 when the L4 assessment passes L4.5's validator, names all four SoL-Pi mechanism ids, and every
`path:line` citation in it resolves; 1 when a readable record proves the bar is not met; 2 when the
evidence is missing, unreadable, corrupt or unknown, which is NO-DATA and never a pass.

WHY IT EXISTS. L4's recorded done_check was the sentence "each proposed mechanism names Brother's own
current behavior it would change, and a measurable before/after (token count from a real run, not
estimated)". No tool can execute a sentence, so the unit could never close however much work landed
under it. The sentence is the DEFINITION and stays in the unit's done_check_prose field; this command
is the MEASUREMENT.

WHAT IT REFUSES, which is the point. The bar for the assessment lives in L4.5's validator, so this
parent DELEGATES to it instead of writing a second validator that would drift away from the first, and
adds only what the sentence names that the validator does not check: all four mechanism ids are named,
and every `path:line` citation resolves to a real line of a real file under --root. A citation that
went stale because the cited file changed is a failure, which is the intended direction. A validator
that has not landed, an unreadable or non-UTF-8 document, and a validator result that is not a list of
strings are NO-DATA. This script reads only, writes nothing under --root, and never runs a shell.

HOSTILE INPUT, refused at the source. Every helper below is total: handed None, a wrong type, NaN or a
value that cannot name a file, it returns its own refusal shape (a named problem, or (None, why))
instead of letting a raw TypeError or AttributeError reach the caller.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VALIDATOR_REL = os.path.join("scripts", "check_harness_efficiency_assessment.py")
ASSESSMENT_REL = os.path.join("docs", "architecture", "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md")
# docs/plan/specs/L4.md:77 names these four mechanisms, so a document that drops one has not assessed it.
MECHANISM_IDS = ("action_fusion", "online_context_compact", "observation_pack", "evidence_preserving_reducer")
# docs/plan/specs/L4.md:201-204: every `path:line` citation must point at a real line of a real file.
CITATION_RE = re.compile(r"(?<![\w/.-])([\w./-]+\.(?:py|sh|json|md)):(\d+)\b")
# No text file has a line number this long, and int() on a pathological run of digits is a refusal here
# rather than a crash on the interpreters that cap the length of int-from-string conversion.
MAX_LINE_DIGITS = 9


def load_validator(root):
    """(module, reason): the L4.5 validator when it is usable, otherwise (None, why).

    reason None means L4.5 has not landed (no file, or no validate_assessment in it); any other reason
    is what stopped the import. Both are NO-DATA to the caller, never a pass. A root that is not a
    directory path is refused here rather than handed to os.path.join.
    """
    if not isinstance(root, str) or not root:
        return None, "the root is not a directory path (%s)" % type(root).__name__
    path = os.path.join(root, VALIDATOR_REL)
    if not os.path.isfile(path):
        return None, None
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("l4_assessment_validator", path)
        if spec is None or spec.loader is None:
            return None, "the file %s carries no importable module" % path
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, SyntaxError, OSError, ValueError) as exc:
        return None, "the L4.5 validator (%s) could not be imported (%s: %s)" % (path, type(exc).__name__, exc)
    if not hasattr(module, "validate_assessment"):
        return None, None
    return module, None


def read_text(path):
    """(text, None) for a UTF-8 file, (None, why) when it cannot be read as text.

    A path that is not a non empty string is refused here, never handed to open(): None, bytes, a
    number, NaN and a container come back as a named reason instead of a TypeError.
    """
    if not isinstance(path, str) or not path:
        return None, "the path is not a file path (%s), so no text can be read from it" % type(path).__name__
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except (OSError, ValueError) as exc:
        return None, "%s could not be read (%s)" % (path, exc)
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, "%s is not UTF-8 text (%s)" % (path, exc)


def mechanism_problems(text):
    """Every way the document fails to name the four mechanisms it is about.

    A text that is not a string (None, a list, a number, a bool) is refused as a named problem here
    rather than reaching .strip(), which would raise an AttributeError.
    """
    if not isinstance(text, str):
        return ["the assessment document is not text (%s), so it names no current behavior"
                % type(text).__name__]
    problems = []
    if not text.strip():
        problems.append("the assessment document is empty, so it names no current behavior")
    for mechanism_id in MECHANISM_IDS:
        if mechanism_id not in text:
            problems.append("the mechanism id %s is not named in the document" % mechanism_id)
    return problems


def citation_problems(text, root):
    """(citations seen, problems): every `path:line` citation must resolve under root.

    A text that is not a string, or a root that is not a directory path, is refused as a named problem
    (seen 0) here rather than reaching the regex or os.path, which would raise a raw TypeError.
    """
    if not isinstance(text, str):
        return 0, ["the assessment document is not text (%s), so no citation can be read"
                   % type(text).__name__]
    if not isinstance(root, str) or not root:
        return 0, ["the root is not a directory path (%s), so no citation can be resolved"
                   % type(root).__name__]
    problems = []
    seen = 0
    root_norm = os.path.normpath(os.path.abspath(root))
    for match in CITATION_RE.finditer(text):
        seen += 1
        rel = match.group(1)
        digits = match.group(2)
        if len(digits) > MAX_LINE_DIGITS:
            problems.append("citation %s:%s is not a usable line number" % (rel, digits[:MAX_LINE_DIGITS]))
            continue
        lineno = int(digits)
        target = os.path.normpath(os.path.join(root_norm, rel))
        if target != root_norm and not target.startswith(root_norm + os.sep):
            problems.append("citation %s:%d points outside the root" % (rel, lineno))
            continue
        body, why = read_text(target)
        if body is None:
            problems.append("citation %s:%d cannot be read: %s" % (rel, lineno, why))
            continue
        line_count = len(body.splitlines())
        if lineno < 1 or lineno > line_count:
            problems.append("citation %s:%d is past the end of that file, which holds %d line(s)"
                            % (rel, lineno, line_count))
    return seen, problems


def main(argv: list[str] | None = None) -> int:
    if argv is not None and not (isinstance(argv, (list, tuple))
                                and all(isinstance(item, str) for item in argv)):
        print("NO-DATA: argv must be a list of strings, not %s" % (type(argv).__name__,))
        return 2
    parser = argparse.ArgumentParser(description="L4's done check; see the module docstring.")
    parser.add_argument("--root", default=ROOT,
                        help="repository to read (default: the repository holding this script)")
    args = parser.parse_args(argv)
    if not isinstance(args.root, str) or not args.root:
        print("NO-DATA: --root must name a repository directory, not %r" % (args.root,))
        return 2
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("NO-DATA: %s is not a directory, so no record can be read from it" % root)
        return 2
    doc_path = os.path.join(root, ASSESSMENT_REL)

    module, why = load_validator(root)
    if module is None:
        if why is None:
            print("NO-DATA: L4.5 has not landed, so the L4 bar cannot be measured under %s" % root)
        else:
            print("NO-DATA: %s" % why)
        return 2

    try:
        errors = module.validate_assessment(doc_path)
    except (OSError, ValueError) as exc:
        print("NO-DATA: the L4.5 validator could not read the assessment (%s: %s)"
              % (type(exc).__name__, exc))
        return 2
    if not isinstance(errors, list) or not all(isinstance(item, str) for item in errors):
        print("NO-DATA: the L4.5 validator returned %s, not a list of strings, so its verdict is unknown"
              % (type(errors).__name__,))
        return 2

    text, why = read_text(doc_path)
    if text is None:
        print("NO-DATA: %s" % why)
        return 2

    mechanism_gap = mechanism_problems(text)
    seen, citation_gap = citation_problems(text, root)
    if seen == 0:
        citation_gap = citation_gap + ["the document cites no `path:line`, so it names no current behavior"]

    failures = ["validator: %s" % error for error in errors]
    failures.extend(mechanism_gap)
    failures.extend(citation_gap)
    if failures:
        print("EVIDENCE %s" % doc_path)
        for failure in failures:
            print("  SHORT   %s" % failure)
        print("FAIL: %d problem(s) in the L4 assessment (%d from the validator, %d mechanism problem(s), "
              "%d citation problem(s))" % (len(failures), len(errors), len(mechanism_gap), len(citation_gap)))
        return 1
    print("PASS: validator clean, %d of %d mechanisms named, %d of %d citations resolve"
          % (len(MECHANISM_IDS), len(MECHANISM_IDS), seen, seen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
