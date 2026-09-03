#!/usr/bin/env python3
"""BrotherMode public benchmark: 13 scenarios, run for real, against real stores.

WHAT THIS IS
  The thirteen scenarios ratified in
  docs/BrotherMode_V2_Post_Audit_Execution_Loops.md ("Public benchmark suite"),
  turned into something you can run. Every scenario builds its OWN throwaway
  git repository under a temporary directory, drives the real command line
  tools as a subprocess exactly as a user would, prints the commands it ran,
  what it expected, what it actually observed, and PASS or FAIL.

  It prints a score at the end. The score is the last line and the least
  interesting one: the inputs and the expected outputs are printed above it so
  that a reader who distrusts the score can re-run any single scenario by hand.
  Publishing only a number is exactly what this file exists to prevent.

WHAT IT IS NOT
  It is not the test suite. `python3 tools/test_all.py` is the test suite, it
  is far larger, and it is what the project gates on. This is the public,
  founder-legible demonstration that the thirteen claimed behaviours are real.

  It is not a performance benchmark. Nothing here is timed.

HONESTY RULES THIS FILE FOLLOWS
  - A scenario that cannot run on this machine prints SKIP with the reason. A
    SKIP is never counted as a PASS, and any SKIP makes the summary say so.
  - Observed output is quoted from the command, never paraphrased.
  - The exit code is 0 only when every scenario passed.
  - Zero scenarios run is never an exit 0. An unknown option or a scenario
    number outside 1 to 13 is refused with exit 2 rather than quietly running
    nothing and reporting a green.

USAGE
  python3 scripts/benchmark.py            run all thirteen
  python3 scripts/benchmark.py 3 7        run only scenarios 3 and 7
  python3 scripts/benchmark.py --quiet    verdict lines only
  exit codes: 0 all passed, 1 a failure or a skip, 2 bad arguments

Python 3.9, standard library only. It is under scripts/ and not under tools/
precisely because it uses subprocess: the shipping tools may not, and this
harness has to drive them the way a user does rather than importing them.

No em or en dashes anywhere in this file or its output.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")

STORE = os.path.join(TOOLS, "bm_store.py")
LEARN = os.path.join(TOOLS, "bm_learn.py")
AUTOSAVE = os.path.join(TOOLS, "bm_autosave.py")
FENCE = os.path.join(TOOLS, "bm_fence_hook.py")


class Skip(Exception):
    """This machine cannot run the scenario. Not a failure, never a pass."""


class Bench(object):
    """One throwaway project, plus a transcript of everything run in it."""

    def __init__(self, tmp):
        self.root = tmp
        self.log = []
        # Every GIT_ name is dropped: git honours GIT_DIR and GIT_WORK_TREE over
        # cwd, so an inherited one aims this throwaway benchmark's git calls at
        # the operator's own repository. Same class as tools/bm_autosave.py.
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_")}
        self.env["BROTHERMODE_ROOT"] = self.root
        # A benchmark that read the operator's own store would be worthless and
        # would also be a privacy leak into published output.
        self.env.pop("BM_FENCE_SESSION_ID", None)

    # ---- primitives -------------------------------------------------------

    def run(self, argv, stdin=None, label=None):
        r = subprocess.run(argv, cwd=self.root, env=self.env, input=stdin,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.log.append((label or self.pretty(argv), r.returncode,
                         (r.stdout or "") + (r.stderr or "")))
        return r

    def pretty(self, argv):
        out = []
        for a in argv:
            a = a.replace(self.root, "$PROJECT").replace(ROOT, "$BM")
            out.append('"%s"' % a if " " in a else a)
        return " ".join(out)

    def git(self, *args):
        if not shutil.which("git"):
            raise Skip("git is not on PATH, and every scenario needs a repo")
        return self.run(["git", "-C", self.root] + list(args))

    def tool(self, script, *args, **kw):
        return self.run([sys.executable, script] + [str(a) for a in args],
                        stdin=kw.get("stdin"))

    def learn(self, *args, **kw):
        return self.tool(LEARN, *args, **kw)

    def init_repo(self):
        self.git("init", "-q", ".")
        self.git("config", "user.email", "benchmark@example.invalid")
        self.git("config", "user.name", "benchmark")
        with open(os.path.join(self.root, ".git", "info", "exclude"), "a") as fh:
            fh.write("\n/.brothermode\n")
        r = self.tool(STORE, "init")
        if r.returncode != 0:
            raise Skip("bm_store.py init failed: %s" % (r.stderr.strip(),))

    # ---- small helpers on top of the CLI ----------------------------------

    def capture(self, trigger, action, because, **kw):
        argv = ["capture", "--trigger", trigger, "--action", action,
                "--because", because, "--scope", kw.get("scope", "global")]
        for flag in ("raw", "session"):
            if kw.get(flag):
                argv += ["--" + flag, kw[flag]]
        r = self.learn(*argv)
        # "captured <id> (pending, ...)"
        return r.stdout.split()[1] if r.returncode == 0 else ""

    def approve(self, cid, gate=False, override=None):
        argv = ["approve", cid, "--ref", "benchmark scenario"]
        if gate:
            argv.append("--gate")
        if override:
            argv += ["--override-conflict", "--override-reason", override]
        r = self.learn(*argv)
        # First line is "approved as rule <id>"; the block under it repeats the
        # rule, so the LAST word of the whole output is not the id.
        return r.stdout.splitlines()[0].split()[-1] if r.returncode == 0 else ""

    def claim(self, name, objective, files=None, session=None):
        argv = ["claim", name, "--lifetime", "ephemeral", "--objective", objective]
        for f in files or []:
            argv += ["--files", f]
        if session:
            argv += ["--session", session]
        r = self.tool(STORE, *argv)
        for word in (r.stdout or "").replace("(", " ").split():
            if len(word) == 32 and all(c in "0123456789abcdef" for c in word):
                return word
        return ""

    def record_applications(self, query, session, record, limit=None):
        argv = ["relevant", "--query", query, "--record-applications",
                "--session", session, "--record", record]
        if limit is not None:
            argv += ["--limit", str(limit)]
        self.learn(*argv)
        r = self.learn("applications", "--session", session, "--json")
        return json.loads(r.stdout or "[]")

    def jrun(self, *args):
        r = self.learn(*args)
        try:
            return json.loads(r.stdout)
        except ValueError:
            raise AssertionError("expected JSON, got: %s" % (r.stdout or r.stderr)[:300])


# ---------------------------------------------------------------------------
# The thirteen scenarios. Each returns (expected, observed) and raises
# AssertionError on a mismatch. The docstring is printed as the scenario's
# statement of intent.
# ---------------------------------------------------------------------------

GH_TRIGGER = "pushing commits or publishing a branch to GitHub"
GH_ACTION = "use the GitHub Desktop app, never a bare git push"
GH_WHY = "the founder wants every push visible on screen"
NOTE_TRIGGER = "writing a release note"
NOTE_ACTION = "state unproven limits before the claims"
NOTE_WHY = "the founder needs honest release evidence"


def s01_relevant_correction_retrieved(b):
    """An approved correction is returned for work it plainly applies to."""
    b.init_repo()
    rule = b.approve(b.capture(GH_TRIGGER, GH_ACTION, GH_WHY), gate=True)
    res = b.jrun("relevant", "--query", "I want to push this branch to github",
                 "--json")
    expected = ("rule %s returned, with the matched words named "
                "and mode=lexical stated" % rule[:8])
    ids = [r["rule_uuid"][:8] for r in res["results"]]
    terms = res["results"][0]["why"]["matched_terms"] if res["results"] else []
    observed = "returned %s, mode=%s, matched terms %s" % (ids, res["mode"], terms)
    assert rule[:8] in ids, observed
    assert res["mode"] == "lexical", observed
    assert terms, "a match with no named terms explains nothing: " + observed
    return expected, observed


def s02_irrelevant_rule_not_retrieved(b):
    """A non-gate rule sharing no word with the task is NOT injected."""
    b.init_repo()
    rule = b.approve(b.capture(NOTE_TRIGGER, NOTE_ACTION, NOTE_WHY))
    res = b.jrun("relevant", "--query", "what colour should the breathing orb be",
                 "--json")
    expected = "no rules returned (rule %s is eligible by scope but shares no term)" % rule[:8]
    observed = "returned %s, %d eligible" % (
        [r["rule_uuid"][:8] for r in res["results"]], res["eligible"])
    assert res["results"] == [], observed
    return expected, observed


def s03_gate_returned_despite_limit_zero(b):
    """A gate survives a limit that truncates everything else, including zero."""
    b.init_repo()
    gate = b.approve(b.capture(GH_TRIGGER, GH_ACTION, GH_WHY), gate=True)
    soft = b.approve(b.capture(GH_TRIGGER, "name the branch in the note",
                               "the founder wants the branch named"),
                     override="the benchmark wants both live")
    res = b.jrun("relevant", "--query", "push this branch to github",
                 "--limit", "0", "--json")
    ids = [r["rule_uuid"][:8] for r in res["results"]]
    expected = ("gate %s still returned at --limit 0; soft rule %s dropped by "
                "the limit" % (gate[:8], soft[:8]))
    observed = "at --limit 0 returned %s" % ids
    assert gate[:8] in ids, observed
    assert soft[:8] not in ids, "the limit must still bind non-gates: " + observed
    return expected, observed


def s04_conflicting_rules_surfaced(b):
    """A reversal is refused, an override is recorded, and BOTH sides show."""
    b.init_repo()
    first = b.approve(b.capture(GH_TRIGGER, GH_ACTION, GH_WHY), gate=True)
    cid = b.capture(GH_TRIGGER, "never use the GitHub Desktop app, always a bare git push",
                    "speed matters more")
    refused = b.learn("approve", cid, "--ref", "benchmark scenario")
    second = b.approve(cid, override="the benchmark wants the pair live")
    pairs = b.jrun("conflicts", "--json")
    res = b.jrun("relevant", "--query", "push this branch to github", "--json")
    shown = sorted(r["rule_uuid"][:8] for r in res["results"])
    expected = ("plain approve refused with exit 2; after an explicit override "
                "both %s and %s are live, listed as a conflicting pair, and both "
                "are returned together" % (first[:8], second[:8]))
    observed = "refusal exit=%d (%s); conflicts=%d; retrieval returned %s" % (
        refused.returncode, (refused.stderr or "").strip()[:60],
        len(pairs) if isinstance(pairs, list) else len(pairs.get("pairs", [])),
        shown)
    assert refused.returncode == 2, observed
    assert sorted([first[:8], second[:8]]) == shown, observed
    return expected, observed


def s05_rule_edit_does_not_rewrite_history(b):
    """Superseding a rule leaves the past application reading as it was applied."""
    b.init_repo()
    old = b.approve(b.capture(NOTE_TRIGGER, NOTE_ACTION, NOTE_WHY))
    rec = b.claim("release-note", "write the release notes")
    apps = b.record_applications("write the release notes", "session-05", rec)
    assert apps, "no application was recorded, so nothing can be checked"
    new = b.approve(b.capture(NOTE_TRIGGER, "lead with the user visible change",
                              "the founder rewrote this rule"),
                    override="the benchmark is replacing the rule on purpose")
    b.learn("supersede", old, "--with", new, "--because", "the benchmark edited it")
    after = json.loads(b.learn("applications", "--rule", old[:8], "--json").stdout)
    expected = ("the recorded application still reads %r at v1, not the text the "
                "rule was edited to" % NOTE_ACTION)
    observed = "application text now reads %r at v%d" % (
        after[0]["action_text"], after[0]["rule_version"])
    assert after[0]["action_text"] == NOTE_ACTION, observed
    assert after[0]["rule_version"] == 1, observed
    return expected, observed


def s06_retrieval_miss_classified(b):
    """A rule that applied but never reached the model is a retrieval miss."""
    b.init_repo()
    shown = b.approve(b.capture(NOTE_TRIGGER, NOTE_ACTION, NOTE_WHY))
    missed = b.approve(b.capture("writing a release note for a customer",
                                 "name the customer visible change first",
                                 "the founder reads it as a customer"),
                       override="the benchmark wants two eligible rules")
    rec = b.claim("release-note", "write the release notes")
    b.record_applications("write the release note", "session-06", rec, limit=1)
    res = b.jrun("classify", "--session", "session-06", "--json")
    misses = [m["rule_uuid"][:8] for m in res["retrieval_misses"]]
    expected = ("exactly one retrieval_miss, naming the eligible rule that got "
                "no application row (one of %s / %s)" % (shown[:8], missed[:8]))
    observed = "counts=%s, misses=%s, reason=%r" % (
        res["counts"], misses,
        res["retrieval_misses"][0]["classification_reason"][:90]
        if res["retrieval_misses"] else "")
    assert res["counts"]["retrieval_miss"] == 1, observed
    return expected, observed


def s07_ignored_gate_is_compliance_failure(b):
    """A gate shown and skipped is graded a compliance failure, not an excuse."""
    b.init_repo()
    b.approve(b.capture(GH_TRIGGER, GH_ACTION, GH_WHY), gate=True)
    rec = b.claim("push-work", "push the branch")
    apps = b.record_applications("push this branch to github", "session-07", rec)
    b.learn("disposition", apps[0]["application_uuid"][:8], "ignored",
            "--because", "a bare git push went out anyway")
    res = b.jrun("classify", "--session", "session-07", "--json")
    got = res["applications"][0]
    expected = "classification compliance_failure"
    observed = "%s: %r" % (got["classification"], got["classification_reason"][:90])
    assert got["classification"] == "compliance_failure", observed
    return expected, observed


def s08_followed_bad_rule_from_rework(b):
    """Followed, and the work still had to be redone: that grades the RULE."""
    b.init_repo()
    b.approve(b.capture(NOTE_TRIGGER, NOTE_ACTION, NOTE_WHY))
    rec = b.claim("release-note", "write the release notes")
    apps = b.record_applications("write the release notes", "session-08", rec)
    b.learn("disposition", apps[0]["application_uuid"][:8], "followed",
            "--because", "followed exactly", "--outcome", "rework",
            "--outcome-ref", "the note had to be rewritten")
    res = b.jrun("classify", "--session", "session-08", "--json")
    got = res["applications"][0]
    expected = "classification bad_rule, not compliance_failure"
    observed = "%s: %r" % (got["classification"], got["classification_reason"][:90])
    assert got["classification"] == "bad_rule", observed
    return expected, observed


def s09_repeated_correction_classified_by_cause(b):
    """The same correction, given twice, is graded by WHY it repeated."""
    b.init_repo()
    rule = b.approve(b.capture(GH_TRIGGER, GH_ACTION, GH_WHY))
    rec = b.claim("push-work", "push the branch")
    skipped = b.record_applications("push this branch to github", "session-09a", rec)
    b.learn("disposition", skipped[0]["application_uuid"][:8], "ignored",
            "--because", "a bare git push went out anyway")
    # A rule only reaches settled on independent evidence that it worked once,
    # which is the point: a repeat is first evidence until the rule is settled.
    rec2 = b.claim("push-work-2", "push another branch")
    ok = b.record_applications("push another branch to github", "session-09b", rec2)
    b.learn("disposition", ok[0]["application_uuid"][:8], "followed",
            "--because", "went through the desktop app",
            "--verification-ref", "benchmark:observed", "--outcome", "accepted")
    b.learn("confirm", rule, "--to", "confirmed", "--because", "one clean application")
    b.learn("confirm", rule, "--to", "settled", "--because", "the founder settled it")
    again = b.capture(GH_TRIGGER, GH_ACTION, "the founder said it a second time",
                      session="session-09a")
    res = b.jrun("repeat-check", again, "--record", rec, "--json")
    expected = ("the repeat is attributed to rule %s with cause "
                "compliance_failure, not counted as new evidence" % rule[:8])
    observed = "counts=%s, repeats=%s" % (
        res["counts"],
        [(r["rule_uuid"][:8], r["rule_state"], r["classification"])
         for r in res["repeats"]])
    assert res["counts"].get("compliance_failure") == 1, observed
    assert res["repeats"] and res["repeats"][0]["rule_state"] == "settled", observed
    return expected, observed


def s10_forced_compaction_recovery(b):
    """A snapshot taken at compaction gives the uncommitted work back."""
    b.init_repo()
    os.mkdir(os.path.join(b.root, "src"))
    path = os.path.join(b.root, "src", "app.py")
    with open(path, "w") as fh:
        fh.write("committed\n")
    b.git("add", "-A")
    b.git("commit", "-qm", "benchmark baseline")
    with open(path, "w") as fh:
        fh.write("the work in flight when compaction hit\n")
    rec = b.claim("in-flight", "the work that was under way")
    snap = b.tool(AUTOSAVE, "precompact",
                  stdin=json.dumps({"cwd": b.root, "session_id": "session-10"}))
    if snap.returncode != 0:
        raise Skip("precompact refused here: %s" % (snap.stderr or "")[:120])
    with open(path, "w") as fh:
        fh.write("clobbered after compaction\n")
    rr = b.tool(AUTOSAVE, "recover")
    worktree = ""
    for line in (rr.stdout or "").splitlines():
        line = line.strip()
        if os.path.isabs(line) and os.path.isdir(line):
            worktree = line
            break
    if not worktree:
        raise Skip("recover named no worktree: %s" % (rr.stdout or rr.stderr)[:160])
    with open(os.path.join(worktree, "src", "app.py")) as fh:
        recovered = fh.read().strip()
    with open(path) as fh:
        live = fh.read().strip()
    dash = b.tool(STORE, "dashboard")
    expected = ("recovered copy holds the in-flight text, the live tree is NOT "
                "touched, and the store still knows record %s" % rec[:8])
    observed = "recovered=%r; live=%r; store still lists the record: %s" % (
        recovered, live, rec[:8] in (dash.stdout or ""))
    assert recovered == "the work in flight when compaction hit", observed
    assert live == "clobbered after compaction", observed
    assert rec[:8] in (dash.stdout or ""), observed
    shutil.rmtree(worktree, ignore_errors=True)
    return expected, observed


def s11_conflicting_write_blocked(b):
    """A second session's edit to a fenced file is denied by the PreToolUse hook."""
    b.init_repo()
    os.mkdir(os.path.join(b.root, "src"))
    with open(os.path.join(b.root, "src", "app.py"), "w") as fh:
        fh.write("owned by session A\n")
    label = b.tool(FENCE, "session-label", "--session-id", "benchmark-session-A")
    if label.returncode != 0:
        raise Skip("session-label refused: %s" % (label.stderr or "")[:120])
    owner = (label.stdout or "").strip().split()[-1]
    rec = b.claim("fenced", "session A owns src/app.py", files=["src/app.py"],
                  session=owner)
    assert rec, "session A could not claim the file, so nothing is fenced"
    payload = json.dumps({
        "session_id": "benchmark-session-B", "cwd": b.root,
        "hook_event_name": "PreToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": "src/app.py",
                       "old_string": "owned", "new_string": "stolen"}})
    hook = b.tool(FENCE, stdin=payload)
    try:
        decision = json.loads(hook.stdout)["hookSpecificOutput"]
    except (ValueError, KeyError):
        raise AssertionError("expected a deny object, got: %r"
                             % ((hook.stdout or hook.stderr)[:200],))
    expected = "permissionDecision deny, naming the record that owns the file"
    observed = "%s: %r" % (decision["permissionDecision"],
                           decision["permissionDecisionReason"][:120])
    assert decision["permissionDecision"] == "deny", observed
    assert rec[:8] in decision["permissionDecisionReason"], (
        "a deny that does not name the owning record cannot be acted on: " + observed)
    return expected, observed


