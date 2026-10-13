#!/usr/bin/env python3
"""rc_handover.py: refuse a release candidate handover that says more than its evidence.

RC0.a (docs/plan/specs/RC0.md). The handover is one markdown file whose twelve
fields are lines `- <Field>: <value>`, in the order of FIELDS. Six of them
(SOURCED) end with `(source=<path>)`, a regular file under the evidence root.
The rule: a field may say less than its source, never more. A PASS beside a
source that says NO-DATA is refused; counts are repeated exactly; a source
that cannot be read, or that predates the candidate commit, never supports a
pass. The owner decision stays PENDING with NO AUTO RELEASE, and no line may
claim a tag, a public push or a release.

    python3 scripts/rc_handover.py --check FILE [--root DIR] [--evidence DIR]

Exit 0 and `PASS handover`; exit 1 with one line per problem; exit 2 NO-DATA
when FILE is absent. Reads only; runs git cat-file and git show in --root.

Python 3.9, standard library only. No em or en dashes.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional

FIELDS = ("Baseline SHA/tag", "Candidate SHA/branch", "Landed U-items and linked tests",
          "Reverted/refused items and reasons", "Public gate summary",
          "Actual signed-in host results, installed identity and guard action",
          "Resume/lock/corruption replay", "SUT productive units, minutes and interventions",
          "Raw competitive arm packets and missing controls",
          "Version/source-to-bundle/export manifest check",
          "Unresolved risks that block stronger claims", "Owner decision required")
SOURCED = FIELDS[4:10]
STRENGTH = {"FAIL": 0, "BLOCKED": 0, "NO-DATA": 1, "PASS": 2}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EVIDENCE = os.path.join(os.path.expanduser("~"), ".claude", "evidence", "plan113")
WBS = os.path.join("docs", "plan", "BROTHER-1.1.3-WBS.json")

COUNTS = re.compile(r"\bpass\s+(\d+)\s+fail\s+(\d+)\s+no-data\s+(\d+)")
SOURCE = re.compile(r"\(source=([^)]+)\)\s*$")
VERDICT = re.compile(r"(?<![\w-])(PASS|FAIL|NO-DATA|BLOCKED)(?![\w-])")
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
CLAIM = re.compile(r"\btagged\b|pushed to the public|\breleased\b", re.I)


def parse_handover(text: str) -> Dict[str, str]:
    out = {}  # type: Dict[str, str]
    for line in text.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        label, _, value = line[2:].partition(":")
        label, value = label.strip(), value.strip()
        if label not in FIELDS:
            out["!unrecognized:" + label] = value
        elif label in out:
            out["!duplicate:" + label] = value
        else:
            out[label] = value
    return out


def source_verdict(text: str) -> str:
    lines = text.split("\n")
    lines = lines[:-1]  # the part after the last newline is torn (or empty) and never decides
    for line in reversed(lines):
        m = COUNTS.search(line)
        if m:
            _, fail, nodata = (int(x) for x in m.groups())
            return "FAIL" if fail > 0 else "NO-DATA" if nodata > 0 else "PASS"
    for line in reversed(lines):
        if line.startswith("REHEARSAL READY"):
            return "PASS"
        if line.startswith("REHEARSAL NOT-READY"):
            return "FAIL"
    for line in reversed(lines):
        if line.startswith("GREEN"):
            return "PASS"
        if line.startswith("RED"):
            return "FAIL"
        if "COMPOSITE: refused" in line or "upper bound" in line or line.startswith("NO-DATA"):
            return "NO-DATA"
        if line.startswith("BLOCKED"):
            return "BLOCKED"
    return "NO-DATA"


def value_verdict(value: str) -> str:
    m = VERDICT.search(SOURCE.sub("", value))
    return m.group(1) if m else "NO-DATA"


def _git(root: str, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def commit_exists(root: str, sha: str) -> Optional[bool]:
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return False
    out = _git(root, "cat-file", "-t", sha)
    if out is None:
        return None
    return out.returncode == 0 and out.stdout.strip() == "commit"


def _commit_time(root: str, sha: str) -> Optional[int]:
    out = _git(root, "show", "-s", "--format=%ct", sha)
    try:
        return int(out.stdout.strip()) if out is not None and out.returncode == 0 else None
    except ValueError:
        return None


def _read_source(evidence_root: str, path: str):
    """(text, mtime, problem): the problem is set when the source cannot support anything."""
    full = path if os.path.isabs(path) else os.path.join(evidence_root, path)
    real, base = os.path.realpath(full), os.path.realpath(evidence_root)
    if not real.startswith(base + os.sep):
        return None, None, "is outside the evidence root %s" % evidence_root
    if not os.path.isfile(real):
        return None, None, "source unreadable: %s is not a regular file" % path
    try:
        with open(real, "rb") as f:
            return f.read().decode("utf-8"), os.path.getmtime(real), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, "source unreadable: %s (%s)" % (path, exc.__class__.__name__)


def _units(value: str):
    if value.strip().lower() == "none":
        return []
    return [(i.partition(":")[0].strip(), i.partition(":")[2].strip()) for i in value.split(";") if i.strip()]


def check_handover(path: str, root: str, evidence_root: str, notes: Optional[List[str]] = None) -> List[str]:
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except UnicodeDecodeError:
        return ["handover is not UTF-8: %s" % path]
    except OSError as exc:
        return ["handover unreadable: %s (%s)" % (path, exc)]
    fields = parse_handover(text)
    problems = []
    for key in fields:
        if key.startswith("!duplicate:"):
            problems.append("repeated field: %s" % key[11:])
        elif key.startswith("!unrecognized:"):
            problems.append("unrecognized field: %s" % key[14:])
    present = [k for k in fields if not k.startswith("!")]
    if present != [f for f in FIELDS if f in present]:
        problems.append("fields out of order: they must follow the template order")
    for label in FIELDS:
        if label not in fields:
            problems.append("missing field: %s" % label)
        elif not fields[label]:
            problems.append("empty field: %s" % label)
    get = lambda k: fields.get(k, "")  # noqa: E731

    cand_time = None
    for label in FIELDS[:2]:
        if not get(label):
            continue
        shas = SHA.findall(get(label))
        if not shas:
            problems.append("%s names no commit" % label)
        for sha in shas[:1]:
            found = commit_exists(root, sha)
            if found is None:
                problems.append("%s: git could not run to resolve %s; never a pass" % (label, sha))
            elif not found:
                problems.append("%s: %s is not a commit in %s" % (label, sha, root))
            elif label == FIELDS[1]:
                cand_time = _commit_time(root, sha)

    for label in SOURCED:
        value = get(label)
        if not value:
            continue
        m = SOURCE.search(value)
        if not m:
            problems.append("%s: no (source=<path>) at the end of the value" % label)
            continue
        text_src, mtime, why = _read_source(evidence_root, m.group(1).strip())
        if why:
            problems.append("%s: %s" % (label, why))
            continue
        if notes is not None:
            notes.append("SOURCE %s mtime %s" % (m.group(1).strip(),
                                                   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))))
        if cand_time is not None and mtime < cand_time:
            problems.append("%s: source %s is older than the candidate commit; it describes another tree"
                            % (label, m.group(1).strip()))
        said, src = value_verdict(value), source_verdict(text_src)
        if STRENGTH[said] > STRENGTH[src]:
            problems.append("%s: says %s but its source says %s" % (label, said, src))
        src_counts = [c.groups() for c in COUNTS.finditer(text_src)]
        if src_counts:
            got = COUNTS.search(SOURCE.sub("", value))
            if not got or got.groups() != src_counts[-1]:
                problems.append("%s: counts %s differ from the source's pass %s fail %s no-data %s"
                                % ((label, " ".join(got.groups()) if got else "absent") + src_counts[-1]))

    wbs_ids = None
    try:
        with open(os.path.join(root, WBS), encoding="utf-8") as f:
            wbs_ids = {u["id"] for u in json.load(f)["units"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problems.append("WBS unreadable: %s (%s)" % (WBS, exc.__class__.__name__))
    in_wbs = lambda uid: wbs_ids is not None and uid.split(".")[0] in wbs_ids  # noqa: E731
    for uid, cmd in _units(get(FIELDS[2])):
        if not in_wbs(uid):
            problems.append("landed unit %s is not in the WBS" % uid)
        paths = [t for t in cmd.split() if t.endswith((".py", ".sh"))]
        if not paths:
            problems.append("landed unit %s has no test command" % uid)
        elif not os.path.isfile(os.path.join(root, paths[0])):
            problems.append("landed unit %s: test path %s does not exist" % (uid, paths[0]))
    for uid, reason in _units(get(FIELDS[3])):
        if not in_wbs(uid):
            problems.append("refused unit %s is not in the WBS" % uid)
        if not reason:
            problems.append("refused unit %s has no reason" % uid)

    decision = get(FIELDS[11])
    if decision:
        if not decision.startswith("PENDING"):
            problems.append("%s: must begin PENDING" % FIELDS[11])
        for word in ("accept", "hold", "abandon"):
            if not re.search(r"\b%s\b" % word, decision, re.I):
                problems.append("%s: does not name %s" % (FIELDS[11], word))
        if "NO AUTO RELEASE" not in decision:
            problems.append("%s: lacks NO AUTO RELEASE" % FIELDS[11])
    for n, line in enumerate(text.splitlines(), 1):
        if CLAIM.search(line.replace("NO AUTO RELEASE", "")):
            problems.append("line %d claims a tag, a public push or a release: %s" % (n, line.strip()))
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", required=True, metavar="FILE")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    args = ap.parse_args(argv)
    if not os.path.isfile(args.check):
        print("NO-DATA handover absent: %s" % args.check)
        return 2
    notes = []  # type: List[str]
    problems = check_handover(args.check, args.root, args.evidence, notes)
    for line in notes + problems:
        print(line)
    if problems:
        return 1
    print("PASS handover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
