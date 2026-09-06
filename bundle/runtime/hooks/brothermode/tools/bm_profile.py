#!/usr/bin/env python3
"""bm_profile: the vault profile V7 asked for, one plain text file per person and
project so the intake gets quieter the longer a team uses it (role, level, and
standing preferences read before the first question).

  read      print the CURRENT effective profile (role, level, and every
            preference that has been promoted), or NO-DATA when the file is
            absent or unreadable
  record    append one dated line, never rewrite an existing one, per the vault
            constitution's append-only law ("supersede or append, never edit")
  promoted  print whether one key has reached its promotion threshold, and the
            value, or NO-DATA when it has not

THE FILE. 10-Projects/<slug>/Profile.md, plain lines the person can read and, by
hand, correct: no YAML frontmatter, no machinery, because the whole point of this
row is that a non-engineer can open the file and see why a question was skipped.
A stated fact (role, level) counts from its first line: it is read back next
session with no repeat count, matching the seam both start/SKILL.md files already
name ("Write the answer to session state; that is the seam for a persistent vault
profile, not the profile itself"). A preference is different: the done-check is
explicit that it promotes to a default "after three repeats and not before", so
record() logs every stated preference and promoted() (folded into read()) only
surfaces one once the SAME value has been recorded three or more times.

THE CORRECTION PATH. The person edits or appends a line "correct: <key>: <value>"
by hand, in the same plain text file, no CLI required to write it. A correction
always wins over both the raw latest value and a promoted default, checked last
in both read() and promoted() below.

Python 3.9, standard library only, no network. Mirrors tools/bm_vault_promote.py's
own shape: a DEFAULT_VAULT constant, an explicit-failure _read(), an argparse CLI
over one small set of module functions a caller can also import directly.
"""
import argparse
import datetime
import os
import re
import sys

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
PROMOTE_AFTER = 3
CORRECT_PREFIX = "correct: "

# "2026-09-05: preference: tone: formal" -- date first, then key and value
# split on the LAST ": " in the remainder, since the key itself legitimately
# contains one or two (e.g. "preference: tone", "correct: preference: tone").
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}): (.+)$")


def profile_path(vault_root, project_slug):
    """The one path this whole module reads and writes: 10-Projects/<slug>/Profile.md."""
    return os.path.join(vault_root, "10-Projects", project_slug, "Profile.md")


def _read_text(path):
    """Explicit failure path, matching bm_vault_promote.py's own _read(): a file
    that cannot be read is reported to stderr and treated as absent, never a
    crash and never silently swallowed. A missing file is the ordinary case
    (first intake ever) and stays quiet."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:  # sbe: allow-silent missing file is the ordinary first-intake case, docstring above
        return None
    except OSError as e:
        sys.stderr.write("bm_profile: cannot read %s: %s\n" % (path, e))
        return None


def _entries(path):
    """Every dated line in the file, in file order, as (key, value) pairs. None
    when the file is absent or unreadable; [] when it exists but holds no
    parseable line yet."""
    text = _read_text(path)
    if text is None:
        return None
    out = []
    for line in text.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        rest = m.group(2)
        if ": " not in rest:
            continue
        key, value = rest.rsplit(": ", 1)
        out.append((key, value))
    return out


def record(path, key, value):
    """Append one dated line for key/value. Never rewrites or removes an
    existing line: this is the vault's append-only law applied to one file.
    Creates the file (and its parent directory) with a short header on first
    use. Raises OSError on a write failure; callers decide how to report it,
    matching the boundary rule that a write failure is never swallowed."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    is_new = not os.path.exists(path)
    line = "%s: %s: %s\n" % (datetime.date.today().isoformat(), key, value)
    with open(path, "a", encoding="utf-8") as f:
        if is_new:
            f.write("# Profile\n\n"
                    "Plain lines, oldest first, never edited: correct any line by\n"
                    "appending \"correct: <key>: <value>\".\n\n")
        f.write(line)


