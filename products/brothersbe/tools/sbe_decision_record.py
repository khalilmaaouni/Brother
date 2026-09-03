#!/usr/bin/env python3
"""Records a human decision: the write side of the pair
`tools/sbe_decision_verify.py` has only ever been the read side of.

WHY THIS EXISTS, team complaint P4: "the decision record stays empty".
Before this file, nothing in this CLI could write one. `sbe decide` is
`sbe_decide.py`, an unrelated architecture-scoring table with no notion of a
human decision or a packet. `decisions.bind_human_decision` (src/brothersbe/
decisions.py) is the one function that decides whether a decision binds a
packet, and it was verification-only: called from `tools/sbe_decision_verify.
py` (which reads two files a caller already has and creates nothing, by its
own module docstring) and from `tools/test_sbe_readiness.py`. A decision
taken out loud in one conversation had no reachable place to land.

ONE INTERACTION, ONE PACKAGE. This tool takes what was decided, who decided
it, the alternatives that were not taken, and the condition that would flip
the decision, builds the packet and the human decision `contracts.
validate_decision_packet` / `contracts.validate_human_decision` accept
(field for field, the shape `tools/test_sbe_readiness.py`'s `a_packet`/
`a_decision` fixtures already hand-build), writes both to disk, and reads
them straight back through `decisions.bind_human_decision`, the same call
`sbe_decision_verify.py` makes. IT NEVER INVENTS A SECOND BINDING RULE:
success here is exactly "written, and `bind_human_decision` says PASS over
what was just written", never a private check of its own.

WHERE IT WRITES: `<root>/.sbe/human-decisions/<change-id>/<slug>/{packet.json,
decision.json}` by default, a store separate from `.sbe/decisions/` (the
NNN-slug `DECISION.md` store `build_package`/`write_package` own): that store
holds a different object (a markdown record quoting a shipped check's own
verdict line) and `list_packages` there treats any entry that is not a
`NNN-<slug>` directory as a NO-DATA problem, so a human decision's JSON pair
never lands inside it.

Usage:
    python3 tools/sbe_decision_record.py \\
        --change-id CHG-1 --who "the accountable engineer" \\
        --what "ship the fix now" \\
        --alternative "HOLD: wait for the missing observation" \\
        --flip-condition "the missing observation lands" \\
        --decision RELEASE

Exit codes:
  0  PASS      the packet and the decision were written and bind_human_
               decision reports they bind, at the current HEAD
  2  FAIL      both files were written but do not bind (a contract bug to
               report, never a private rule this tool invented)
  3  NO-DATA   nothing was written: bad usage, an unwritable path, or HEAD
               (or `git` itself) could not be resolved

Python floor 3.9, standard library only, mirroring `sbe_decision_verify.py`.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import contracts as contracts_mod  # noqa: E402
from brothersbe import decisions as decisions_mod  # noqa: E402
from sbe_checks import say  # noqa: E402

#: Kept apart from `decisions.DECISIONS_REL_ROOT` on purpose; see the module
#: docstring's "WHERE IT WRITES" section for why the two stores never share a
#: directory.
HUMAN_DECISIONS_REL_ROOT = os.path.join(".sbe", "human-decisions")

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slug(text):
    slug = _SLUG_STRIP_RE.sub("-", str(text or "").lower()).strip("-")
    return slug or "decision"


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _current_head(root):
    """`git rev-parse HEAD` in `root`, or None when git cannot answer."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _origin(root):
    """The repository's own `origin` remote, or a `local:` fallback naming
    the path: `origin` is a required spine field at schema 1.1 (`contracts.
    LIFECYCLE_FIELDS_INTRODUCED_IN`), and a repository recorded against with
    no remote configured still needs an answered value, never a hollow one."""
    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "local:%s" % os.path.abspath(root)


def _parse_alternative(raw, index):
    """One `{"label", "text"}` dict from a `LABEL:TEXT` or bare-text flag
    value, mirroring the `options` shape `decisions.render_decision_packet`
    reads."""
    label, sep, text = raw.partition(":")
    if not sep:
        return {"label": "option %d" % (index + 1), "text": raw.strip()}
    label = label.strip() or "option %d" % (index + 1)
    text = text.strip() or raw.strip()
    return {"label": label, "text": text}


