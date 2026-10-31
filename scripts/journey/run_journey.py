#!/usr/bin/env python3
"""JT0.a: walk a journey on a FRESH INSTALL and print one row per rubric cell.

WHY IT EXISTS. Every access claim in 1.1.9 is about what a stranger meets. Measured against a fixture,
such a claim is worth nothing: the fixture is built by the same person who wrote the claim. So this
walks the real thing, installed from this tree into a throwaway sandbox, and prints PASS, FAIL or
NO-DATA per row with the atom ids that row answers for.

IT REUSES THE SMOKE'S INSTALL, IT DOES NOT INVENT A SECOND ONE. scripts/bundle-install-smoke.sh is the
estate's one-install proof and its path is copied here command for command: a throwaway
CLAUDE_CONFIG_DIR, `claude plugin marketplace add <tree>`, `claude plugin install brother@brother`,
`claude plugin list`, and the launcher found BY NAME under the installed config rather than at a
hardcoded cache path, because the host's cache layout is not this script's business. Two installs that
drift apart would mean the journey proves a different install than the smoke does.

NO CLIENT IS NO-DATA, NEVER PASS. The smoke's own words for this are "same refusal to treat a missing
client as a pass", and it exits 2 with BLOCKED. A journey that cannot be walked has not been walked,
and reporting that as a pass is the one failure this whole unit exists to prevent.

Usage:
  python3 scripts/journey/run_journey.py --journey J1 --tree . --expect A.first-screen-paths-exist,...
  python3 scripts/journey/run_journey.py --journey J1 --tree . --list

Exit 0 when every expected row is PASS. Exit 1 when an expected row is not PASS. Exit 2 when the
journey could not be walked at all (no client, unreadable row set), which is NO-DATA and never a pass.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
JOURNEYS = os.path.join(HERE, "journeys.json")
PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"
#: the smoke's own timeout shape: a client command that hangs is not a pass
STEP_TIMEOUT_S = 900


def rows_for(journey, path=JOURNEYS):
    """The declared rows for one journey, or None when the row set cannot be read. None is never an
    empty journey: a missing row set means nothing was measured, which is NO-DATA."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    entry = doc.get(journey)
    if isinstance(entry, dict):
        entry = entry.get("rows")
    if not isinstance(entry, list):
        return None
    return entry


def have_client():
    """Is there a real claude binary to install with? The smoke's own gate, in one place."""
    return shutil.which("claude") is not None


#: The catalogue name this walker installs under. Deliberately NOT the published name: installing
#: `brother@brother` resolves .claude-plugin/marketplace.json, which pins a published git tag, so the
#: walk would measure the previous release. See install_fresh's own note.
CANDIDATE_MARKETPLACE = "brother-candidate"
#: The file whose bytes identify a tree. It is generated from the whole runtime closure, so two trees
#: with the same manifest carry the same runtime. It sits at a DIFFERENT relative path in the two
#: places compared: a checkout keeps it under bundle/, and an install keeps the bundle's contents AS
#: the plugin root, so there is no bundle/ above it. Measured, after the first version of this code
#: used one constant for both and reported the candidate as unidentifiable.
RUNTIME_MANIFEST = os.path.join("runtime", "RUNTIME-MANIFEST.json")
CANDIDATE_MANIFEST = os.path.join("bundle", RUNTIME_MANIFEST)


def _sha256(path):
    """The file's hash, or "" when it cannot be read. Never raises: an unreadable canary is a
    NO-DATA the caller reports, not an exception out of an install."""
    import hashlib
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _candidate_catalogue(tree, scratch):
    """A throwaway marketplace under `scratch` whose one plugin IS the tree's own bundle.

    The client refuses an absolute local source ("source: Invalid string: must start with ./"), so
    the source is the relative "./bundle" and the bundle is reached by a link named `bundle` beside
    the catalogue. A copy is used where a link cannot be made, because correctness here matters more
    than the duplication.

    Returns (marketplace_dir, reason). reason is non empty when it could not be built.
    """
    mkt = os.path.join(scratch, "candidate-marketplace")
    meta = os.path.join(mkt, ".claude-plugin")
    bundle = os.path.abspath(os.path.join(tree, "bundle"))
    if not os.path.isdir(bundle):
        return "", "%s carries no bundle directory, so there is no candidate to install" % tree
    try:
        os.makedirs(meta, exist_ok=True)
        link = os.path.join(mkt, "bundle")
        if not os.path.exists(link):
            try:
                os.symlink(bundle, link)
            except (OSError, NotImplementedError):
                shutil.copytree(bundle, link)
        with open(os.path.join(meta, "marketplace.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": CANDIDATE_MARKETPLACE,
                       "owner": {"name": "Khalil Maaouni"},
                       "plugins": [{"name": "brother", "source": "./bundle",
                                    "description": "the candidate under test, installed from this tree",
                                    "version": "0.0.0-candidate"}]}, fh, indent=1)
    except OSError as exc:
        return "", "the candidate catalogue could not be written: %s" % exc
    return mkt, ""


