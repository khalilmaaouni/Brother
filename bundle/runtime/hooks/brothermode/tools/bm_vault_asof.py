#!/usr/bin/env python3
"""bm_vault_asof: the tooling half of D09's "ask a fact what it was at a past
instant" observable. bm_vault_temporal.py (the contract half, shipped separately)
defines the five bi-temporal fields and answers in_truth(window, problems, when)
for ONE note's own declared window. This file is the query surface a caller
actually uses: which notes were the truth as of instant T across the whole
vault, or for one named note, with supersedes: edges honoured.

WHY A SEPARATE QUERY IS NEEDED, NOT JUST bm_vault_temporal.py's OWN in_truth().
A note's own frontmatter often never gets an explicit valid_to: someone writes
the successor, links it with supersedes:, and never goes back to close the old
note's window by hand. Read in isolation, in_truth() correctly calls that old
note "still true forever" (absence never invents a validity window -- that is
its own contract, and it must not guess). But the vault DOES know better: the
supersedes: edge from the new note to the old one, plus the new note's own
valid_from, tells you exactly when the old note stopped being the answer. This
module derives that missing valid_to from the graph, so a superseded note IS
the answer for instants before its successor's valid_from and is NOT for
instants after, even when nobody ever typed a valid_to on the superseded note
itself. Reuses bm_vault_temporal.py (parse/scan/in_truth, never re-decided
here) and bm_vault_graph.py's own typed-edge resolver (_load_notes/
_build_indices/_build_file_index/_typed_edges, the same wikilink resolution
bm_vault_graph.py's own `edges` command already uses) rather than
reimplementing frontmatter parsing or wikilink resolution a second time.

FOUR OUTCOMES PER NOTE AT INSTANT T, never collapsed to two:
  declared_true      has a declared (or supersedes-derived) window, and T falls
                      inside it: this is the answer that RESTS ON A DECLARED
                      INTERVAL.
  declared_false      has a window, T falls outside it (before valid_from, or at
                      or after an explicit or derived valid_to).
  timeless_current    carries no temporal field at all (still true of most of
                      this corpus). Absence never invents a window, so this is
                      reported as its own bucket, distinct from "declared true",
                      and always counted toward "current truth" the way it
                      already behaves everywhere else in this estate.
  malformed           a problem bm_vault_temporal.parse already named (bad date,
                      inverted window). CANNOT SAY, excluded from the answer
                      set, reported rather than silently coerced either way.

CEILING (stated, not papered over): supersession is followed ONE HOP. A chain
A supersedes B supersedes C only closes B's window against A's valid_from and
C's window against B's valid_from; C's effective end is not chased through B a
second time. Good enough for the corpus today (no observed chain longer than
one hop); if a longer chain appears, walk superseded_by transitively here.

Exit 0 on a normal query (including declared_false/malformed answers -- those
are correct answers, not failures), 2 on NO-DATA (no readable vault, or an
unparseable --date, or an unknown --stem). Python 3.9 floor, stdlib only,
writes nothing anywhere: read-only, same as its two sibling modules.

No em or en dashes anywhere in this file.
"""
import argparse
import importlib.util
import os
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(filename, modname):
    """Dynamic import by path, the same pattern bm_freshness.py already uses for
    bm_vault.py: load a sibling tool file without relying on tools/ being on
    sys.path, and without re-deciding anything the sibling module already owns."""
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_TOOLS_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stem(relpath):
    """vault-relative path -> stem, matching bm_vault_graph.py's own _no_ext:
    forward slashes, ".md" stripped. bm_vault_temporal.scan() returns
    os.path.relpath() (os.sep-joined); normalized here so a note's temporal
    window and its graph stem key on the exact same string."""
    rel = relpath.replace(os.sep, "/")
    return rel[:-3] if rel.lower().endswith(".md") else rel


