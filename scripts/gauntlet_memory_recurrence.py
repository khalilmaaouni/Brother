#!/usr/bin/env python3
"""The memory recurrence gauntlet: five seeded conditions against the real recall path.

Row S12. The frozen specification is benchmarks/gauntlets/memory-recurrence.json,
section 20.4's own arm list copied without addition: memory off, memory on, stale
memory, contradictory memory, superseded memory.

WHAT THIS MEASURES. One seeded repeat action (a session about to edit
widget_parser.py, a file whose parse helper returns without validating its input)
and one seeded lesson that would prevent the repeat. Each condition plants a
different memory state in a THROWAWAY vault in a temp directory, then runs the
real recall path over it: products/brothermode/tools/bm_vault.py `check --paths`
(retrieval, supersession withholding, contradiction flagging), then
products/brothermode/tools/vault_recall_hook.py lesson_states() (the applies_to
revalidation E74 landed), then scripts/receipt_door.py applied_memory() (the
receipt section a reader actually sees). Nothing is stubbed: the same three
programs a real edit goes through.

WHAT THIS IS HONESTLY NOT. It is not the five arm benchmark over real sessions
that row S12 ships. That needs a denominator of real work units, which is why
S12 says the code exists well before the rate means anything, and this file
cannot shorten that. This measures what the MECHANISM does when a recurrence is
set up for it, once per condition, never what a model does over a night of work.
The number it prints is a property of this one fixture and says so.

HOW A CONDITION IS SCORED. Each condition names the one lesson that must reach
the receipt's applied section. A condition reads `surfaced` only when the applied
section holds exactly that slug and nothing else, `silent` when nothing reached
the model at all, and `wrong` otherwise, naming what was applied instead. The
scoring rubric frozen with the spec governs: a stale, contradictory or superseded
lesson that reaches the applied section fails its arm outright, and a refusal
that NAMES the stale lesson is a pass, because the required behaviour is refusal
rather than silence.

NO-DATA IS NEVER A PASS. A condition the current mechanism cannot express reports
NO-DATA naming the field that is missing, and is excluded from the denominator
rather than counted either way. That verdict is MEASURED from the run's own
output, never hardcoded: the contradictory arm reports NO-DATA exactly while both
sides of a declared contradiction reach the applied section together, so the day
a field ranks them, the arm scores like any other.

Run: python3 scripts/gauntlet_memory_recurrence.py
Exit 0 when every condition was observed, 1 when an arm failed the rubric,
2 when a condition could not be observed at all (NO-DATA about the run itself).
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
TOOLS_DIR = os.path.join(REPO_ROOT, "products", "brothermode", "tools")
VAULT_TOOL = os.path.join(TOOLS_DIR, "bm_vault.py")
HOOK_PATH = os.path.join(TOOLS_DIR, "vault_recall_hook.py")
SPEC_PATH = os.path.join(REPO_ROOT, "benchmarks", "gauntlets", "memory-recurrence.json")
RESULTS_DIR = os.path.join(REPO_ROOT, "benchmarks", "results")

sys.path.insert(0, HERE)
import gauntlet_frozen  # noqa: E402
import receipt_door as RD  # noqa: E402

#: The file the seeded repeat action is about to edit, and the failure class the
#: seeded lesson prevents. One family (section 19's family 1, a tiny bug fix) is
#: enough for a mechanism run, and the record says so rather than implying three.
TARGET_FILE = "widget_parser.py"
TARGET_SOURCE = "def parse(raw):\n    return raw.split(\",\")\n"

LESSON_SLUG = "validate-before-return"
LESSON_TITLE = "validate before return"
LESSON_BODY = ("The parse helper in %s returned its input unvalidated and a "
               "malformed row reached the caller. Validate before returning. "
               "See %s." % (TARGET_FILE, TARGET_FILE))

STALE_SLUG = "old-lexer-rule"
STALE_BODY = ("The lexer half of %s needs a guard in gone_lexer.py before "
              "parsing. See %s." % (TARGET_FILE, TARGET_FILE))

OPPOSITE_SLUG = "never-validate"
OPPOSITE_BODY = ("The parse helper in %s must never validate its input; the "
                 "caller owns validation. See %s." % (TARGET_FILE, TARGET_FILE))

OLD_SLUG = "old-parse-rule"
OLD_BODY = ("The parse helper in %s should return early on an empty row. "
            "See %s." % (TARGET_FILE, TARGET_FILE))
NEW_SLUG = "current-parse-rule"
NEW_TITLE = "current parse rule"

#: bm_vault.py prints exactly this heading for a note it withholds because a
#: newer note declares `supersedes: [[stem]]` (its own _print_hits), and the
#: line after it names the successor. Read off that function rather than guessed.
WITHHELD_SUPERSEDED_RE = re.compile(r"^  WITHHELD \(superseded\)  (.+?)  \[", re.M)
#: The flag _print_hits prints on an ordinary hit that some other note declares
#: it contradicts. Both sides carry it, symmetrically, and neither is withheld.
CONTRADICTS_RE = re.compile(r"^    CONTRADICTS: (.+?) \(see both", re.M)

NODATA = "NO-DATA"


def load_hook():
    """vault_recall_hook loaded by path, the same import shape
    scripts/test_recall_revalidation.py uses: lesson_states() is a pure
    function over bm_vault's own output, so it is called directly rather than
    through the consent gated cmd_check() a different suite already covers."""
    spec = importlib.util.spec_from_file_location(
        "vault_recall_hook_for_memory_recurrence", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_note(vault, stem, title, body, applies_to=None, supersedes=None,
               contradicts=None, evidence_locator=None, status=None,
               scope=None):
    """One vault note in a TEMP vault directory. The frontmatter fields are the
    ones bm_vault.py and vault_recall_hook.py actually read: name and
    description (title and summary), applies_to (E74's curator declared
    anchors), supersedes: [[stem]] and contradicts: [[stem]] (bm_vault.py's own
    single line wikilink lists, matched by FRONT_SUPERSEDES and
    FRONT_CONTRADICTS). evidence_locator, status and scope are the fields
    bm_vault_contradiction.py's own resolver reads (its FIELDS tuple) to
    decide a same-scope contradiction rather than only annotate it."""
    lines = ["---", "name: %s" % title, "description: %s" % title,
             "type: project"]
    if applies_to:
        lines.append("applies_to: [%s]" % applies_to)
        lines.append("last_verified_at: 2026-09-05")
    if supersedes:
        lines.append("supersedes: [[%s]]" % supersedes)
    if contradicts:
        lines.append("contradicts: [[%s]]" % contradicts)
    if evidence_locator:
        lines.append("evidence_locator: %s" % evidence_locator)
    if status:
        lines.append("status: %s" % status)
    if scope:
        lines.append("scope: %s" % scope)
    lines += ["---", body, ""]
    path = os.path.join(vault, stem + ".md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


#: Grep patterns checked against TARGET_FILE's own CURRENT text inside this
#: run's temp tree (never a claim taken on faith): EVIDENCE_HOLD_PATTERN is a
#: literal substring of TARGET_SOURCE, so a "grep:<target>:<pattern>" locator
#: on it HOLDS; EVIDENCE_FAIL_PATTERN is not, so the same locator shape on it
#: FAILS. Used by seed_contradictory (one holds, one fails: current evidence
#: resolves the pair) and seed_contradictory_absent_evidence (both fail:
#: current evidence resolves neither).
EVIDENCE_HOLD_PATTERN = "raw.split"
EVIDENCE_FAIL_PATTERN = "if raw is None"


def seed_off(vault, tree):
    """The control arm: the estate has learned nothing about this file."""
    return


def seed_on(vault, tree):
    write_note(vault, LESSON_SLUG, LESSON_TITLE, LESSON_BODY,
               applies_to=TARGET_FILE)


def seed_stale(vault, tree):
    """The current lesson beside one whose declared anchor no longer exists in
    the tree, the exact shape scripts/test_recall_revalidation.py drives."""
    seed_on(vault, tree)
    write_note(vault, STALE_SLUG, "old lexer rule", STALE_BODY,
               applies_to="gone_lexer.py")


def seed_contradictory(vault, tree):
    """Two retrievable lessons that assert the opposite of each other about
    the same current source, each declaring the other in contradicts:, now
    also carrying the resolver's own fields (bm_vault_contradiction.py,
    landed in PR 346) so THE LAW can engage rather than only annotate:
    LESSON_SLUG's evidence_locator points at TARGET_FILE's own current text
    and HOLDS (the file genuinely has no validation right now, which is
    exactly what this lesson asserts); OPPOSITE_SLUG's evidence_locator
    points at the same current file for a pattern that is NOT there, so it
    FAILS. Exactly one side's evidence holds, so the resolver's tier 1
    (current direct evidence) applies LESSON_SLUG and withholds the other,
    never picked on recency or wording."""
    target_path = os.path.join(tree, TARGET_FILE)
    write_note(vault, LESSON_SLUG, LESSON_TITLE, LESSON_BODY,
               applies_to=TARGET_FILE, contradicts=OPPOSITE_SLUG,
               scope=TARGET_FILE,
               evidence_locator="grep:%s:%s" % (target_path, EVIDENCE_HOLD_PATTERN))
    write_note(vault, OPPOSITE_SLUG, "never validate", OPPOSITE_BODY,
               applies_to=TARGET_FILE, contradicts=LESSON_SLUG,
               scope=TARGET_FILE,
               evidence_locator="grep:%s:%s" % (target_path, EVIDENCE_FAIL_PATTERN))


def seed_contradictory_absent_evidence(vault, tree):
    """The sibling outcome the frozen 'contradictory memory' arm cannot
    exercise on its own (founder steering 2026-09-05, section 11): both
    sides' evidence_locator point at TARGET_FILE's current text for a
    pattern that is NOT there, so BOTH fail and current evidence resolves
    neither. The resolver's own law then withholds both rather than picking
    one on recency or wording -- the second acceptable outcome, driven as a
    second run of the same arm rather than a sixth frozen condition, so the
    spec's own five-entry seeded_conditions list stays untouched."""
    target_path = os.path.join(tree, TARGET_FILE)
    write_note(vault, LESSON_SLUG, LESSON_TITLE, LESSON_BODY,
               applies_to=TARGET_FILE, contradicts=OPPOSITE_SLUG,
               scope=TARGET_FILE,
               evidence_locator="grep:%s:%s" % (target_path, EVIDENCE_FAIL_PATTERN))
    write_note(vault, OPPOSITE_SLUG, "never validate", OPPOSITE_BODY,
               applies_to=TARGET_FILE, contradicts=LESSON_SLUG,
               scope=TARGET_FILE,
               evidence_locator="grep:%s:%s" % (target_path, EVIDENCE_FAIL_PATTERN))


