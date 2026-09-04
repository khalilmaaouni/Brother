#!/usr/bin/env python3
"""The decay contract: an old, never-confirmed note fades in RANKING, and a
confirmed one strengthens. It is never deleted, never moved, never rewritten.

BORROWED, and the source page is the point (readiness row E57, mechanism 2):
MemoryBank (Zhong, Guo, Wang and co-authors, AAAI 2024) updates its memories on
an Ebbinghaus forgetting curve, so an old memory nobody confirms fades while a
frequently confirmed one strengthens. Source page:
https://arxiv.org/abs/2305.10250 (code:
https://github.com/zhongwanjun/MemoryBank-SiliconFriend). bm_vault.py's own
BM25 plus anchor plus wikilink fusion has no such notion: a note written in
February and never matched since ranks exactly as a note confirmed yesterday.

WHAT THIS IS NOT, and the boundary matters more than the curve. The vault
constitution says notes are never deleted and never reorganised, so NOTHING
here writes to a note. Decay affects RANKING ONLY, it is bounded by a floor so
a decayed note can be reordered but never removed from a result set, and the
reinforcement it records lives in its own sidecar store outside the vault.

IT IS ALSO NOT bm_vault_staleness.py, which sits beside it in the same sort.
Staleness reads verified_at and asks "has anybody CHECKED this claim against
its source lately"; a stale note is demoted one AUTHORITY step, a governance
statement. Decay asks "has this note been USEFUL lately"; a decayed note keeps
its authority and loses similarity rank inside its own tier. A source of record
nobody has confirmed in a year is still a source of record.

THREE READINGS, in this order, and the first one that answers wins:

  1  the sidecar store's own entry for the note (reps, last), written by
     reinforce() when a shown note is confirmed to have prevented a repeat
  2  the note's own `decay:` frontmatter field, a curator-declared retention
     between 0 and 1, read verbatim. This is the "decay field on vault notes"
     the row names, and it is READ ONLY: no code path here writes it
  3  neither: retention 1.0, no penalty at all

Reading 3 is the one that keeps this safe to ship on a real vault. Absence is
not a measurement: 898 notes declare nothing today, and a mechanism that
demoted every one of them would be a rewrite of the whole ranking dressed up
as a decay curve.

THE CURVE. R = exp(-t / S), Ebbinghaus's own shape, where t is days since the
last reinforcement and S is stability in days. S = HALF_LIFE_DAYS * (1 + reps),
so each confirmation lengthens the memory rather than merely resetting it,
which is MemoryBank's own reinforcement half. The rank multiplier is
FLOOR + (1 - FLOOR) * R, so a fully decayed note keeps FLOOR of its similarity
score and is reordered, never erased.

Python 3.9, standard library only. No network.
"""
import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
import tempfile

#: The sidecar store. Outside the vault on purpose: a note's own file is the
#: curator's, and a retrieval statistic is not a curator's edit. Overridable
#: so a test never touches the real one.
STORE = os.environ.get(
    "BM_VAULT_DECAY",
    brother_paths.config_path("vault-decay.json"))

#: Days of stability a note with no reinforcement gets. Thirty is the estate's
#: own shortest staleness horizon halved (bm_vault_staleness.DEFAULT_HORIZONS
#: puts a decision at 180 days), chosen so decay moves inside a working month
#: rather than a working year: this is a usefulness signal, not an expiry.
HALF_LIFE_DAYS = 30.0

#: The multiplier a fully decayed note keeps. Never 0: the constitution's
#: "notes are never deleted" holds at the ranking seam too, and a note that
#: falls out of a result set entirely has been deleted from the reader's view
#: whatever the file system still holds.
FLOOR = 0.5

#: The curator-declared field, read and never written. Same single-line
#: frontmatter shape every other bm_vault_* contract module reads.
DECAY_RE = re.compile(r"^decay:\s*(\S+)\s*$", re.M)

SECONDS_PER_DAY = 86400.0


def _frontmatter(body):
    """The text between the opening and closing --- fences, or "" outside one.
    The same three lines bm_vault_staleness.py and vault_recall_hook.py each
    keep their own copy of, duplicated for the same stated reason: a contract
    module that imports a sibling to read a fence is a coupling nobody needs."""
    if not body.startswith("---"):
        return ""
    end = body.find("\n---", 3)
    return body[3:end] if end != -1 else ""