def build_effective_windows(vault, bt, bg):
    """{stem: window} with valid_to backfilled from supersedes: edges where the
    note's own frontmatter left it open, plus {stem: problems} and {stem: source}
    ("own" when valid_to came from the note's own frontmatter or there was none
    to derive, "supersedes" when this function closed it from a successor's
    valid_from). Never narrows a window a human already closed: an existing,
    earlier explicit valid_to always wins over a later derived cap."""
    rows = bt.scan(vault)
    windows, problems_by_stem = {}, {}
    for relpath, window, problems in rows:
        stem = _stem(relpath)
        windows[stem] = dict(window)
        problems_by_stem[stem] = problems

    notes = bg._load_notes(vault)
    exact, by_basename = bg._build_indices(notes)
    file_index = bg._build_file_index(vault)
    typed = bg._typed_edges(notes, exact, by_basename, file_index)
    superseded_by = typed["superseded_by"]  # old_stem -> [new_stem, ...]

    source = {stem: ("own" if "valid_to" in w else None) for stem, w in windows.items()}

    for old_stem, successors in superseded_by.items():
        caps = [windows[new_stem]["valid_from"] for new_stem in successors
                if new_stem in windows and "valid_from" in windows[new_stem]]
        if not caps:
            continue
        derived_cap = min(caps)
        old_window = windows.setdefault(old_stem, {})
        problems_by_stem.setdefault(old_stem, [])
        existing_vt = old_window.get("valid_to")
        if existing_vt is None or derived_cap < existing_vt:
            old_window["valid_to"] = derived_cap
            source[old_stem] = "supersedes"
        elif source.get(old_stem) is None:
            source[old_stem] = "own"

    return windows, problems_by_stem, source


def classify(window, problems, when, bt):
    """One of the four outcomes in the module docstring, for one note at one
    instant. Delegates the actual interval math to bt.in_truth rather than
    re-deciding valid_from-inclusive/valid_to-exclusive semantics here."""
    if problems:
        return "malformed"
    if not window or "valid_from" not in window:
        return "timeless_current"
    return "declared_true" if bt.in_truth(window, problems, when) else "declared_false"


def answer_as_of(vault, when, bt, bg):
    """[(stem, state, source, valid_from, valid_to)], sorted by stem, for every
    note bm_vault_temporal.scan() found."""
    windows, problems_by_stem, source = build_effective_windows(vault, bt, bg)
    rows = []
    for stem in sorted(windows):
        window = windows[stem]
        problems = problems_by_stem.get(stem, [])
        state = classify(window, problems, when, bt)
        rows.append((stem, state, source.get(stem), window.get("valid_from"), window.get("valid_to")))
    return rows


def cmd_query(vault, when, stem=None, verbose=False):
    bt = _load("bm_vault_temporal.py", "bm_vault_temporal")
    bg = _load("bm_vault_graph.py", "bm_vault_graph")
    rows = answer_as_of(vault, when, bt, bg)

    if stem is not None:
        matches = [r for r in rows if r[0] == stem]
        if not matches:
            print("bm_vault_asof: NO-DATA, no note found at stem %r" % stem, file=sys.stderr)
            return 2
        s, state, src, vf, vt = matches[0]
        print("as of %s: %s -> %s" % (when, s, state.upper()))
        if state in ("declared_true", "declared_false"):
            print("  window: valid_from=%s valid_to=%s (source: %s)" % (vf, vt, src))
        return 0

    counts = {"declared_true": 0, "declared_false": 0, "timeless_current": 0, "malformed": 0}
    for _s, state, _src, _vf, _vt in rows:
        counts[state] += 1
    print("vault: %s" % vault)
    print("as of: %s" % when)
    print("notes: %d total" % len(rows))
    print("in truth, resting on a declared interval: %d" % counts["declared_true"])
    print("not in truth, resting on a declared interval: %d" % counts["declared_false"])
    print("timeless-current (no temporal field, absence invents no window): %d"
          % counts["timeless_current"])
    print("malformed window (CANNOT SAY, excluded and reported): %d" % counts["malformed"])
    if verbose:
        for s, state, src, vf, vt in rows:
            if state in ("declared_true", "declared_false"):
                print("  [%s] %s  (valid_from=%s valid_to=%s, source=%s)"
                      % (state.upper(), s, vf, vt, src))
            else:
                print("  [%s] %s" % (state.upper(), s))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("query",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--date", required=True, help="YYYY-MM-DD, the instant to ask about")
    ap.add_argument("--stem", default=None,
                    help="ask about one note only, by its vault-relative stem (no .md)")
    ap.add_argument("--verbose", action="store_true", help="list every note's verdict")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_asof: NO-DATA, no readable vault at %r" % args.vault, file=sys.stderr)
        return 2
    # Reuses bm_vault_temporal.py's own date parser rather than a second one: it
    # already tolerates quoted dates and refuses garbage the same way this file
    # must.
    bt = _load("bm_vault_temporal.py", "bm_vault_temporal")
    when = bt._parse_date(args.date)
    if when is None:
        print("bm_vault_asof: query needs --date YYYY-MM-DD", file=sys.stderr)
        return 2
    return cmd_query(args.vault, when, stem=args.stem, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