def promoted(path, key):
    """The value for key once it has been recorded 3 or more times with the
    SAME value, else None. A "correct: <key>: <value>" line always wins,
    however many times the key was otherwise recorded, and however recent."""
    entries = _entries(path)
    if not entries:
        return None
    correction = None
    counts = {}
    for k, v in entries:
        if k == CORRECT_PREFIX + key:
            correction = v
        elif k == key:
            counts[v] = counts.get(v, 0) + 1
    if correction is not None:
        return correction
    for value, n in counts.items():
        if n >= PROMOTE_AFTER:
            return value
    return None


def read(path):
    """The current effective profile as {key: value}. A plain key (role,
    level, anything not prefixed "preference: ") resolves to its most
    recently recorded value straight away, no repeat count needed: that
    matches the seam start/SKILL.md already names, a stated fact is read
    back next session as-is. A "preference: <name>" key only appears once
    promoted() would return a value for it (3+ repeats of the same value).
    A "correct: <key>: <value>" line always wins for that key, over either
    path. Returns {} when the file is absent, unreadable, or empty."""
    entries = _entries(path)
    if not entries:
        return {}
    latest = {}
    corrections = {}
    pref_counts = {}
    for k, v in entries:
        if k.startswith(CORRECT_PREFIX):
            corrections[k[len(CORRECT_PREFIX):]] = v
            continue
        latest[k] = v
        if k.startswith("preference: "):
            pref_counts.setdefault(k, {})
            pref_counts[k][v] = pref_counts[k].get(v, 0) + 1
    result = {}
    for k, v in latest.items():
        if k.startswith("preference: "):
            counts = pref_counts.get(k, {})
            hit = next((val for val, n in counts.items() if n >= PROMOTE_AFTER), None)
            if hit is not None:
                result[k] = hit
        else:
            result[k] = v
    result.update(corrections)
    return result


# ---------------------------------------------------------------------------
# CLI, thin over the three functions above.
# ---------------------------------------------------------------------------

def _resolve_path(args):
    if args.profile:
        return args.profile
    if args.vault and args.project:
        return profile_path(args.vault, args.project)
    return None


def cmd_read(args):
    path = _resolve_path(args)
    if not path:
        print("NO-DATA: pass --profile, or --vault and --project")
        return 3
    if not os.path.isfile(path):
        print("NO-DATA: no profile at %s" % path)
        return 3
    data = read(path)
    if not data:
        print("no promoted or recorded facts yet at %s" % path)
        return 0
    for k in sorted(data):
        print("%s: %s" % (k, data[k]))
    return 0


def cmd_record(args):
    path = _resolve_path(args)
    if not path:
        print("NO-DATA: pass --profile, or --vault and --project")
        return 3
    try:
        record(path, args.key, args.value)
    except OSError as e:
        print("NO-DATA: could not write %s: %s" % (path, e))
        return 3
    print("recorded: %s: %s" % (args.key, args.value))
    return 0


def cmd_promoted(args):
    path = _resolve_path(args)
    if not path:
        print("NO-DATA: pass --profile, or --vault and --project")
        return 3
    value = promoted(path, args.key)
    if value is None:
        print("NO-DATA: %s is not promoted" % args.key)
        return 3
    print(value)
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--profile", default=None, help="direct path to Profile.md")
        sp.add_argument("--vault", default=None, help="vault root, default the Kay Vault")
        sp.add_argument("--project", default=None, help="project slug under 10-Projects/")

    pr = sub.add_parser("read", help="print the current effective profile")
    add_common(pr)

    pw = sub.add_parser("record", help="append one dated key/value line")
    add_common(pw)
    pw.add_argument("--key", required=True)
    pw.add_argument("--value", required=True)

    pp = sub.add_parser("promoted", help="print a key's promoted value, if any")
    add_common(pp)
    pp.add_argument("--key", required=True)

    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if not args.vault:
        args.vault = DEFAULT_VAULT
    return {"read": cmd_read, "record": cmd_record, "promoted": cmd_promoted}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
