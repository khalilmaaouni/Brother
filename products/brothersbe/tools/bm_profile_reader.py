#!/usr/bin/env python3
"""bm_profile_reader: BrotherSBE's own READ-ONLY reader of the shared vault
profile file, so its own start skill can skip a question the profile already
answers.

Why this file exists rather than an import. Per
docs/adr/2026-08-12-where-the-shared-machinery-lives.md, "no session in either
project runs the other's tools": BrotherSBE never imports or calls
products/brothermode/tools/bm_profile.py. Both products read the SAME plain
text file for the SAME person/project, so this module reproduces that file's
format and precedence rules byte-for-byte, standalone, from its own source.
It is deliberately a reader only: recording and promoting a preference stays
BrotherMode's job (its own start flow already does the recording); this file
only needs to read the result.

THE FILE, reproduced from bm_profile.py's own docstring so both readers agree
on it: 10-Projects/<slug>/Profile.md under the vault root, one dated line per
recorded fact ("YYYY-MM-DD: <key>: <value>"), oldest first, never rewritten.
A plain key (role, level, anything not prefixed "preference: ") resolves to
its most recent value straight away. A "preference: <name>" key only resolves
once the SAME value has been recorded 3 or more times (promoted). A hand
written "correct: <key>: <value>" line always wins over both of those, for
that key, however many times it was otherwise recorded.

Python 3.9, standard library only, no network, no import of bm_profile.py or
any other brothermode tool.

Usage:
    python3 tools/bm_profile_reader.py read --project <slug>
    python3 tools/bm_profile_reader.py promoted --project <slug> --key <key>
"""
import argparse
import os
import re
import sys

# Same default as bm_profile.py's own DEFAULT_VAULT: both products read one
# shared file, so the resolved path must agree without either importing the
# other.
DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
PROMOTE_AFTER = 3
CORRECT_PREFIX = "correct: "

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}): (.+)$")


def profile_path(vault_root, project_slug):
    """The one path this module reads: 10-Projects/<slug>/Profile.md, identical
    to bm_profile.py's own profile_path()."""
    return os.path.join(vault_root, "10-Projects", project_slug, "Profile.md")


def _read_text(path):
    """Explicit failure path: a file that cannot be read is reported to stderr
    and treated as absent, never a crash, never silently swallowed. A missing
    file is the ordinary first-intake case and stays quiet."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:  # sbe: allow-silent missing file is the ordinary first-intake case, docstring above
        return None
    except OSError as e:
        sys.stderr.write("bm_profile_reader: cannot read %s: %s\n" % (path, e))
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


def promoted(path, key):
    """The value for key once it has been recorded 3 or more times with the
    SAME value, else None. A "correct: <key>: <value>" line always wins."""
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
    """The current effective profile as {key: value}, matching bm_profile.py's
    own read() exactly: a plain key resolves to its latest value; a
    "preference: <name>" key only appears once promoted; a "correct:" line
    always wins for that key. Returns {} when the file is absent, unreadable,
    or empty."""
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
# CLI, read-only: no record subcommand. Recording stays BrotherMode's job.
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

    pp = sub.add_parser("promoted", help="print a key's promoted value, if any")
    add_common(pp)
    pp.add_argument("--key", required=True)

    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if not args.vault:
        args.vault = DEFAULT_VAULT
    return {"read": cmd_read, "promoted": cmd_promoted}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