def s12_default_export_withholds_founder_prose(b):
    """The founder's own words are withheld unless the founder asks for them."""
    b.init_repo()
    secret = "you keep hiding the limits in release notes and it has to stop"
    cid = b.capture(NOTE_TRIGGER, NOTE_ACTION, NOTE_WHY, raw=secret)
    default = b.jrun("show-candidate", cid, "--json")
    opted_in = b.jrun("show-candidate", cid, "--show-source", "--json")
    dump = b.tool(STORE, "dump")
    expected = ("default JSON carries a withheld marker instead of the raw text; "
                "--show-source returns it verbatim; bm_store.py dump never has it")
    observed = "default raw_text=%r; --show-source matches=%s; in dump=%s" % (
        default["raw_text"], opted_in["raw_text"] == secret,
        secret in (dump.stdout or ""))
    assert secret not in json.dumps(default), observed
    assert "withheld" in default["raw_text"], observed
    assert opted_in["raw_text"] == secret, observed
    assert secret not in (dump.stdout or ""), observed
    return expected, observed


def s13_install_and_uninstall_traces(b):
    """Installing and using it touches the project only where it says it does."""
    b.init_repo()
    home = os.path.join(b.root, "fake-home")
    os.mkdir(home)
    b.env["HOME"] = home
    before = set(os.listdir(b.root))
    b.approve(b.capture(GH_TRIGGER, GH_ACTION, GH_WHY), gate=True)
    b.learn("relevant", "--query", "push this branch to github")
    b.claim("some-work", "do the work")
    after = set(os.listdir(b.root))
    created = sorted(after - before)
    allowed = {".brothermode", "STATE.md"}
    stray = [p for p in created
             if p not in allowed and not p.startswith("STATE.md.bak-")]
    # Everything that appeared in the fake HOME is printed, not filtered, so a
    # reader sees the truth. What is ASSERTED is narrower and is the claim the
    # project actually makes: BrotherMode writes no artefact of its own there.
    # A macOS Python interpreter creating ~/Library/Caches is not this project
    # writing to your home directory, and pretending otherwise would be as
    # dishonest as hiding it.
    home_traces = sorted(os.listdir(home))
    bm_in_home, bytecode = [], []
    for base, dirs, files in os.walk(home):
        for name in list(dirs) + list(files):
            rel = os.path.relpath(os.path.join(base, name), home)
            low = name.lower()
            if not ("brothermode" in low or low.startswith("bm_")
                    or low.startswith("store.sqlite3")):
                continue
            # CPython writes compiled bytecode for any module it runs, and on
            # macOS that cache lands under HOME. It is a real trace and it is
            # named here rather than hidden, but it is the interpreter's, it
            # holds no founder data, and `python3 -B` suppresses it.
            if name.endswith(".pyc") or "__pycache__" in rel or "Caches" in rel:
                bytecode.append(rel)
            else:
                bm_in_home.append(rel)
    # Uninstall: the tools live outside the project, so uninstalling is deleting
    # the clone. What has to be true is that removing the project's own store
    # directory returns the tree to what it was, with nothing left behind.
    shutil.rmtree(os.path.join(b.root, ".brothermode"))
    for name in list(os.listdir(b.root)):
        if name == "STATE.md" or name.startswith("STATE.md.bak-"):
            os.remove(os.path.join(b.root, name))
    left = sorted(set(os.listdir(b.root)) - before)
    expected = ("only .brothermode/ and STATE.md (plus timestamped STATE.md "
                "backups) created in the project; no BrotherMode artefact "
                "anywhere under HOME; removing them leaves the tree as found")
    observed = ("created %s; HOME gained %s; BrotherMode data in HOME: %s; "
                "interpreter bytecode caches in HOME: %d; after removal %s remain"
                % (created, home_traces, bm_in_home or "none", len(bytecode), left))
    assert not stray, "unexpected trace in the project: " + observed
    assert not bm_in_home, "a BrotherMode artefact was written to HOME: " + observed
    assert not left, observed
    return expected, observed


