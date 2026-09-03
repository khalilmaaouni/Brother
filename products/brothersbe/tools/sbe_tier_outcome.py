#!/usr/bin/env python3
"""Tier-versus-outcome report (H5, a MEASURE not a gate).

The hole this closes: nothing compares an intake's assigned TIER with the
change's REAL OUTCOME. H4 (tools/sbe_intake.py) lets a defect intake name
what it fixes on `origin.fixes`; H8 (brothersbe.decisions.close_durations_by_tier)
stamps a dossier's `openedAt` and reads a task's own `closedAt` back to
measure how long a tiered change actually took. This ties the two together:
per tier, how many changes closed, their median close duration, and how
many of those closed changes were later named as what a DEFECT intake
fixes -- the signal that the tier judged them too light the first time.

THE CONTRACT, same shape as tools/sbe_approval_concentration.py's: a
MEASURE, never a gate. This tool always exits 0, even on its own internal
bug. A tier with no closed, tiered, intake-stamped task prints NO-DATA,
the same words H8's own `render_close_durations_by_tier` already prints
for that case. A tier with closures and zero defect links prints the zero
as a count, never NO-DATA: a defect count is a fact about this run, not an
absence.

WHAT COUNTS AS "NAMING A ROW": a change is identified by its own dossier
directory name under `design/` (the same `change` field `sbe task open
--change` records, and the same name `decisions._dossier_intake` resolves
against). A defect intake's `origin.fixes` text NAMES a closed change when
that change's directory name appears in it as a substring. `fixes` is free
text a human types (H4 accepts "REG-114" or a plain sentence), so substring
containment is the only honest test here; a placeholder like "TBD" is read
through `sbe_checks.answered`, the same reading H4's own `normalize_origin`
already applies.

A dossier's `00-intake.json` that does not parse is a MALFORMED intake:
skipped with a named warning, never a crash and never silently dropped.

Usage: python3 tools/sbe_tier_outcome.py [target-dir] [--out PATH]
target-dir defaults to the current directory. Exit code is always 0.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, mirroring every other tool in this directory.
"""
import argparse
import io
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from sbe_checks import answered  # noqa: E402
from brothersbe import decisions as decisions_mod  # noqa: E402
from brothersbe import tasks as tasks_mod  # noqa: E402

INTAKE_REL = decisions_mod.INTAKE_REL


def closed_changes_by_tier(root):
    """{tier: [(change_id, duration_seconds), ...]}, one entry per CLOSED
    task in `.sbe/tasks.json` whose `change` resolves to a dossier carrying
    both a readable `tier` and an `openedAt` intake stamp -- the exact
    filter `decisions.close_durations_by_tier` applies, kept alongside the
    change id here because that function discards it and a defect link
    needs it to know which row was closed.

    A task missing `closedAt`, a change with no resolvable tier, or a
    dossier with no `openedAt` contributes no sample, exactly as H8 already
    decided; nothing here re-derives that rule differently."""
    buckets = {tier: [] for tier in decisions_mod.TIERS}
    try:
        data = tasks_mod.load_registry(root)
    except tasks_mod.RegistryUnusable:
        return buckets
    for task in data.get("tasks", []) or []:
        if task.get("status") != "closed" or not task.get("closedAt"):
            continue
        change = (task.get("change") or "").strip()
        intake = decisions_mod._dossier_intake(root, change)
        if intake is None or not intake["openedAt"]:
            continue
        try:
            opened_epoch = tasks_mod._iso_epoch(intake["openedAt"])
            closed_epoch = tasks_mod._iso_epoch(task["closedAt"])
        except ValueError as exc:
            # Same reasoning as decisions.close_durations_by_tier: this feeds a
            # reported bucket, and a pair dropped in silence makes the population
            # smaller than the reader believes.
            sys.stderr.write(
                "sbe-tier-outcome: skipping task %r, its timestamps did not parse "
                "(%s), so it is not in any bucket below.\n"
                % (task.get("id", "?"), exc))
            continue
        duration = closed_epoch - opened_epoch
        if duration >= 0 and change:
            buckets[intake["tier"]].append((change, duration))
    return buckets


def defect_fix_texts(root):
    """(texts, warnings): every non-placeholder `origin.fixes` text from a
    defect intake under `design/`, plus one warning per dossier whose
    `00-intake.json` could not be parsed."""
    design_dir = os.path.join(root, "design")
    texts = []
    warnings = []
    if not os.path.isdir(design_dir):
        return texts, warnings
    for name in sorted(os.listdir(design_dir)):
        path = os.path.join(design_dir, name, INTAKE_REL)
        if not os.path.isfile(path):
            continue
        try:
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            warnings.append("skipped an unreadable intake at %s (%s)" % (path, e))
            continue
        origin = data.get("origin")
        if not isinstance(origin, dict) or origin.get("type") != "defect":
            continue
        fixes = answered(origin.get("fixes"))
        if fixes is not None:
            texts.append(fixes)
    return texts, warnings


def render(root):
    """The full report as a list of lines. Numbers only, no judgment: a
    NO-DATA tier names why, a tiered-and-closed tier prints its counts."""
    lines = ["TIER VS OUTCOME for %s" % root, ""]
    buckets = closed_changes_by_tier(root)
    texts, warnings = defect_fix_texts(root)
    for tier in decisions_mod.TIERS:
        samples = buckets[tier]
        if not samples:
            lines.append(
                "%s: NO-DATA (no closed task both stamped with an intake openedAt and "
                "resolved to this tier)" % tier)
            continue
        durations = [d for _, d in samples]
        linked = {change_id for change_id, _ in samples
                  if any(change_id in t for t in texts)}
        lines.append("%s: %d closed, median %.0fs close duration, %d defect-linked"
                     % (tier, len(samples), statistics.median(durations), len(linked)))
    for w in warnings:
        lines.append("WARNING: %s" % w)
    return lines


def safe_render(root):
    """`render`, never raising: a bug in this tool is reported as an honest
    line, the same crash boundary `sbe_approval_concentration.safe_render`
    already applies, because a measure that can fail the build over its own
    bug is a gate wearing a measure's name."""
    try:
        return render(root)
    except Exception as e:  # noqa: intentionally broad, see module docstring
        return ["TIER VS OUTCOME for %s" % root, "",
                "NO-DATA: could not report because %s: %s" % (type(e).__name__, e)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default=os.getcwd(),
                    help="repository to report on (default: current directory)")
    ap.add_argument("--out", default=None,
                    help="also write the report here (best effort; a write "
                         "problem here is reported, never fails the build)")
    ns = ap.parse_args(argv)
    root = os.path.abspath(ns.target)

    lines = safe_render(root)
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)

    if ns.out:
        try:
            d = os.path.dirname(os.path.abspath(ns.out))
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(ns.out, "w") as fh:
                fh.write(text)
        except OSError as e:
            sys.stderr.write("sbe-tier-outcome: could not write %s "
                              "(%s), report already printed above\n" % (ns.out, e))

    return 0


if __name__ == "__main__":
    sys.exit(main())
