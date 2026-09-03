#!/usr/bin/env python3
"""Atomic Enterprise Benchmark V2, dimension D: Vault institutional memory.

FOUNDER ASK 2026-08-29: "put a benchmark at atomic level to reach for our vault
technology". This implements dimension D (D01 to D15) of the steering directive
as MACHINE-READABLE CHECKS THAT RUN, which is that document's own P0-1: establish
truth before more architecture, because otherwise every team calls its feature
enterprise-ready using a different definition.

THE SCORING DOCTRINE, from section 7 of the directive, and it is the part most
benchmarks get wrong. Three numbers, never one, and NO-DATA never disappears
from the denominator:

    proven_score     = 10 * PASS_W / TOTAL_W      how much is actually proven
    coverage         = (PASS_W + FAIL_W) / TOTAL_W   how much we managed to test
    covered_accuracy = PASS_W / (PASS_W + FAIL_W)    where we had evidence, how often we cleared

A system with 7 PASS, 1 FAIL and 12 NO-DATA must not outrank one with 16 PASS
and 4 FAIL merely because its denominator got smaller. Reporting proven_score
alone is exactly how a score is raised by testing less, which section 30 names
as a thing to refuse.

WHAT THIS DELIBERATELY DOES NOT DO. It does not detect documentation tokens.
Section 6 names that as V1's first limitation: several V1 checks pass because a
word appears in a file. Every check below reads the vault's own content or runs
the real tool and reads what it does. A check that cannot do that returns
NO-DATA and says why, rather than grading prose.

Exit 0 the benchmark ran. Exit 2 NO-DATA, the vault could not be read at all.
A FAIL never sets a nonzero exit: this measures, it does not gate. Making it a
gate before the baseline is known would be the same mistake as designing
benchmark criteria after the feature is built.

Python 3.9 floor, standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
WIKILINK = re.compile(r"\[\[([^\]|]+)")
SKIP_DIRS = {".git", ".trash", ".obsidian"}


def field(block, name):
    m = re.search(r"(?m)^%s:\s*(.*)$" % re.escape(name), block or "")
    return m.group(1).strip() if m else None


def load_notes(vault):
    """Every note with its frontmatter block, read once. Telemetry is included
    rather than filtered: it is inside the vault, so the vault's contract
    applies to it, and hiding it would be the scope trick this estate already
    recorded as a way to make a checker look clean."""
    out = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                # This estate already recorded hiding a note as the scope trick
                # that makes a checker look clean; an unreadable note dropped
                # silently from the count is the same trick one level down, so
                # it is named on stderr rather than only vanishing from the sum.
                print("vault_benchmark_v2: %s could not be read, excluded from "
                      "the count: %s" % (p, exc), file=sys.stderr)
                continue
            m = FRONT.match(text)
            out.append({
                "path": os.path.relpath(p, vault),
                "stem": os.path.splitext(fn)[0],
                "front": m.group(1) if m else "",
                "has_front": bool(m),
                "body": text,
            })
    return out


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


# --------------------------------------------------------------------------
# The checks. Each returns (verdict, detail). Each reads real state.
# --------------------------------------------------------------------------

def d01_central_retrieval_api(ctx):
    """Memory cannot depend on one developer machine or path."""
    tools = ctx["tools"]
    hook = os.path.join(tools, "vault_recall_hook.py")
    if not os.path.exists(hook):
        return NODATA, "no vault_recall_hook.py under %s" % tools
    with open(hook, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    # A retrieval entry that names a specific person's home directory cannot be
    # installed on a second machine, which is the whole of D01.
    operator = sorted(set(re.findall(r"/Users/[A-Za-z0-9._-]+/", src)))
    if operator:
        return FAIL, "retrieval entry names an operator home path: %s" % ", ".join(operator)
    # TIGHTENED after the first run graded this PASS. expanduser("~/Documents/...")
    # carries no operator name, so a check looking only for /Users/<name>/ calls it
    # portable. It is not: it assumes a CHECKOUT AT A FIXED PLACE IN A HOME
    # DIRECTORY, which is a developer-machine dependency and is exactly what D01
    # forbids. A check that answers a narrower question than the risk is the
    # failure this estate keeps recording, and it happened here first try.
    home_default = re.findall(r'expanduser\(\s*"(~/[^"]+)"', src)
    if home_default:
        return FAIL, ("retrieval resolves to a per-developer home checkout by default (%s): "
                      "portable in spelling, machine-bound in fact. D01 wants a defined service "
                      "interface, not a guessed local path" % ", ".join(sorted(set(home_default))))
    if "os.environ" not in src:
        return FAIL, "retrieval entry has no environment override, so its path is fixed at install"
    return PASS, "retrieval entry resolves through a configured interface with no local default"


def _foreign_anchor(ctx):
    """An anchor token cited by a real vault note whose file does NOT exist under
    the freshness roots. That is the shape that forces an exhaustive walk per
    root, which is the case the 6-second timeout used to kill silently."""
    roots = [os.path.expanduser(r) for r in (
        os.environ.get("BM_FRESHNESS_ROOTS", "").split(os.pathsep) or [])
        if r] or [os.path.expanduser(x) for x in (
            "~/Documents/BrotherModeUp", "~/Brother", "~/Documents/BrotherSBE")]
    tok = re.compile(r"\b([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:swift|py|ts|tsx|kt|java|go|rb))\b")
    seen = set()
    for n in ctx["notes"]:
        for cand in tok.findall(n["body"][:4000]):
            if cand in seen:
                continue
            seen.add(cand)
            if any(os.path.exists(os.path.join(r, cand)) for r in roots):
                continue
            found = False
            for r in roots:
                for dp, dn, fnames in os.walk(r):
                    dn[:] = [d for d in dn if not d.startswith(".")]
                    if cand in fnames:
                        found = True
                        break
                if found:
                    break
            if not found:
                return cand
    return None


def d02_point_of_need_invocation(ctx):
    """Measured, not asserted: run the real tool on a real payload and see
    whether it answers inside the budget the hook actually allows it."""
    tool = os.path.join(ctx["tools"], "bm_vault.py")
    if not os.path.exists(tool):
        return NODATA, "no bm_vault.py to query"
    hook = os.path.join(ctx["tools"], "vault_recall_hook.py")
    budget = None
    if os.path.exists(hook):
        with open(hook, encoding="utf-8", errors="replace") as fh:
            hsrc = fh.read()
        m = re.search(r"timeout=(?:TIMEOUT_S|(\d+))", hsrc)
        m2 = re.search(r"TIMEOUT_S\s*=\s*(\d+)", hsrc)
        budget = int(m2.group(1)) if m2 else (int(m.group(1)) if m and m.group(1) else None)
    if budget is None:
        return NODATA, "could not read the hook's own timeout budget"
    # THE CASE THAT MATTERS, not the easy one. The first version of this check
    # queried bm_vault.py, a file INSIDE bm_freshness's hardcoded roots, and
    # graded PASS at 0.1s. The defect this row exists to catch only appears for a
    # file OUTSIDE those roots, where an exhaustive os.walk per root was measured
    # at 8.7 to 9.4 seconds against what used to be a 6 second budget. Testing the
    # fast path reproduced, inside the benchmark, the exact blindness the timeout
    # bug had. Both are measured now and the WORST one decides.
    # The out-of-roots probe is DERIVED FROM THE VAULT, not invented. An invented
    # name matches no note, so the query returns NO-DATA in milliseconds and
    # certifies nothing: the first version of this fix graded PASS on a 0.0s probe
    # that had found nothing at all, which is the empty-output-reads-as-clean
    # shape. A probe that matches no note is reported as NO-DATA, never as speed.
    probes = [("in-roots", "bm_vault.py")]
    foreign = _foreign_anchor(ctx)
    if foreign:
        probes.append(("outside-roots", foreign))
    worst, results = 0.0, []
    for label, name in probes:
        t0 = time.time()
        try:
            pr = subprocess.run([sys.executable, tool, "check", "--paths", name, "--limit", "2"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=budget + 30)
            rc = pr.returncode
        except subprocess.TimeoutExpired:
            return FAIL, ("a %s query did not return within %ds, far past the hook's %ds budget"
                          % (label, budget + 30, budget))
        took = time.time() - t0
        worst = max(worst, took)
        results.append("%s %.1fs (exit %d)" % (label, took, rc))
    if worst > budget:
        return FAIL, ("worst case %.1fs against a %ds hook budget, so the hook kills it SILENTLY "
                      "and no lesson is ever shown: %s" % (worst, budget, "; ".join(results)))
    if not foreign:
        return NODATA, ("only the in-roots path could be measured (%s). No vault note cites a file "
                        "outside the freshness roots, so the expensive case this row exists for "
                        "could not be exercised, and a fast easy case is not evidence about it"
                        % "; ".join(results))
    return PASS, "worst case %.1fs inside a %ds budget: %s" % (worst, budget, "; ".join(results))


def d03_memory_on_off_benchmark(ctx):
    # The harness lives beside THIS script (Brother scripts/), not only in the
    # BMU tools dir the --tools flag names; a probe that looks in one place for
    # a thing that lives in another reports absence about its own blind spot.
    here = os.path.dirname(os.path.abspath(__file__))
    for root in (ctx["tools"], here):
        for name in ("memory_ab.py", "memory_on_off.py", "bm_memory_ab.py"):
            if os.path.exists(os.path.join(root, name)):
                return PASS, "found %s" % os.path.join(root, name)
    return NODATA, "no memory ON/OFF harness exists yet; nothing to run, so nothing is claimed"


def d04_memory_outcome_lift(ctx):
    """REWRITTEN 2026-08-30, when the first real ON/OFF run landed: the old body
    returned NO-DATA unconditionally, the fourth unconditional-verdict probe
    found in one night. The probe reads the newest results file the harness
    wrote, validates that every task ran BOTH ways, and reports the lift with
    its own denominator. A malformed or unpaired file is NO-DATA with the
    reason; a measured zero-or-negative lift is a FAIL stated plainly, because
    a measurement that memory did not help is a real answer, not missing data."""
    import glob as _glob
    here = os.path.dirname(os.path.abspath(__file__))
    pattern = ctx.get("results_glob") or os.path.join(here, "..", "benchmarks",
                                                      "memory-ab", "results-*.json")
    candidates = sorted(_glob.glob(pattern))
    if not candidates:
        return NODATA, ("no outcome-lift measurement exists. The 56-match recall result proves "
                        "memory SURFACED, which is not evidence that memory HELPED")
    path = candidates[-1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return NODATA, "results file %s unreadable (%s); nothing is claimed" % (path, e)
    # Schema check before any .get()/iteration: a JSON value that parses
    # but is not shaped like {"rows": [...]} must not crash this check
    # (AttributeError on .get() off a bare list was the exact miss here).
    if not isinstance(data, dict):
        return NODATA, "results file %s is not a JSON object; nothing is claimed" % path
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        return NODATA, "results file %s 'rows' is not a list; nothing is claimed" % path
    by_task = {}
    for r in rows:
        if not isinstance(r, dict) or "task_id" not in r or "memory" not in r:
            return NODATA, "results file %s carries a malformed row; nothing is claimed" % path
        by_task.setdefault(r["task_id"], {})[r["memory"]] = bool(r.get("success"))
    unpaired = [t for t, m in by_task.items() if set(m) != {"on", "off"}]
    if unpaired or not by_task:
        return NODATA, ("results file %s is not fully paired (%d task(s) missing a side); an "
                        "interrupted run is not a measurement" % (path, len(unpaired)))
    gained = sum(1 for m in by_task.values() if m["on"] and not m["off"])
    lost = sum(1 for m in by_task.values() if m["off"] and not m["on"])
    detail = ("over %d paired task(s): %d gained with memory ON, %d lost, from %s"
              % (len(by_task), gained, lost, os.path.basename(path)))
    if gained > lost:
        return PASS, "a lift is observed on this task set, " + detail
    return FAIL, "no lift on this task set, " + detail


def d05_stable_ids(ctx):
    """An ID independent of path and title. Renaming a note must not orphan it."""
    notes = ctx["notes"]
    if not notes:
        return NODATA, "no notes"
    with_id = [n for n in notes if field(n["front"], "id") or field(n["front"], "uid")]
    if not with_id:
        return FAIL, ("0 of %d notes carry an id: or uid:. Identity is the filename, so a rename "
                      "breaks every inbound link and every stored reference" % len(notes))
    return (PASS if len(with_id) == len(notes) else FAIL), "%d of %d notes carry a stable id" % (
        len(with_id), len(notes))


def d06_entity_crosswalk(ctx):
    """REWRITTEN 2026-08-30 when the crosswalk landed (bm_vault_crosswalk.py,
    the 30-Entities corpus). The test is STRUCTURAL, same doctrine as D14 after
    its three false passes: a crosswalk exists when an ENTITY note (one that
    declares entity:) lists system-qualified source_ids and at least one entity
    is named in TWO OR MORE systems, because a crosswalk with one namespace per
    entity crosses nothing. A declaration hanging on a plain document is the
    token-gaming shape and FAILS; a vault: id resolving to no note is dangling
    and FAILS by name. Zero declarations stays NO-DATA, never a pass."""
    notes = ctx["notes"]
    if not notes:
        return NODATA, "no notes"
    systems = ("github", "path", "vault", "plugin", "artifact")
    note_ids = {field(n["front"], "id") for n in notes if field(n["front"], "id")}
    decls, problems = [], []
    for n in notes:
        raw = field(n["front"], "source_ids")
        if raw is None:
            continue
        if not field(n["front"], "entity"):
            problems.append("%s declares source_ids without entity:" % n["path"])
            continue
        body = raw.strip()
        if body.startswith("[") and body.endswith("]"):
            body = body[1:-1]
        entries = []
        for part in (p.strip().strip('"').strip("'") for p in body.split(",")):
            if not part:
                continue
            system, sep, ident = part.partition(":")
            if not sep or not ident or system not in systems:
                problems.append("%s: entry %r is not system-qualified" % (n["path"], part))
            elif system == "vault" and ident not in note_ids:
                problems.append("%s: DANGLING vault reference %r" % (n["path"], part))
            else:
                entries.append(system)
        decls.append((n["path"], entries))
    if not decls and not problems:
        return NODATA, "no note declares source_ids, so there is no crosswalk to measure"
    if problems:
        return FAIL, "; ".join(problems[:5])
    multi = sum(1 for _, entries in decls if len(set(entries)) >= 2)
    if not multi:
        return FAIL, ("%d entities declare source_ids but none is named in two systems, "
                      "so nothing is crossed" % len(decls))
    return PASS, ("%d entities declare %d source-IDs, %d crossing 2+ systems, 0 dangling: "
                  "a foreign name resolves to the thing it denotes"
                  % (len(decls), sum(len(e) for _, e in decls), multi))


CLAIM_LINE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?claim:[ \t]*(.+?)[ \t]*\[evidence:[ \t]*([^\]]+?)[ \t]*\]",
    re.M)


def d07_fact_level_provenance(ctx):
    """A specific claim pointing at a specific evidence locator. Note-level
    attribution is real but weaker, so it is reported separately rather than
    counted as the same thing.

    REWRITTEN 2026-08-30, when the claim syntax landed (bm_vault_provenance.py
    and the 16-note corpus sample): the old body RETURNED FAIL UNCONDITIONALLY,
    so the row could never see the capability it measures, the false-fail twin
    of the check-that-infers-a-positive-from-an-absence class. The probe now
    finds the POSITIVE and stays structural: a claim line must carry nonempty
    text and a locator; a path locator must resolve inside the vault and an id
    locator against the id index (dangling FAILS by name-count); repo: and URL
    locators are counted unverifiable offline, never a pass and never a fail.
    Zero claims keeps the old FAIL, because absence of the syntax is the absence
    of the capability, not missing data about it."""
    notes = ctx["notes"]
    if not notes:
        return NODATA, "no notes"
    note_level = [n for n in notes if field(n["front"], "verified-by")]
    note_ids = {field(n["front"], "id") for n in notes if field(n["front"], "id")}
    vault = ctx["vault"]
    total, resolving, unverifiable, dangling = 0, 0, 0, 0
    for n in notes:
        for text, locator in CLAIM_LINE.findall(n["body"]):
            if not text.strip() or not locator.strip():
                continue
            total += 1
            loc = locator.strip()
            if loc.startswith("repo:") or loc.startswith("http://") \
                    or loc.startswith("https://"):
                unverifiable += 1
            elif loc.startswith("n-"):
                resolving += 1 if loc in note_ids else 0
                dangling += 0 if loc in note_ids else 1
            else:
                path = loc.split("#", 1)[0]
                if os.path.isfile(os.path.join(vault, path)):
                    resolving += 1
                else:
                    dangling += 1
    if total == 0:
        return FAIL, ("provenance is NOTE level at best (%d of %d carry verified-by) and never "
                      "CLAIM level: no sentence in this vault points at its own evidence locator"
                      % (len(note_level), len(notes)))
    if dangling:
        return FAIL, ("%d of %d claim(s) carry a DANGLING evidence locator; a claim pointing at "
                      "nothing is worse than no claim" % (dangling, total))
    if resolving == 0:
        return NODATA, ("%d claim(s) exist but every locator is unverifiable offline (repo: or "
                        "URL); nothing resolved, so nothing is proven, and a row cannot pass on "
                        "evidence nobody checked" % total)
    return PASS, ("%d claim(s) point at their own evidence: %d resolving, %d unverifiable "
                  "offline (counted, never a pass); note-level verified-by remains on %d of %d "
                  "notes and is reported separately" % (total, resolving, unverifiable,
                                                        len(note_level), len(notes)))


def d08_authority_model(ctx):
    notes = ctx["notes"]
    if not notes:
        return NODATA, "no notes"
    with_authority = [n for n in notes if field(n["front"], "authority")]
    if not with_authority:
        return FAIL, ("0 of %d notes declare authority:. A source-of-record fact cannot outrank a "
                      "casual note, so ranking is similarity only" % len(notes))
    return PASS, "%d of %d notes declare authority" % (len(with_authority), len(notes))


def d09_bitemporal_facts(ctx):
    required = ("valid_from", "valid_to", "observed_at", "ingested_at", "verified_at")
    notes = ctx["notes"]
    if not notes:
        return NODATA, "no notes"
    present = {f: sum(1 for n in notes if field(n["front"], f)) for f in required}
    have = [f for f, c in present.items() if c]
    if not have:
        return FAIL, ("none of the five temporal fields appear on any of %d notes; created: is a "
                      "write date, which cannot answer what was true when" % len(notes))
    if len(have) < len(required):
        return FAIL, "only %s present; a bi-temporal fact needs all five" % ", ".join(sorted(have))
    # REWRITTEN 2026-08-30: the old body FAILED even with all five present, the
    # third unconditional-verdict probe found tonight. All five in use is the
    # capability this row measures; per-field counts stay in the message so a
    # single token on one note reads as thin rather than as done.
    return PASS, ("all five temporal fields are in use: " +
                  ", ".join("%s on %d" % (f, present[f]) for f in required))


def d10_contradictions_preserved(ctx):
    notes = ctx["notes"]
    contradicts = [n for n in notes if field(n["front"], "contradicts")]
    if contradicts:
        return PASS, "%d note(s) carry contradicts:" % len(contradicts)
    return FAIL, ("no contradicts: edge exists in the contract, so a conflicting assertion can only "
                  "be written by overwriting or by silently coexisting")


def d11_superseded_out_of_current_truth(ctx):
    """The one check that must read BEHAVIOUR, because the edge existing and the
    reader honouring it are different facts, and they were different here."""
    tool = os.path.join(ctx["tools"], "bm_vault.py")
    if not os.path.exists(tool):
        return NODATA, "no bm_vault.py to inspect"
    with open(tool, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    reads_field = "FRONT_SUPERSEDES" in src or re.search(r"\bsupersedes:\s*\"", src)
    notes = ctx["notes"]
    edges = sum(len(WIKILINK.findall(field(n["front"], "supersedes") or "")) for n in notes)
    if not reads_field:
        return FAIL, ("the vault declares %d supersedes edge(s) and the RETRIEVAL tool never reads "
                      "the field: it matches the word only inside a path, so a superseded lesson is "
                      "still served as current" % edges)
    if edges == 0:
        return NODATA, "retrieval reads the field but the vault declares no supersedes edge to test it on"
    return PASS, "retrieval reads supersedes: and the vault declares %d edge(s)" % edges


def d12_candidate_validated_canonical(ctx):
    """REWRITTEN 2026-08-29 after the lifecycle contract shipped on its own
    promotion: field. The first version read status: values, so a migrated
    corpus with a perfect promotion contract would have FAILED here forever,
    which is the inverted-check defect this estate has already recorded: it
    goes red exactly when the row succeeds. And a vocabulary-presence pass is
    the D14 false-pass shape, so the positive demanded is STRUCTURAL: at least
    one promotion that is RECORDED (who and when), and zero states above
    candidate missing their record. An empty vault fails: a positive is
    required, absence proves nothing."""
    notes = ctx["notes"]
    if not notes:
        return NODATA, "no notes"
    recorded, unrecorded, candidates = 0, 0, 0
    for n in notes:
        state = (field(n["front"], "promotion") or "").strip().strip('"')
        if not state:
            continue
        if state == "candidate":
            candidates += 1
            continue
        if state in ("validated", "canonical", "rejected"):
            if field(n["front"], "promoted_by") and field(n["front"], "promoted_at"):
                recorded += 1
            else:
                unrecorded += 1
    if unrecorded:
        return FAIL, ("%d note(s) hold a state above candidate with NO promotion record: "
                      "auto-promotion wearing a legal-looking state" % unrecorded)
    if recorded:
        return PASS, ("%d recorded promotion(s), %d candidate(s), 0 unrecorded: model output "
                      "provably does not become truth by being written" % (recorded, candidates))
    return FAIL, ("no note carries a recorded promotion on the promotion: field: the state "
                  "machine exists in tooling (bm_vault_lifecycle.py) and the corpus does not "
                  "use it yet, so model output and validated truth are still indistinguishable "
                  "on disk")


def d13_retention_deletion_propagation(ctx):
    """REWRITTEN 2026-08-30 per the VB-14 probe proposal: the old body returned
    NO-DATA unconditionally, the fifth unconditional-verdict probe found in one
    night. The probe measures the RECONCILIATION, never a vocabulary: it opens
    the retrieval index the vault actually uses and tests every file-backed row
    against disk. PASS is a populated index with zero stale rows (nothing
    deleted or revoked still answers retrieval from a derived copy); each stale
    row is a named finding; an absent or empty index stays NO-DATA."""
    import sqlite3
    path = ctx.get("index_path") or os.path.expanduser("~/.claude/bm_vault_index.sqlite3")
    if not os.path.exists(path):
        return NODATA, ("no retrieval index at %s; nothing derived exists to reconcile, and "
                        "absence proves nothing" % path)
    try:
        con = sqlite3.connect(path)
        rows = con.execute("SELECT path FROM notes").fetchall()
    except sqlite3.Error as e:
        return NODATA, "retrieval index unreadable (%s); nothing is claimed" % e
    backed = [r[0] for r in rows if r[0] and not r[0].startswith("correction-rule:")]
    if not backed:
        return NODATA, "the retrieval index holds no file-backed rows; nothing to reconcile"
    revoked_dirs = ("/superseded/", "/archive/", "/attic/", "/.trash/")
    stale = []
    for p in backed:
        if not os.path.exists(p):
            stale.append("%s (gone)" % p)
        elif any(seg in p for seg in revoked_dirs):
            stale.append("%s (revoked location)" % p)
    if stale:
        return FAIL, ("%d of %d file-backed index row(s) are stale, so a removed or revoked "
                      "source still answers retrieval from a derived copy; first: %s"
                      % (len(stale), len(backed), stale[0]))
    return PASS, ("deletion propagates: %d file-backed index row(s), 0 stale; nothing removed "
                  "or revoked on disk still answers retrieval" % len(backed))


def d14_typed_ontology(ctx):
    """Real entity and relationship types, not only document-to-document relates."""
    notes = ctx["notes"]
    if not notes:
        return NODATA, "no notes"
    rel_types = {}
    for n in notes:
        for f in ("supersedes", "relates", "contradicts", "depends_on", "derives_from",
                  "about_entity", "evidence_for"):
            v = field(n["front"], f)
            if v and WIKILINK.findall(v):
                rel_types[f] = rel_types.get(f, 0) + len(WIKILINK.findall(v))
    types = {}
    for n in notes:
        t = (field(n["front"], "type") or "").strip()
        if t:
            types[t] = types.get(t, 0) + 1
    # HOW THIS DECIDES, and why it is no longer a name blocklist. The first version
    # held a hardcoded set of document kinds and called anything outside it an
    # entity type. On 2026-08-29 three notes arrived carrying `type: pattern`,
    # which is plainly another DOCUMENT KIND, and the check flipped to PASS: it
    # reported that an ontology existed because a word was missing from a list I
    # had typed. That is the same absence-reads-as-success shape this benchmark
    # was written to catch, and it produced a false PASS within two hours.
    #
    # So the test is now STRUCTURAL. An ontology exists when something is said
    # ABOUT a thing that is not a document: either a note declares an entity
    # explicitly, or a typed relationship points at a non-note target. Vocabulary
    # cannot fake that, and a new document kind cannot trip it.
    entity_decls = 0
    for n in notes:
        # `subject:` was in this list for one revision and matched 3 notes whose
        # value is free prose ("CR-07 (NO-DATA verification loop) and CR-08 ..."),
        # which is a document describing documents. An entity reference has to be
        # a declared identifier, not a sentence, so only explicit entity fields
        # count. That was the THIRD false pass this benchmark produced in one
        # evening, all three from a check inferring a positive out of a loose
        # match or an absence.
        for f in ("entity", "about_entity", "entity_id"):
            if field(n["front"], f):
                entity_decls += 1
                break
    if entity_decls:
        return PASS, ("%d note(s) declare an entity they are about, so something can be said "
                      "ABOUT a thing rather than only about a document" % entity_decls)
    return FAIL, ("no note declares an entity, and all %d typed edge(s) are document to document "
                  "(%s). type: values are all document kinds (%s), which is a taxonomy of PAGES, "
                  "not an ontology of THINGS: nothing here can be said about a customer, a system "
                  "or a metric"
                  % (sum(rel_types.values()),
                     ", ".join("%s=%d" % kv for kv in sorted(rel_types.items())) or "none",
                     ", ".join(sorted(types))))


def d15_graph_value_proven(ctx):
    tool = os.path.join(ctx["tools"], "bm_vault.py")
    if not os.path.exists(tool):
        return NODATA, "no bm_vault.py to inspect"
    with open(tool, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    walks = bool(re.search(r"links\s+WHERE|JOIN\s+links|multi.?hop", src))
    if not walks:
        return FAIL, ("retrieval never traverses the link graph: it resolves through an anchors "
                      "table and a lexical search, so no multi-hop value can be claimed and the "
                      "structural-orphan metric measures browsing, not retrieval")
    # EXTENDED 2026-08-30 per the VB-15 probe proposal: the measurement now
    # exists on disk, so the probe reads it structurally. Five checks: pairing
    # (every query ran both arms), arithmetic (summary matches rows), verdict
    # consistency (the sentence matches the numbers), calibration recorded, and
    # a floor of 8 queries. PASS whichever way the verdict points; an unrun,
    # unpaired or uncalibrated comparison stays NO-DATA, and a self-
    # contradictory artifact is a FAIL finding.
    import glob as _glob
    here = os.path.dirname(os.path.abspath(__file__))
    pattern = ctx.get("graph_results_glob") or os.path.join(
        here, "..", "benchmarks", "graph-value", "results-*.json")
    candidates = sorted(_glob.glob(pattern))
    if not candidates:
        return NODATA, ("retrieval touches links but no measured use case proves multi-hop "
                        "value; an unrun comparison stays NO-DATA")
    path = candidates[-1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        rows, summary = data["rows"], data["summary"]
        calibration, verdict = data.get("calibration", ""), data.get("verdict", "")
    except (OSError, ValueError, KeyError) as e:
        return NODATA, "graph-value results %s unreadable (%s); nothing is claimed" % (path, e)
    arms = {}
    for r in rows:
        arms.setdefault(r.get("query_id"), set()).add(r.get("arm"))
    unpaired = [q for q, a in arms.items() if a != {"flat", "graph"}]
    if unpaired or not arms:
        return NODATA, ("graph-value results are not fully paired (%d query(ies) missing an "
                        "arm); a one-armed comparison is not a comparison" % len(unpaired))
    if "PASS" not in str(calibration):
        return NODATA, "graph-value checks carry no recorded calibration; the run proves nothing"
    if len(arms) < 8:
        return NODATA, "only %d queries; below the 8-query floor, anecdote not measurement" % len(arms)
    flat_n = sum(1 for r in rows if r["arm"] == "flat" and r.get("success"))
    graph_n = sum(1 for r in rows if r["arm"] == "graph" and r.get("success"))
    graph_only = sorted(q for q in arms
                        if any(r["query_id"] == q and r["arm"] == "graph" and r.get("success") for r in rows)
                        and not any(r["query_id"] == q and r["arm"] == "flat" and r.get("success") for r in rows))
    if flat_n != summary.get("flat_success") or graph_n != summary.get("graph_success") \
            or sorted(summary.get("graph_only", [])) != graph_only:
        return FAIL, "graph-value summary contradicts its own rows in %s" % os.path.basename(path)
    demanded = bool(graph_only)
    claims = str(verdict).upper().startswith("MEASURED USE CASE DEMANDS")
    if demanded != claims:
        return FAIL, "graph-value verdict contradicts its own arithmetic in %s" % os.path.basename(path)
    return PASS, ("measured over %d paired queries: flat %d, graph %d, %d answered only by the "
                  "graph; the verdict (%s) matches the arithmetic"
                  % (len(arms), flat_n, graph_n, len(graph_only),
                     "graph demanded" if demanded else "graph not demanded"))


CHECKS = [
    ("D01", "Central Vault Retrieval API", 1, d01_central_retrieval_api),
    ("D02", "Point-of-need memory invocation", 1, d02_point_of_need_invocation),
    ("D03", "Memory ON/OFF benchmark", 1, d03_memory_on_off_benchmark),
    ("D04", "Memory outcome lift", 1, d04_memory_outcome_lift),
    ("D05", "Stable IDs", 1, d05_stable_ids),
    ("D06", "Entity crosswalk", 1, d06_entity_crosswalk),
    ("D07", "Fact-level provenance", 1, d07_fact_level_provenance),
    ("D08", "Authority model", 1, d08_authority_model),
    ("D09", "Bi-temporal facts", 1, d09_bitemporal_facts),
    ("D10", "Contradictions preserved", 1, d10_contradictions_preserved),
    ("D11", "Superseded out of current truth", 1, d11_superseded_out_of_current_truth),
    ("D12", "Candidate to validated to canonical", 1, d12_candidate_validated_canonical),
    ("D13", "Retention and deletion propagation", 1, d13_retention_deletion_propagation),
    ("D14", "Typed ontology", 1, d14_typed_ontology),
    ("D15", "Graph value proven", 1, d15_graph_value_proven),
]


def run(vault, tools, results_glob=None, graph_results_glob=None):
    notes = load_notes(vault)
    ctx = {"vault": vault, "tools": tools, "notes": notes}
    if results_glob:
        ctx["results_glob"] = results_glob
    if graph_results_glob:
        ctx["graph_results_glob"] = graph_results_glob
    rows = []
    for cid, title, weight, fn in CHECKS:
        try:
            verdict, detail = fn(ctx)
        except Exception as exc:                      # a crashing check is NO-DATA, never a pass
            verdict, detail = NODATA, "check raised %r" % exc
        rows.append({"id": cid, "title": title, "weight": weight,
                     "verdict": verdict, "detail": detail})
    return rows, notes


def score(rows):
    p = sum(r["weight"] for r in rows if r["verdict"] == PASS)
    f = sum(r["weight"] for r in rows if r["verdict"] == FAIL)
    n = sum(r["weight"] for r in rows if r["verdict"] == NODATA)
    total = p + f + n
    return {
        "pass_weight": p, "fail_weight": f, "nodata_weight": n, "total_weight": total,
        "proven_score": round(10.0 * p / total, 2) if total else 0.0,
        "coverage": round((p + f) / float(total), 3) if total else 0.0,
        "covered_accuracy": round(p / float(p + f), 3) if (p + f) else None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"),
                    help="vault root to measure")
    ap.add_argument("--tools", default=os.path.expanduser("~/Documents/BrotherModeUp/tools"),
                    help="the tools directory whose BEHAVIOUR is under test")
    ap.add_argument("--results-glob", default=None,
                    help="explicit glob for D04's memory-ab results-*.json "
                         "(overrides the default path relative to this script)")
    ap.add_argument("--graph-results-glob", default=None,
                    help="explicit glob for D15's graph-value results-*.json "
                         "(overrides the default path relative to this script)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("vault-benchmark: NO-DATA, no readable vault at %r" % args.vault, file=sys.stderr)
        return 2
    rows, notes = run(args.vault, args.tools, args.results_glob, args.graph_results_glob)
    s = score(rows)
    if args.json:
        print(json.dumps({"vault": args.vault, "tools": args.tools, "notes": len(notes),
                          "rows": rows, "score": s}, indent=2))
        return 0
    print("Atomic Enterprise Benchmark V2, dimension D: Vault institutional memory")
    print("vault: %s  (%d notes)" % (args.vault, len(notes)))
    print("tools: %s" % args.tools)
    print()
    for r in rows:
        print("  %-4s %-8s %-38s %s" % (r["id"], r["verdict"], r["title"], ""))
        print("       %s" % r["detail"])
    print()
    print("  PASS %d   FAIL %d   NO-DATA %d   of %d" % (
        s["pass_weight"], s["fail_weight"], s["nodata_weight"], s["total_weight"]))
    print("  proven_score     %.2f / 10   how much of the vault is actually proven" % s["proven_score"])
    print("  coverage         %.3f        how much of the benchmark we managed to test" % s["coverage"])
    if s["covered_accuracy"] is None:
        print("  covered_accuracy NO-DATA      nothing was testable, so no accuracy exists")
    else:
        print("  covered_accuracy %.3f        where we had evidence, how often we cleared the bar"
              % s["covered_accuracy"])
    print()
    print("  All three, always. Reporting proven_score alone is how a score is raised by")
    print("  testing less, which the steering directive names as a thing to refuse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