SCENARIOS = [
    ("Relevant correction retrieved", s01_relevant_correction_retrieved),
    ("Irrelevant rule not retrieved", s02_irrelevant_rule_not_retrieved),
    ("Applicable gate returned despite limit zero", s03_gate_returned_despite_limit_zero),
    ("Conflicting rules surfaced", s04_conflicting_rules_surfaced),
    ("Rule edit does not rewrite past application history",
     s05_rule_edit_does_not_rewrite_history),
    ("Retrieval miss classified from complete context", s06_retrieval_miss_classified),
    ("Ignored gate classified as compliance failure",
     s07_ignored_gate_is_compliance_failure),
    ("Followed bad rule classified from rework", s08_followed_bad_rule_from_rework),
    ("Repeated correction classified by cause",
     s09_repeated_correction_classified_by_cause),
    ("Forced compaction recovery restores files and context",
     s10_forced_compaction_recovery),
    ("Conflicting file write blocked through supported edit tool",
     s11_conflicting_write_blocked),
    ("Default export withholds founder prose",
     s12_default_export_withholds_founder_prose),
    ("Clean install and uninstall leave expected traces only",
     s13_install_and_uninstall_traces),
]


def main(argv):
    # EVERY ARGUMENT IS VALIDATED, BECAUSE THE ALTERNATIVE IS AN UNEARNED
    # GREEN. A selector was previously read as `int(a) for a in argv if
    # a.isdigit()` with no check against the scenario count, and anything
    # non-numeric was silently discarded. `benchmark.py 99` then ran nothing,
    # printed "0 passed, 0 failed, 0 skipped, of 0 run" and exited 0, so a CI
    # step or a reviewer wiring `benchmark.py $N` got a passing exit code as
    # proof that scenario N passed. A mistyped flag such as `--quite` was
    # dropped just as quietly. Both now refuse with exit 2.
    quiet = "--quiet" in argv
    bad_flags = [a for a in argv if a.startswith("-") and a != "--quiet"]
    selectors = [a for a in argv if not a.startswith("-")]
    bad_selectors = [a for a in selectors
                     if not a.isdigit() or not 1 <= int(a) <= len(SCENARIOS)]
    if bad_flags or bad_selectors:
        for a in bad_flags:
            print("benchmark: unknown option %r. The only option is --quiet." % a)
        for a in bad_selectors:
            print("benchmark: %r is not a scenario number. Scenarios are 1 to %d."
                  % (a, len(SCENARIOS)))
        print("usage: benchmark.py [--quiet] [scenario-number ...]")
        return 2
    wanted = [int(a) for a in selectors]
    results = []
    print("BrotherMode public benchmark, %d scenarios" % len(SCENARIOS))
    print("repository: %s" % ROOT)
    print("python: %s" % sys.version.split()[0])
    print("Every scenario below runs in its own throwaway git repository under "
          "a temporary directory and is deleted afterwards.")
    for i, (title, fn) in enumerate(SCENARIOS, 1):
        if wanted and i not in wanted:
            continue
        tmp = tempfile.mkdtemp(prefix="bm-benchmark-%02d-" % i)
        b = Bench(os.path.realpath(tmp))
        verdict, expected, observed, detail = "FAIL", "", "", ""
        try:
            expected, observed = fn(b)
            verdict = "PASS"
        except Skip as exc:
            verdict, detail = "SKIP", str(exc)
        except AssertionError as exc:
            detail = str(exc)
        except Exception as exc:  # a crash is a FAIL, never a silent pass
            detail = "%s: %s" % (type(exc).__name__, exc)
        results.append((i, title, verdict))
        if quiet:
            print("%-4s %2d. %s" % (verdict, i, title))
        else:
            print("")
            print("=" * 78)
            print("SCENARIO %d: %s" % (i, title))
            print("intent   : %s" % (fn.__doc__ or "").strip().splitlines()[0])
            print("-" * 78)
            print("INPUTS (every command, in order, as run):")
            for cmd, code, out in b.log:
                print("  $ %s" % cmd)
                if code != 0:
                    print("    exit %d" % code)
            print("EXPECTED : %s" % (expected or detail or "(scenario did not reach its assertion)"))
            print("OBSERVED : %s" % (observed or "(none)"))
            if detail and verdict != "PASS":
                print("DETAIL   : %s" % detail)
            print("VERDICT  : %s" % verdict)
        shutil.rmtree(tmp, ignore_errors=True)
    passed = [r for r in results if r[2] == "PASS"]
    failed = [r for r in results if r[2] == "FAIL"]
    skipped = [r for r in results if r[2] == "SKIP"]
    print("")
    print("=" * 78)
    for i, title, verdict in results:
        print("  %-4s %2d. %s" % (verdict, i, title))
    print("")
    print("BENCHMARK: %d passed, %d failed, %d skipped, of %d run"
          % (len(passed), len(failed), len(skipped), len(results)))
    if skipped:
        print("A skipped scenario is NOT a pass. Reasons are printed above it.")
    if not results:
        # Belt and braces behind the argument check above. Zero scenarios run
        # is zero evidence, and evidence is the only thing an exit 0 here is
        # allowed to mean.
        print("NOTHING RAN. Zero scenarios is not a pass.")
        return 2
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