def seed_superseded(vault, tree):
    """An older lesson and the newer one that declares it superseded, both
    retrievable, so the run must apply the newer."""
    write_note(vault, OLD_SLUG, "old parse rule", OLD_BODY,
               applies_to=TARGET_FILE)
    write_note(vault, NEW_SLUG, NEW_TITLE, LESSON_BODY,
               applies_to=TARGET_FILE, supersedes=OLD_SLUG)


def contradiction_unresolved(obs):
    """True only when the resolver could not engage the pair at all:
    bm_vault_contradiction.py's own recall_verdict falls back to "NO_DATA"
    (the plain CONTRADICTS annotation, neither side withheld nor resolved)
    exactly when neither lesson in the pair carries evidence_locator or
    status (see bm_vault_contradiction.py's _has_signal). Read off the
    resolver's own printed verdict line in the raw recall text, never
    inferred from counting how many lessons ended up applied: a RESOLVED
    (applies) line or a WITHHELD (contradiction ...) / WITHHELD
    (contradicted, ...) line both mean the resolver DID engage, whatever it
    decided, so this reads False for them. This is now a regression guard
    for the day the fixture forgets these fields, not the everyday path:
    seed_contradictory below always supplies evidence_locator, so this
    condition's own arm no longer reports NO-DATA."""
    raw = obs.get("raw", "")
    if not CONTRADICTS_RE.search(raw):
        return False
    engaged = ("RESOLVED (applies):" in raw
               or "WITHHELD (contradiction" in raw
               or "WITHHELD (contradicted," in raw)
    return not engaged