def build_documents(args, root):
    """The packet and the human decision, as one dict with keys `packet`,
    `decision` and `head`. Raises `ValueError`, naming the problem, rather
    than writing half a pair: HEAD is read once here and stamped onto both
    documents, so the two can never disagree about which commit was decided
    over."""
    head = _current_head(root)
    if head is None:
        raise ValueError("git could not resolve HEAD in %r; nothing was recorded" % root)
    alternatives = [_parse_alternative(raw, i) for i, raw in enumerate(args.alternative)]
    not_established = ["the alternative %r was not taken and was not investigated further: %s"
                       % (a["label"], a["text"]) for a in alternatives]
    origin = _origin(root)
    now = time.time()
    packet = {
        "schemaVersion": contracts_mod.LIFECYCLE_SCHEMA_VERSION,
        "changeId": args.change_id,
        "createdAt": _iso(now),
        "producer": args.who,
        "producerClass": "human",
        "origin": origin,
        "headCommit": head,
        "readinessState": args.readiness,
        "question": args.what,
        "knownRisks": ["this decision would flip if: %s" % args.flip_condition],
        "notEstablished": not_established,
        # Renderer-only fields `decisions.render_decision_packet` reads;
        # `contracts.validate_decision_packet` never looks at them.
        "options": alternatives,
        "recommendation": args.what,
        "safeDefault": args.flip_condition,
        "neededPerson": args.who,
    }
    decision = {
        "schemaVersion": contracts_mod.LIFECYCLE_SCHEMA_VERSION,
        "changeId": args.change_id,
        "createdAt": _iso(now + 1),
        "producer": args.who,
        "producerClass": "human",
        "origin": origin,
        "headCommit": head,
        "packetSha256": contracts_mod.canonical_digest(packet),
        "decision": args.decision,
    }
    return {"packet": packet, "decision": decision, "head": head}


def _write_json(path, document):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe_decision_record.py",
        description="Record a human decision package: what was decided, by whom, the "
                    "alternatives, and what would flip it. Writes the packet and the "
                    "decision, then verifies the pair through bind_human_decision.")
    parser.add_argument("--change-id", required=True, help="the changeId both documents carry")
    parser.add_argument("--who", required=True, help="the human who decided (producer)")
    parser.add_argument("--what", required=True, help="what was decided (the packet's question)")
    parser.add_argument("--decision", required=True, choices=contracts_mod.DECISION_ANSWERS,
                        help="RELEASE or HOLD")
    parser.add_argument("--alternative", action="append", default=[],
                        metavar="LABEL:TEXT",
                        help="an alternative that was NOT taken; repeatable, at least one "
                             "required")
    parser.add_argument("--flip-condition", required=True,
                        help="what would change this decision")
    parser.add_argument("--readiness", default="READY_FOR_HUMAN_DECISION",
                        choices=contracts_mod.READINESS_STATES,
                        help="the readiness state the packet declares (default "
                             "READY_FOR_HUMAN_DECISION)")
    parser.add_argument("--out-dir", default=None,
                        help="where packet.json/decision.json land; default "
                             ".sbe/human-decisions/<change-id>/<slug of --what>")
    parser.add_argument("--cwd", default=".", help="the repository to record against")
    return parser


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 2

    root = os.path.abspath(args.cwd)
    if not os.path.isdir(root):
        print("NO-DATA: %r is not a directory; nothing was recorded" % root)
        return 3
    if not args.alternative:
        print("NO-DATA: at least one --alternative is required; a decision with no "
              "recorded alternative is not a recorded choice")
        return 3

    try:
        built = build_documents(args, root)
    except ValueError as exc:
        print("NO-DATA: %s" % exc)
        return 3

    out_dir = args.out_dir or os.path.join(
        root, HUMAN_DECISIONS_REL_ROOT, args.change_id, _slug(args.what))
    packet_path = os.path.join(out_dir, "packet.json")
    decision_path = os.path.join(out_dir, "decision.json")
    try:
        _write_json(packet_path, built["packet"])
        _write_json(decision_path, built["decision"])
    except OSError as exc:
        print("NO-DATA: %s could not be written (%s); nothing was recorded" % (out_dir, exc))
        return 3

    try:
        verdict, evidence, problems = decisions_mod.bind_human_decision(
            built["decision"], built["packet"], built["head"])
    except AttributeError:
        # This checkout's decisions.py may predate bind_human_decision (a
        # partial or stale install); a NO-DATA about the checkout, never a
        # traceback naming an internal attribute, mirrors sbe_decision_
        # verify.py's own guard for the same case.
        print("NO-DATA: this checkout's decisions.py has no bind_human_decision; update "
              "src/brothersbe/decisions.py before recording a decision")
        return 3

    say("%-8s %s" % (verdict, evidence))
    for problem in problems:
        say("  - %s" % problem)
    say("packet:   %s" % packet_path)
    say("decision: %s" % decision_path)

    if verdict == "PASS":
        return 0
    if verdict == "FAIL":
        return 2
    return 3


if __name__ == "__main__":
    sys.exit(main())