def declared_retention(body):
    """The note's own `decay:` value as a float in [0, 1], or None when the
    field is absent, unparseable, or out of range. Out of range is None and
    not a clamp on purpose: a value nobody can read is a finding, and guessing
    what a curator meant by decay: 7 would hide it."""
    m = DECAY_RE.search(_frontmatter(body or ""))
    if not m:
        return None
    try:
        value = float(m.group(1).strip().strip('"').strip("'"))
    except ValueError:  # sbe: allow-silent reader-only: None IS this parser's answer for a decay value that is not a number, and the caller falls back to the default rather than inventing one
        return None
    if value < 0.0 or value > 1.0:
        return None
    return value


def read_store(path=None):
    """The sidecar store as a dict of slug -> {"reps", "last"}, or {} when it
    is absent, unreadable, or the wrong shape. Never raises: this is a ranking
    hint, and a ranking hint that can kill a recall is not a hint."""
    path = STORE if path is None else path
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (IOError, OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_store(data, path=None):
    """Whole-store atomic replace: temp file in the same directory, then
    os.replace, so a crash mid-write leaves the previous store rather than
    half a JSON object."""
    path = STORE if path is None else path
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".vault-decay-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def reinforce(slug, now=None, path=None):
    """Record that this note earned its place: one more repetition, and the
    clock restarts from now. Returns the note's new entry.

    WHEN THIS IS CALLED, per the row: when a shown note prevented a repeat.
    scripts/repeat_control.py is the only thing on this machine that can say
    that, because it is the only thing that joins a shown lesson to a later
    session's failures, so this is a command it (or a person reading it) runs,
    never something retrieval does to itself. Retrieval reinforcing its own
    top hit would be a feedback loop that rewards whatever already ranks
    first."""
    now = _now(now)
    data = read_store(path)
    entry = data.get(slug)
    reps = int(entry.get("reps", 0)) + 1 if isinstance(entry, dict) else 1
    data[slug] = {"reps": reps, "last": float(now)}
    write_store(data, path)
    return data[slug]


def _now(now):
    if now is not None:
        return float(now)
    import time
    return time.time()


def retention(slug, body="", store=None, now=None):
    """This note's retention, a float in (0, 1]. See the module docstring's
    THREE READINGS and THE CURVE. `store` is an already-read dict (read_store
    is deliberately not called per note: one recall ranks many notes and one
    file read serves all of them)."""
    store = {} if store is None else store
    entry = store.get(slug)
    if isinstance(entry, dict) and entry.get("last") is not None:
        try:
            reps = int(entry.get("reps", 0))
            last = float(entry["last"])
        except (TypeError, ValueError):
            return 1.0
        days = max(0.0, (_now(now) - last) / SECONDS_PER_DAY)
        stability = HALF_LIFE_DAYS * (1 + max(0, reps))
        return math.exp(-days / stability)
    declared = declared_retention(body)
    if declared is not None:
        return declared
    return 1.0


def scale(slug, body="", store=None, now=None):
    """The multiplier retrieval applies to a note's similarity score:
    FLOOR + (1 - FLOOR) * retention. Bounded below by FLOOR, so decay reorders
    and never removes."""
    return FLOOR + (1.0 - FLOOR) * retention(slug, body, store, now)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="decay and reinforcement over vault notes, ranking only")
    ap.add_argument("slug", help="the note's filename stem")
    ap.add_argument("--reinforce", action="store_true",
                    help="record that this note prevented a repeat")
    ap.add_argument("--store", default=None, help="the sidecar store path")
    args = ap.parse_args(argv)
    if args.reinforce:
        entry = reinforce(args.slug, path=args.store)
        print("decay: %s reinforced, reps %d" % (args.slug, entry["reps"]))
        return 0
    data = read_store(args.store)
    if args.slug not in data:
        print("NO-DATA decay: %s has no reinforcement entry, so it ranks "
              "undecayed unless it declares a decay: field" % args.slug)
        return 2
    print("decay: %s retention %.4f, scale %.4f"
          % (args.slug, retention(args.slug, store=data),
             scale(args.slug, store=data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