CONTRADICTION_MISSING_FIELD = (
    "no field expresses which side of a contradiction current source supports: "
    "bm_vault.py serves both and flags each with CONTRADICTS, and "
    "vault_recall_hook.py's lesson_states has no branch for the contradictions "
    "table, so neither a note field (there is nothing like verified_against:) "
    "nor the recall path ranks the two")

CONDITIONS = [
    {
        "id": "memory off",
        "seed": seed_off,
        "expected": LESSON_SLUG,
        "note": ("the control arm: nothing is stored, so the lesson cannot be "
                 "applied and this arm counts zero by construction. A surfaced "
                 "result here would mean the arms are not isolated."),
    },
    {
        "id": "memory on",
        "seed": seed_on,
        "expected": LESSON_SLUG,
        "note": "the lesson is stored and the same edit is attempted again.",
    },
    {
        "id": "stale memory",
        "seed": seed_stale,
        "expected": LESSON_SLUG,
        "note": ("a second lesson names a path that no longer exists; it must "
                 "be refused BY NAME and the current one still applied."),
    },
    {
        "id": "contradictory memory",
        "seed": seed_contradictory,
        "expected": LESSON_SLUG,
        "nodata_when": contradiction_unresolved,
        "missing_field": CONTRADICTION_MISSING_FIELD,
        "note": ("two retrievable lessons assert the opposite of each other "
                 "about the same current source."),
    },
    {
        "id": "superseded memory",
        "seed": seed_superseded,
        "expected": NEW_SLUG,
        "note": ("an older lesson and the newer one that supersedes it are "
                 "both retrievable; the newer must be the one applied."),
    },
]