def install_fresh(tree, scratch):
    """Install THIS TREE as a plugin into a throwaway sandbox under `scratch`, and answer what the
    journey needs to walk it.

    IT INSTALLS THE CANDIDATE, AND IT SAYS WHICH TREE IT INSTALLED. Until 2026-10-29 this function
    took the tree, registered it with `claude plugin marketplace add <tree>`, and then installed by
    NAME with `claude plugin install brother@brother`. That name resolves
    .claude-plugin/marketplace.json, which pins source git-subdir, url
    https://github.com/khalilmaaouni/Brother, ref v1.1.8. So the tree argument found the catalogue and
    was then never read, and every row walked described the ALREADY RELEASED version. The install's
    cache directory was literally named 1.1.8, and the installed bm_project.py carried none of a
    symbol the candidate's source and bundle copies each carried twice, in two separate brand new
    config directories. Cut gate X01 is what decides whether a tag may be pushed, so that gate was
    certifying an unbuilt candidate on evidence about its predecessor.

    So the walk now installs from a throwaway catalogue naming the tree's own bundle, and records
    `installed_root` plus `identity`, the sha256 of the installed runtime manifest. The caller
    compares that against the candidate's own manifest; a mismatch is NO-DATA and never a pass,
    because a walk of the wrong tree has measured nothing about this one. The identity is CONTENT,
    not a version string: the cache directory is named from the plugin's own manifest version, which
    is identical for a candidate and the release it was cut from, so a version comparison would pass
    on the wrong tree.

    Returns {"ok", "verdict", "config", "home", "launcher", "reason", "installed_root", "identity",
             "candidate_identity"}. ok is False with verdict NO-DATA when there is no client, when the
    install fails, or when the installed tree is not the candidate: never a pass. Nothing here raises
    on a client failure; the reason travels in the record so a row can report it.
    """
    config = os.path.join(scratch, "config")
    home = os.path.join(scratch, "home")
    for d in (config, home):
        os.makedirs(d, exist_ok=True)
    candidate_identity = _sha256(os.path.join(os.path.abspath(tree), CANDIDATE_MANIFEST))
    record = {"ok": False, "verdict": NODATA, "config": config, "home": home,
              "launcher": "", "reason": "", "installed_root": "", "identity": "",
              "candidate_identity": candidate_identity}
    if not have_client():
        record["reason"] = ("no claude binary on PATH; this journey needs a real client, so every row "
                           "it would have walked is NO-DATA and none of them is a pass")
        return record
    if not candidate_identity:
        record["reason"] = ("%s carries no readable %s, so the tree under test cannot be identified "
                            "and no walk of it can be bound to it" % (tree, CANDIDATE_MANIFEST))
        return record
    mkt, why = _candidate_catalogue(tree, scratch)
    if not mkt:
        record["reason"] = why
        return record
    env, stripped = stranger_env(CLAUDE_CONFIG_DIR=config, HOME=home)
    record["stripped_env"] = stripped
    for args, what in ((["claude", "plugin", "marketplace", "add", mkt], "marketplace add"),
                       (["claude", "plugin", "install", "brother@%s" % CANDIDATE_MARKETPLACE],
                        "plugin install"),
                       (["claude", "plugin", "list"], "plugin list")):
        try:
            done = subprocess.run(args, env=env, cwd=scratch, capture_output=True, text=True,
                                  timeout=STEP_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            record["reason"] = "%s could not run: %s" % (what, exc)
            return record
        if done.returncode != 0:
            record["reason"] = "%s exited %d: %s" % (what, done.returncode,
                                                     (done.stdout + done.stderr).strip()[:300])
            return record
    #: BY NAME, never a hardcoded cache path, which is the smoke's own rule at its line 209
    for base, _dirs, files in os.walk(config):
        if "brother-run" in files:
            record["launcher"] = os.path.join(base, "brother-run")
            break
    #: The installed root is the directory HOLDING runtime/, found by its manifest rather than by a
    #: cache path this script has no business knowing.
    for base, _dirs, files in os.walk(config):
        if os.path.basename(base) == "runtime" and "RUNTIME-MANIFEST.json" in files:
            record["installed_root"] = os.path.dirname(base)
            record["identity"] = _sha256(os.path.join(base, "RUNTIME-MANIFEST.json"))
            break
    if not record["installed_root"]:
        record["reason"] = ("the install left no %s under %s, so what was installed cannot be "
                            "identified" % (RUNTIME_MANIFEST, config))
        return record
    if record["identity"] != candidate_identity:
        record["reason"] = ("the installed tree is NOT the candidate: installed runtime manifest %s, "
                            "candidate %s. A walk of another tree has measured nothing about this one."
                            % (record["identity"][:12] or "unreadable", candidate_identity[:12]))
        return record
    record["ok"] = True
    record["verdict"] = PASS
    return record


#: THE REGRESSION GUARD CARVE OUT FOR A JOURNEY ROW, owner ruling 2026-10-26, asked for in his own
#: words as "a declared regression guard concept, the way test_plan_119's GUARDS map works".
#:
#: WHY THE MAP LIVES HERE AND NOT IN journeys.json. test_plan_119's GUARDS sits in the CHECKER, never
#: in the harness files it judges, so the thing being judged cannot write its own exemption. The same
#: logic binds here: journeys.json is the data this walker judges, so a row that could declare itself
#: a guard there would be exempting itself. The key is (journey, row id) and the value is the
#: MEASUREMENT that showed the red case is gone. A declaration with no reason, or a reason that is not
#: even text, is NOT a guard: never a silent True.
#:
#: A GUARD NEVER RESCUES A CHECK. A row that declares a `check` at all is measured by that check, and
#: declaring both is a CONTRADICTION that reads NO-DATA by name rather than a pass: that is what forces
#: the guard to be deleted once a real measurement arrives. So a failing check can never be turned
#: green by adding a guard beside it, which is the whole risk of a permissive change like this one.
#:
#: EMPTY ON PURPOSE, and this is the one entry a reader will come looking for. J2's
#: C.vacuous-single-unit-not-pass is the candidate: measured 2026-10-26, neither shape of vacuous run
#: this estate could build still reads PASS (an already green check plus a cosmetic change reads
#: UNVERIFIED, and a unit writing its own assert True test is refused before any worker starts). It is
#: NOT declared here, because whether any hollow shape still passes is PROOF1's own scope question and
#: belongs to that unit's builder. Declaring it here would close PROOF1's closing condition on the
#: builder's behalf, which is the hole this carve out must not become.
GUARDS = {}   # (journey, row id): the measurement that showed the red case no longer reproduces


def guard_reason(journey, row_id, guards=None):
    """The recorded measurement that lets this row read PASS with no check of its own, or '' when it
    is not declared, its declaration carries no reason, or that reason is not even text. Never a
    silent True.

    ONE function answers this for every caller, the selftest included: test_plan_119's own guard
    reader carries the note that an injected table judged by different code would test something the
    product does not do, and that its first version did exactly that. So the tests inject a table
    THROUGH this function rather than reimplementing the lookup."""
    table = GUARDS if guards is None else guards
    reason = table.get((journey, row_id), "")
    return reason.strip() if isinstance(reason, str) else ""


def measure(row, install, journey="", guards=None):
    """One row's verdict and its words. A row declares a `check` (argv list) run under the installed
    sandbox; exit 0 is PASS, any other exit is FAIL, and a check that cannot run at all is NO-DATA.
    A row with no check is NO-DATA by name: an undeclared measurement is not a passing one.

    A row DECLARED in GUARDS, and declaring no check of its own, reads PASS carrying the recorded
    measurement in its words, so a reader can never mistake a guard for a measurement. Declaring both
    a check and a guard is a contradiction and reads NO-DATA by name: the check is the measurement, so
    a guard beside one can never turn a red row green, and the contradiction is what forces the guard
    to be deleted once a real check arrives. Any `check` key at all counts as declaring one, malformed
    included, so a guard cannot quietly answer for a check somebody typed wrong."""
    check = row.get("check")
    declares_check = "check" in row
    guard = guard_reason(journey, str(row.get("id") or ""), guards)
    if declares_check and guard:
        return NODATA, ("this row declares both a check and a regression guard, so which one measures "
                        "it is unknown: delete the guard now that there is a check")
    if guard:
        return PASS, "regression guard: %s" % guard
    if not isinstance(check, list) or not check:
        return NODATA, "the row declares no check, so nothing measured it"
    if not install.get("ok"):
        return NODATA, install.get("reason") or "the install did not complete"
    env, _stripped = stranger_env(
        CLAUDE_CONFIG_DIR=install["config"], HOME=install["home"],
        BROTHER_JOURNEY_LAUNCHER=install.get("launcher") or "")
    try:
        done = subprocess.run(check, env=env, cwd=ROOT, capture_output=True, text=True,
                              timeout=STEP_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return NODATA, "the check could not run: %s" % exc
    said = (done.stdout + done.stderr).strip().splitlines()
    tail = said[-1][:160] if said else ""
    return (PASS if done.returncode == 0 else FAIL), tail


#: The variable families that configure BROTHER ITSELF. A stranger's machine has none of them,
#: and this session's does: measured 2026-10-29, a walk inherited BROTHERMODE_VAULT,
#: BROTHERSBE_VAULT, BROTHERMODE_REGISTRIES and BROTHERMODE_SESSION_CAP from the maintainer's
#: shell, and J5's A.setup-vault-is-recall-vault read FAIL "BROTHERMODE_VAULT set, so this would
#: measure the maintainer vault, not a stranger one". A gate that decides whether a tag may be
#: pushed must not be measuring this laptop's configuration.
#:
#: The client's own CLAUDE_* variables are LEFT ALONE deliberately. They configure the harness
#: that installs the plugin, not the plugin, and they carry the sign in this walk needs; stripping
#: them would turn every row NO-DATA, which is a worse answer than a real one.
STRANGER_STRIPS = ("BROTHERMODE_", "BROTHERSBE_", "BROTHERDS_", "BROTHER_")


def stranger_env(**overrides):
    """(the environment a stranger's shell would have, the sorted names removed from this one).

    Overrides are applied AFTER the strip, so a variable this walker sets on purpose (the config
    dir, the home, the launcher) survives even when its name is in a stripped family."""
    removed = sorted(k for k in os.environ
                     if k.startswith(STRANGER_STRIPS) and k not in overrides)
    env = {k: v for k, v in os.environ.items() if k not in removed}
    env.update({k: v for k, v in overrides.items()})
    return env, removed


def host_name():
    """The host this walk ran on, as the cut gate reads it from the record: "macos", "ubuntu", or the plain
    system name when it is neither. RDM1.b refuses any record that is not "macos" for the macOS half."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        return "ubuntu" if "ubuntu" in platform.version().lower() else "linux"
    return system.lower() or "unknown"


#: RDM1 (2026-10-29): the seat that walked, read from this variable so a human or a lane can name itself. It
#: defaults to the walker's own name; the hand re-measure (cut_preflight's hand-re-measure row) must come from
#: a different seat than this one.
WALKED_BY_ENV = "BROTHER_JOURNEY_WALKED_BY"
WALKER_NAME = "run_journey.py"


def claude_bin_entries(home):
    """What the walk's HOME held under .claude/bin: the entries' paths, [] when it holds none. A directory that
    cannot be listed is reported as one unreadable entry, never as clean: unknown BLOCKS (RDM1.b)."""
    if not home:
        return []
    path = os.path.join(home, ".claude", "bin")
    if not os.path.lexists(path):
        return []
    try:
        return sorted(os.path.join(path, name) for name in os.listdir(path)) or [path]
    except OSError as exc:
        return ["%s (unreadable: %s)" % (path, type(exc).__name__)]


def write_record(path, journey, rows, install, clean_home):
    """Write the record the cut chain reads, and bind it to the tree that was walked.

    WHY. X01 is read by the cut chain, never by whoever ran the walk. Before this the walker printed
    rows to stdout and wrote nothing, so go_no_go_119.py's journeys-fresh row answered "no journey
    result file matches docs/plan/JOURNEY-*.json" and the gate stayed NO-DATA however often the
    journey was walked.

    clean_home is FORCED FALSE when the installed identity is not the candidate's. A record whose
    rows describe another tree must not be able to claim a clean walk of this one: that is the same
    hole install_fresh had, moved one step downstream, and it is the one a stale record from an
    earlier candidate would slip through.
    """
    identity = install.get("identity") or ""
    candidate = install.get("candidate_identity") or ""
    bound = bool(identity) and identity == candidate
    claude_bin = claude_bin_entries(install.get("home") or "")
    doc = {
        "journey": journey,
        "rows": dict(rows or {}),
        # RDM1: a HOME that held a .claude/bin is not a stranger's, so it cannot claim a clean walk.
        "clean_home": bool(clean_home) and bound and not claude_bin,
        "host": host_name(),
        "claude_bin": claude_bin,
        "walked_by": os.environ.get(WALKED_BY_ENV, "").strip() or WALKER_NAME,
        "identity": identity,
        "candidate_identity": candidate,
        "installed_root": install.get("installed_root") or "",
        "walked_unix": int(time.time()),
        "reason": install.get("reason") or "",
        # The Brother configuration this machine had and a stranger's would not, removed before
        # the walk. Recorded rather than merely done, so the record says what it was cleared of.
        "stripped_env": list(install.get("stripped_env") or []),
    }
    if not bound:
        doc["reason"] = (doc["reason"] or
                         "the installed tree is not the candidate (installed %s, candidate %s), so "
                         "these rows describe another tree and this record claims no clean walk"
                         % (identity[:12] or "unreadable", candidate[:12] or "unreadable"))
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        return "", "the journey record could not be written: %s" % exc
    return path, ""


def run_journey(journey, tree, expect, out=None, record_path=""):
    """Walk one journey and print a row per rubric cell. Returns 0 when every id in `expect` is PASS,
    1 when one is not, and 2 when the journey could not be walked. An id in `expect` that the row set
    does not declare is a FAILURE of this call, never a silent omission."""
    out = out or sys.stdout
    rows = rows_for(journey)
    if rows is None:
        out.write("%s %s: the row set could not be read\n" % (NODATA, journey))
        return 2
    want = [e for e in (expect or []) if e]
    declared = [r.get("id") for r in rows]
    missing = [e for e in want if e not in declared]
    if missing:
        out.write("%s %s: expected rows this journey does not declare: %s\n"
                  % (FAIL, journey, ", ".join(missing)))
        return 1
    scratch = tempfile.mkdtemp(prefix="brother-journey-")
    try:
        install = install_fresh(tree, scratch)
        if not install["ok"]:
            out.write("%s install: %s\n" % (NODATA, install["reason"]))
        verdicts = {}
        for row in rows:
            rid = row.get("id") or "(unnamed row)"
            atoms = ",".join(row.get("atoms") or []) or "no atom ids"
            verdict, said = measure(row, install, journey)
            verdicts[rid] = verdict
            out.write("%-8s %-34s %-28s %s\n" % (verdict, rid, atoms, said))
        # RDM1.b (Linux half): the run log is the ubuntu record, so it names the tree it walked, last, where a
        # tail of the log keeps it. cut_driver binds the log to the candidate by these two hashes.
        out.write("identity candidate=%s installed=%s\n"
                  % (install.get("candidate_identity") or "unreadable", install.get("identity") or "unreadable"))
        if record_path:
            written, why = write_record(record_path, journey, verdicts, install,
                                        clean_home=install.get("ok", False))
            out.write("%s record: %s\n" % ("wrote" if written else NODATA, written or why))
        bad = [r for r in want if verdicts.get(r) != PASS]
        if not want:
            return 2 if not install["ok"] else 0
        if bad:
            out.write("%s %s: expected PASS and did not get it: %s\n" % (FAIL, journey, ", ".join(bad)))
            return 1
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def selftest(out=None):
    """Prove this walker reaches ALL THREE verdicts, which is what makes it evidence rather than a
    green: a check that exits 0 is PASS, a check that exits non zero is FAIL, and a row with no check
    is NO-DATA. A measurer only ever seen saying one of the three is not known to say the others.
    Proven with the shell's own true and false so the proof depends on nothing in this repository."""
    out = out or sys.stdout
    installed = {"ok": True, "verdict": PASS, "config": tempfile.gettempdir(),
                 "home": tempfile.gettempdir(), "launcher": "", "reason": ""}
    cases = [
        ("a check that exits 0 is PASS", {"id": "t", "check": ["true"]}, installed, PASS),
        ("a check that exits non zero is FAIL", {"id": "t", "check": ["false"]}, installed, FAIL),
        ("a row with no check is NO-DATA", {"id": "t"}, installed, NODATA),
        ("a row with an empty check is NO-DATA", {"id": "t", "check": []}, installed, NODATA),
        ("a check that is not a list is NO-DATA", {"id": "t", "check": "true"}, installed, NODATA),
        ("a check that cannot run is NO-DATA", {"id": "t", "check": ["/nonexistent/binary"]},
         installed, NODATA),
        ("every row is NO-DATA when the install did not complete", {"id": "t", "check": ["true"]},
         {"ok": False, "verdict": NODATA, "config": "", "home": "", "launcher": "",
          "reason": "no claude binary on PATH"}, NODATA),
    ]
    #: THE REGRESSION GUARD CASES (owner ruling 2026-10-26). One case per guard, each able to fail on
    #: its own, and the table is injected THROUGH guard_reason so these judge the product's own lookup
    #: rather than a copy of it. want_words is the substring the printed line must carry, because a
    #: guard that reads PASS without saying it is a guard is indistinguishable from a measurement.
    MEASURED = "measured 2026-10-26: the stated red case no longer reproduces"
    guard_cases = [
        ("a declared guard with a reason reads PASS",
         "J2", {"id": "r"}, {("J2", "r"): MEASURED}, PASS, "regression guard"),
        ("a declared guard names itself a guard in its words",
         "J2", {"id": "r"}, {("J2", "r"): MEASURED}, PASS, MEASURED),
        ("a guard declared for ANOTHER journey does not apply to this row",
         "J2", {"id": "r"}, {("J4", "r"): MEASURED}, NODATA, "no check"),
        ("a guard whose reason is empty is NOT a guard",
         "J2", {"id": "r"}, {("J2", "r"): "   "}, NODATA, "no check"),
        ("a guard whose reason is not text is NOT a guard, never a silent True",
         "J2", {"id": "r"}, {("J2", "r"): True}, NODATA, "no check"),
        ("a row that declares a check AND a guard is a contradiction, never a pass",
         "J2", {"id": "r", "check": ["true"]}, {("J2", "r"): MEASURED}, NODATA, "both a check and"),
        ("a guard NEVER rescues a failing check",
         "J2", {"id": "r", "check": ["false"]}, {("J2", "r"): MEASURED}, NODATA, "both a check and"),
        ("a malformed check plus a guard is still the contradiction, not a pass",
         "J2", {"id": "r", "check": "true"}, {("J2", "r"): MEASURED}, NODATA, "both a check and"),
        ("an UNDECLARED row with no check is still NO-DATA",
         "J2", {"id": "r"}, {}, NODATA, "no check"),
    ]
    bad = []
    for why, row, install, want in cases:
        got, _said = measure(row, install)
        if got != want:
            bad.append("%s: wanted %s, got %s" % (why, want, got))
    for why, journey, row, guards, want, want_words in guard_cases:
        got, said = measure(row, installed, journey, guards)
        if got != want:
            bad.append("%s: wanted %s, got %s" % (why, want, got))
        elif want_words not in said:
            bad.append("%s: the words %r are not in %r" % (why, want_words, said))
    #: THE LIVE TABLE'S OWN SHAPE. A future entry that is a bare True would be a guard nobody wrote a
    #: measurement for, and the reader above refuses it, so this asserts the refusal holds over the
    #: REAL table rather than only over injected ones.
    for key, reason in GUARDS.items():
        if not (isinstance(key, tuple) and len(key) == 2 and all(isinstance(k, str) for k in key)):
            bad.append("the live GUARDS table has a key that is not a (journey, row id) pair: %r" % (key,))
        elif not guard_reason(key[0], key[1]):
            bad.append("the live GUARDS entry %r carries no usable measurement (%r), so it is not a "
                       "declared guard and the row it names reads NO-DATA" % (key, reason))
    #: AN EXPECTED ROW THE JOURNEY DOES NOT DECLARE IS A FAILURE OF THE CALL, never a silent omission:
    #: a caller asking for a row that does not exist has been told nothing, and reading that as success
    #: is how a cut gate passes on a rubric cell nobody measured. Checked BEFORE any install, so this
    #: costs nothing and needs no client.
    import io as _io
    sink = _io.StringIO()
    if run_journey("J1", ".", ["no.such.row"], out=sink) != 1:
        bad.append("an expected row the journey does not declare must fail the call")
    if "no.such.row" not in sink.getvalue():
        bad.append("the failure must name the row it could not find")
    rows = rows_for("J1")
    if rows is None:
        bad.append("J1's own row set could not be read")
    elif not all(isinstance(r.get("id"), str) and r.get("id") for r in rows):
        bad.append("a J1 row carries no id")
    #: THE FLAG TWELVE DONE CHECKS NAME (D3, 2026-10-25). Every walk installs fresh, so --fresh-install
    #: changes nothing and exists so a done check can say out loud what the walk does. It is ACCEPTED
    #: rather than left unknown because argparse refuses an unknown flag with exit 2, and twelve of the
    #: plan's done checks pass it, so without this they cannot run at all. Two properties, two guards:
    #: that it parses, and that it changes no part of the answer.
    import contextlib as _ctx
    sink_bare, sink_flag = _io.StringIO(), _io.StringIO()
    with _ctx.redirect_stdout(sink_bare):
        bare = main(["--journey", "J1", "--list"])
    try:
        with _ctx.redirect_stdout(sink_flag):
            flagged = main(["--journey", "J1", "--list", "--fresh-install"])
    except SystemExit as exc:          # an unknown flag exits 2; reported, never a crash out of here
        flagged, _ = "refused with exit %s" % exc.code, sink_flag.write("")
    if flagged != 0:
        bad.append("--fresh-install is not accepted (%s), so the twelve done checks that pass it "
                   "cannot run at all" % flagged)
    elif (bare, sink_bare.getvalue()) != (flagged, sink_flag.getvalue()):
        bad.append("--fresh-install changed the walk, and it is specified as a no op: the walker "
                   "already installs fresh on every journey")
    for line in bad:
        out.write("FAIL %s\n" % line)
    out.write("selftest: %d case(s) including %d regression guard case(s), plus the undeclared row "
              "guard, the live GUARDS shape and the no op flag guards, %s\n"
              % (len(cases) + len(guard_cases), len(guard_cases),
                 "OK" if not bad else "%d FAILED" % len(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--journey", default="J1", help="J1, J2, J4, J5")
    ap.add_argument("--tree", default=ROOT, help="the tree to install (path mode); defaults to this repo")
    ap.add_argument("--expect", default="", help="comma separated row ids that must read PASS")
    #: D3 (2026-10-25): ACCEPTED AND DELIBERATELY A NO OP. Twelve units' done checks pass
    #: --fresh-install, and this walk always installs this tree into a throwaway sandbox first, so the
    #: flag declares what already happens rather than selecting it. Accepting it is what makes those
    #: twelve done checks runnable as the plan writes them; argparse refuses an unknown flag with exit
    #: 2, which is why their absence was not a cosmetic gap. Its no op-ness is a selftest case, so a
    #: later edit cannot quietly give it behaviour.
    ap.add_argument("--fresh-install", action="store_true",
                    help="declare that this walk installs the tree fresh first, which it always does; "
                         "accepted as a no op so a done check can say so out loud")
    ap.add_argument("--record", default="",
                    help="write the walk's result here, bound to the installed tree's identity, so "
                         "the cut chain can read it (docs/plan/JOURNEY-<journey>.json)")
    ap.add_argument("--list", action="store_true", help="print the declared rows and exit 0")
    ap.add_argument("--selftest", action="store_true",
                    help="prove this walker reaches PASS, FAIL and NO-DATA")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.list:
        rows = rows_for(args.journey)
        if rows is None:
            print("%s %s: the row set could not be read" % (NODATA, args.journey))
            return 2
        for row in rows:
            print("%-34s %s" % (row.get("id"), ",".join(row.get("atoms") or [])))
        return 0
    return run_journey(args.journey, args.tree, args.expect.split(","),
                       record_path=args.record)


if __name__ == "__main__":
    sys.exit(main())