def empty_observation(raw=""):
    return {"applied": [], "stale": [], "unverified": [],
            "withheld_superseded": [], "contradicts_flagged": [],
            "recall_said_nothing": True, "raw": raw}


def real_recall(condition):
    """One condition, planted and run through the real recall path in its own
    temp directory, which is removed before this returns. Every store the three
    programs touch is redirected into that directory (HOME moves bm_vault.py's
    index and its config, BROTHERMODE_ROOT pins the correction rule store,
    BM_FRESHNESS_ROOTS and BM_FRESHNESS_STATE pin revalidation at the fixture
    tree), the same isolation products/brothermode/tools/test_bm_vault.py's own
    setUpClass uses, so no real vault, index or store is read or written."""
    tmp = tempfile.mkdtemp(prefix="gauntlet-memory-recurrence-")
    try:
        vault = os.path.join(tmp, "vault")
        tree = os.path.join(tmp, "tree")
        os.makedirs(vault)
        os.makedirs(tree)
        os.makedirs(os.path.join(tmp, ".claude"))
        with open(os.path.join(tree, TARGET_FILE), "w", encoding="utf-8") as fh:
            fh.write(TARGET_SOURCE)
        condition["seed"](vault, tree)

        env = dict(os.environ)
        env["HOME"] = tmp
        env["BROTHERMODE_ROOT"] = tmp
        env["BM_FRESHNESS_ROOTS"] = tree
        env["BM_FRESHNESS_STATE"] = os.path.join(tmp, "freshness_state.sqlite3")

        indexed = subprocess.run(
            [sys.executable, VAULT_TOOL, "index", "--vault", vault], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if indexed.returncode != 0:
            raise RuntimeError(
                "bm_vault.py index exited %d for arm %s: %s"
                % (indexed.returncode, condition["id"],
                   indexed.stdout.decode("utf-8", "replace")[:400]))

        # The repeat action: the same edit, on the same file, checked the way
        # the point of need hook checks it (--fast, the hook's own budget
        # posture; --root pins freshness at the fixture tree).
        checked = subprocess.run(
            [sys.executable, VAULT_TOOL, "check", "--paths", TARGET_FILE,
             "--limit", "5", "--fast", "--root", tree], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = checked.stdout.decode("utf-8", "replace")

        hook = load_hook()
        if hook._is_no_data(out):
            return empty_observation(out)
        records, shown = hook.lesson_states(out, tree)
        section = RD.applied_memory(records)
        return {
            "applied": sorted(e["slug"] for e in section["applied"]),
            "stale": [{"slug": e["slug"], "line": e.get("line")}
                      for e in section["stale"]],
            "unverified": sorted(e["slug"] for e in section["unverified"]),
            "withheld_superseded": sorted(WITHHELD_SUPERSEDED_RE.findall(out)),
            "contradicts_flagged": sorted(CONTRADICTS_RE.findall(out)),
            "recall_said_nothing": not records,
            "raw": shown,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def classify(condition, obs):
    """(result, detail) for one observed condition. Results: NO-DATA (the
    mechanism cannot express this condition, excluded from the denominator),
    surfaced, silent, wrong."""
    nodata_when = condition.get("nodata_when")
    if nodata_when is not None and nodata_when(obs):
        return NODATA, condition["missing_field"]
    expected = condition["expected"]
    applied = obs["applied"]
    refused = "; ".join("refused %s: %s" % (e["slug"], e["line"])
                        for e in obs["stale"] if e.get("line"))
    withheld = ", ".join(obs["withheld_superseded"])
    if applied == [expected]:
        detail = "applied exactly [%s]" % expected
        if refused:
            detail += "; " + refused
        if withheld:
            detail += "; withheld as superseded: %s" % withheld
        return "surfaced", detail
    if (not applied and not obs["stale"] and not obs["unverified"]
            and obs["recall_said_nothing"]):
        return "silent", "no lesson reached the model"
    if not applied:
        detail = "nothing applied"
        if refused:
            detail += "; " + refused
        if obs["unverified"]:
            detail += "; unverified: %s" % ", ".join(obs["unverified"])
        return "wrong", detail
    return "wrong", ("applied [%s], expected exactly [%s]"
                     % (", ".join(applied), expected))


def run_conditions(recall=None, conditions=None):
    """One row per condition: {id, expected, result, detail, ...}. `recall` is
    the seam: the default drives the real recall path in a temp directory, and
    a caller can pass a fake to prove the counting without any fixture."""
    recall = recall or real_recall
    rows = []
    for condition in conditions or CONDITIONS:
        try:
            obs = recall(condition)
        except Exception as exc:  # noqa: BLE001
            rows.append({"id": condition["id"],
                         "expected": condition["expected"],
                         "result": NODATA,
                         "detail": "%s: the arm could not be observed: %s"
                                   % (NODATA, exc),
                         "unobservable": True,
                         "note": condition.get("note", ""),
                         "applied": [], "stale": [], "unverified": [],
                         "withheld_superseded": [], "contradicts_flagged": []})
            continue
        result, detail = classify(condition, obs)
        rows.append({
            "id": condition["id"],
            "expected": condition["expected"],
            "result": result,
            "detail": detail,
            "note": condition.get("note", ""),
            "applied": obs["applied"],
            "stale": obs["stale"],
            "unverified": obs["unverified"],
            "withheld_superseded": obs["withheld_superseded"],
            "contradicts_flagged": obs["contradicts_flagged"],
        })
    return rows


def summarize(rows):
    """(prevented, counted): conditions whose recall surfaced the right lesson
    and only it, over the conditions that could be expressed at all. A NO-DATA
    row is excluded from both, never counted as either."""
    counted = [r for r in rows if r["result"] != NODATA]
    prevented = [r for r in counted if r["result"] == "surfaced"]
    return len(prevented), len(counted)


def summary_line(rows):
    prevented, counted = summarize(rows)
    return "recurrence prevented: %d of %d conditions" % (prevented, counted)


def contradictory_withheld_condition():
    """The condition dict for the second acceptable outcome named in founder
    steering section 11: current evidence resolving a contradiction to the
    correct lesson is one acceptable outcome (the 'contradictory memory'
    condition above, now resolved via seed_contradictory's evidence_locator
    fields); current evidence resolving NEITHER side, so both are withheld,
    is the other. This is a second run of the SAME arm shape rather than a
    sixth entry in CONDITIONS, so the spec's own five-entry seeded_conditions
    list (TheFiveConditionsAreTheSpecsOwnArmList in the test suite) stays
    byte for byte what it was."""
    return {
        "id": "contradictory memory (evidence absent)",
        "seed": seed_contradictory_absent_evidence,
        "expected": None,
    }


def classify_withheld(obs):
    """(result, detail) for the evidence-absent variant. Passes only when
    NEITHER lesson reached the applied section AND the resolver's own
    WITHHELD (contradiction ...) verdict line is present in the raw recall
    text -- read off the resolver's actual output line, per founder steering
    section 11, never assumed from silence: an arm that recalled nothing at
    all would look the same as a correctly-withheld one by applied-count
    alone, and only the verdict line tells them apart."""
    raw = obs.get("raw", "")
    withheld_line = ("WITHHELD (contradiction unresolved)" in raw
                      or "WITHHELD (contradiction escalated)" in raw)
    if not obs["applied"] and withheld_line:
        return ("withheld",
                "both sides withheld: no current evidence resolves this conflict")
    if obs["applied"]:
        return "wrong", ("applied [%s] despite absent evidence on both sides"
                          % ", ".join(obs["applied"]))
    return "wrong", ("nothing applied but no WITHHELD (contradiction ...) verdict "
                      "line appears either; the resolver did not visibly engage")


def run_contradictory_withheld_check(recall=None):
    """Runs the evidence-absent variant and returns one row shaped like
    run_conditions()'s own rows. Never folded into run_conditions()/
    summarize(): this is not one of the frozen five, it is the second
    acceptable outcome of the fourth one, checked separately so the
    'recurrence prevented: N of M conditions' line keeps measuring exactly
    what the spec named."""
    recall = recall or real_recall
    condition = contradictory_withheld_condition()
    try:
        obs = recall(condition)
    except Exception as exc:  # noqa: BLE001
        return {"id": condition["id"], "result": NODATA,
                "detail": "%s: the arm could not be observed: %s" % (NODATA, exc),
                "unobservable": True, "applied": []}
    result, detail = classify_withheld(obs)
    return {"id": condition["id"], "result": result, "detail": detail,
            "applied": obs["applied"]}


def _revision():
    """The commit sha of this checkout, or a NO-DATA string naming why. Never a
    fabricated value, the same posture scripts/brother_run.py's own
    _harness_revision takes."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30)
    except Exception as exc:  # noqa: BLE001
        return "%s: the revision command could not run in %s: %s" % (
            NODATA, REPO_ROOT, exc)
    if proc.returncode != 0:
        return "%s: the revision command exited %d in %s" % (
            NODATA, proc.returncode, REPO_ROOT)
    out = proc.stdout.decode("utf-8", "replace").strip()
    return out or "%s: the revision command printed nothing" % NODATA


def record(rows, path, withheld_check=None):
    prevented, counted = summarize(rows)
    doc = {
        "gauntlet": "memory-recurrence",
        "spec": os.path.relpath(SPEC_PATH, REPO_ROOT),
        "run_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "revision": _revision(),
        "fixture": {
            "target_file": TARGET_FILE,
            "workload_family": ("1, tiny bug fix. One family, not three: this "
                                "is a mechanism run and says so."),
            "instruments": [
                "products/brothermode/tools/bm_vault.py check --paths",
                "products/brothermode/tools/vault_recall_hook.py lesson_states",
                "scripts/receipt_door.py applied_memory",
            ],
        },
        "summary": {
            "prevented": prevented,
            "conditions_counted": counted,
            "line": summary_line(rows),
            "no_data": [r["id"] for r in rows if r["result"] == NODATA],
        },
        "conditions": rows,
        "contradiction_evidence_absent_check": (
            withheld_check if withheld_check is not None else
            "NO-DATA: run_contradictory_withheld_check() was not passed to record()"),
    }
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return doc


def default_record_path(today=None):
    today = today or datetime.date.today()
    return os.path.join(RESULTS_DIR, "memory-recurrence-%s.json" % today.isoformat())


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="the memory recurrence gauntlet, row S12")
    ap.add_argument("--out", default=None,
                    help="where the JSON record lands (default "
                         "benchmarks/results/memory-recurrence-<date>.json)")
    args = ap.parse_args(argv)

    try:
        frozen_result = gauntlet_frozen.check(SPEC_PATH)
    except ValueError as exc:
        print(str(exc))
        return 1
    if frozen_result.startswith(NODATA):
        print(frozen_result)
        return 2
    print("frozen: OK %s" % frozen_result)

    rows = run_conditions()
    width = max(len(r["id"]) for r in rows)
    for row in rows:
        print("%-*s  %-8s  %s" % (width, row["id"], row["result"], row["detail"]))
    print(summary_line(rows))

    # The second acceptable outcome (founder steering section 11): a second
    # run of the same arm shape, evidence absent from both sides this time,
    # never folded into rows/summarize() so the frozen five-entry count above
    # stays exactly what the spec named.
    withheld_row = run_contradictory_withheld_check()
    print("%s  %-8s  %s" % (withheld_row["id"], withheld_row["result"],
                            withheld_row["detail"]))

    out = args.out or default_record_path()
    record(rows, out, withheld_check=withheld_row)
    shown = os.path.relpath(out, REPO_ROOT) if out.startswith(REPO_ROOT) else out
    print("record: %s" % shown)

    unobservable = [r for r in rows if r.get("unobservable")]
    if withheld_row.get("unobservable"):
        unobservable = unobservable + [withheld_row]
    if unobservable:
        print("%s: %d condition(s) could not be observed at all; this is not a "
              "pass" % (NODATA, len(unobservable)))
        return 2
    failed = [r for r in rows if r["result"] == "wrong"]
    if withheld_row["result"] == "wrong":
        failed = failed + [withheld_row]
    if failed:
        print("FAILED the frozen rubric: %s"
              % ", ".join("%s (%s)" % (r["id"], r["detail"]) for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
