#!/usr/bin/env python3
"""BrotherSBE weekly code-graded checks (Anthropic eval guidance: many small
code-graded checks; LLM judgment only for the residue). Reads the telemetry
ledger and fence registries; prints PASS / FAIL / NO-DATA per check with the
evidence inline. Advisory by default (exit 0); `--strict` exits nonzero on any
FAIL, and on a crash, so CI can block. Honest outputs only: a check without data
says NO-DATA, never PASS.

Every check is registered in CHECKS with a declaration of the evidence it opens
(see sbe_checks.py). Three of these checks used to read `PASS if not <empty list>`,
so a fresh checkout with an empty ledger printed "0 pre-schema-2 lines remain PASS"
and two more like it: the project's headline law inverted inside the project's own
scorer, in its default state. A corpus with no rows supports no verdict about
compliance, so those checks now say NO-DATA and name the file they found nothing in.
"""
import collections, json, os, sys, datetime, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_telemetry import (VAULT, TEL_DIR, LEDGER, RATINGS, REVIEWS, CORRECTIONS, SESSIONS_GLOB,
                          OPERATOR_MODEL, age_days, prediction_counts,
                          real_sessions)
from sbe_checks import (Check, run_guarded, answered, stated, vacuous, all_vacuous, derivation_fold,
                        distinct, Pruner, evidence_problem, glob_with_denials, numeric,
                        one_line, say, without_comments)

# Fence registries: the STATE.md files whose fence lines the hygiene checks
# read. Point BROTHERSBE_REGISTRIES at your own projects as colon-separated
# glob patterns, for example:
#   export BROTHERSBE_REGISTRIES="$HOME/work/*/STATE.md:$HOME/work/*/.plans/**/*STATE*.md"
# Unset, the registry checks report NO-DATA instead of guessing at paths.
# Discovery accounts for what it could not see: glob returns FEWER paths, not
# an error, over a directory it cannot enter, so one chmod on a registry's
# PARENT removed its files from this list in silence and the per-file ACCESS
# check was never asked about them. The denials ride beside the hits and the
# fence checks FAIL over them by name, exactly as they do for an unreadable
# registry file.
REGISTRIES = []
REGISTRY_DENIALS = []
# os.pathsep, not a literal colon: on Windows a drive path like C:\x would
# split at its own drive colon and match nothing, reading NO registry and
# reporting NO-DATA (proven live by the windows-latest leg, run 31040612827).
for _pat in os.environ.get("BROTHERSBE_REGISTRIES", "").split(os.pathsep):
    if _pat.strip():
        _hits, _denied = glob_with_denials(_pat.strip())
        REGISTRIES.extend(_hits)
        REGISTRY_DENIALS.extend(_denied)

CACHE_RATIO_FLOOR = 90.0

# Derived from VAULT, exactly like LEDGER, RATINGS, REVIEWS and CORRECTIONS, and
# for the same reason: a cold-start receipt is accumulated evidence rows, the
# same class as those four, so it lives where they live and inherits machinery
# that already works.
#
# This was a repository-root path for one round and the honesty sweep caught it.
# The sweep relativizes a check's declared evidence against VAULT and re-roots
# VAULT into a throwaway directory; a path derived from the repository root does
# not move when the vault does, so every malformed-receipt scenario was silently
# graded against the real absent file and a broken record reported NO-DATA. A
# broken record is a broken claim, never an absence, so the placement was wrong
# rather than the check.
COLDSTART = os.path.join(TEL_DIR, "coldstart.jsonl")

# Empty until a baseline run exists. Declaring a threshold before measuring one
# would be an appetite dressed as a measurement, so the check reports NO-DATA
# while this is empty rather than inventing a bar to clear.
# Set 2026-08-11 from the first completed baseline: 5 of 5 headless runs at
# 724b4eb finished the governed change, turns_to_first_artifact 19/26/28/33/39
# (median 28). Each limit is the baseline's ceiling plus headroom for variance,
# so it catches onboarding getting materially worse, never noise.
# context_bytes_at_start is deliberately ABSENT: the baseline read 0 on all
# five runs, which is exactly the suspiciously perfect score that metric's own
# design warning names (a broken feed scores perfect), so it is under
# investigation and nothing is asserted about it.
COLDSTART_THRESHOLDS = {
    "turns_to_first_artifact": 45,
    "commands_typed": 0,
    "internal_docs_opened": 0,
}

# Four checks compared an age with <= or > and none of them had a floor, so a
# single future-dated row defeated every staleness rule in this file at once:
# `review-cadence` printed "last review: -1620.2d ago" and called it a PASS, the
# genuinely stale review in the same ledger was masked because max() picked the
# future row, and a registry file touched with a future mtime was fresh forever.
# A timestamp in the future is not a fresh record, it is a BROKEN record, and this
# project's own rule for a broken record is that it FAILs and names itself.
AGE_IS_BROKEN = ("a timestamp in the future is not an age; a record dated after now is a broken "
                 "record, and staleness measured against it would be measured against nothing")


class Ledger:
    """A JSONL evidence file, read so that its three states stay distinguishable.

    sbe_telemetry.read_jsonl skips a line it cannot parse, which is right for the
    hook write path (one corrupt line must not lose the rest) and wrong for a
    check: a ledger of unparseable lines read as a ledger of zero lines, and zero
    lines read as compliance. Here, absent is NO-DATA, present-and-empty is
    NO-DATA naming the file, and present-and-unreadable is a FAIL, because a
    broken record is a broken claim and not an absence.
    """

    def __init__(self, path):
        self.path = path
        self.exists = os.path.lexists(path)
        self.rows = []
        self.errors = []
        # The ACCESS axis, before any open(): a ledger path that is a chmod 000
        # file, a directory, or a FIFO is present and unreadable, and "no
        # outcomes.jsonl, so nothing was opened" printed over it is a false
        # sentence (a FIFO also hangs the open forever). Present-and-unreadable
        # is a broken record, which FAILs by the rule two lines down.
        problem = evidence_problem(path)
        if problem:
            self.errors.append("%s is %s" % (path, problem))
            return
        if not self.exists:
            return
        try:
            raw_lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError as e:
            self.errors.append("%s cannot be read (%s)" % (path, type(e).__name__))
            return
        for i, raw in enumerate(raw_lines, 1):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                self.errors.append("%s:%d is not valid JSON" % (os.path.basename(path), i))
                continue
            if not isinstance(obj, dict):
                self.errors.append("%s:%d is a JSON %s, not an object"
                                   % (os.path.basename(path), i, type(obj).__name__))
                continue
            # A session_id is the key every session-shaped row is deduplicated
            # by. A row whose session_id is an object or a list is unhashable and
            # raised inside the shared context, outside the per-check guard, which
            # collapsed all eleven checks into one error line at exit 0. A row
            # whose session_id is "" or " " is worse than that: it is hashable, so
            # it silently merged with every other blank-id row and the coverage
            # check counted them as one session. A session_id of "TODO" or "-" is
            # the same merge with a value that reads as deliberate, so it goes
            # through answered() rather than stated(). All of these are broken
            # records, and this is where a broken record is named.
            if "session_id" in obj and answered(obj.get("session_id")) is None:
                self.errors.append("%s:%d records session_id %r, which is not a session identifier"
                                   % (os.path.basename(path), i, obj.get("session_id")))
                continue
            if "session_id" in obj and not isinstance(obj["session_id"], str):
                self.errors.append("%s:%d records session_id of type %s, not a string"
                                   % (os.path.basename(path), i, type(obj["session_id"]).__name__))
                continue
            self.rows.append(obj)

    def problem(self, what):
        """(verdict, evidence) when this ledger cannot support a verdict, else None."""
        if self.errors:
            return "FAIL", ("%s cannot be scored: %s. A ledger that cannot be read is a broken "
                            "record, not an absent one" % (what, "; ".join(self.errors[:4])))
        if not self.exists:
            return "NO-DATA", ("no %s, so nothing was opened and there is no %s to report on"
                               % (self.path, what))
        if not self.rows:
            return "NO-DATA", ("%s exists and holds no lines, so %s was measured over zero rows; "
                               "a corpus with nothing in it supports no verdict" % (self.path, what))
        return None


class Ctx:
    """Everything the checks read, resolved LAZILY, so a check takes an argument
    instead of reaching for a module global. The meta-test builds one of these
    against a throwaway vault.

    Lazy on purpose, and this is the whole point of the class. Reading every
    evidence file in __init__ put the read outside the per-check guard: one
    malformed ledger line raised while the context was being built, so eleven
    verdict lines became a single error line and advisory mode exited 0. That is
    the absent-check defect in its purest form, in the file whose docstring says
    it was closed. Now each field is opened on first use, inside the guard of the
    check that asked for it, so a broken ledger FAILs the four checks that read
    the ledger and the other seven still run and still print.
    """

    def __init__(self):
        self._cache = {}

    def _once(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    @property
    def ledger(self):
        return self._once("ledger", lambda: Ledger(LEDGER))

    @property
    def ratings(self):
        return self._once("ratings", lambda: Ledger(RATINGS))

    @property
    def reviews(self):
        return self._once("reviews", lambda: Ledger(REVIEWS))

    @property
    def corrections(self):
        return self._once("corrections", lambda: Ledger(CORRECTIONS))

    @property
    def coldstart(self):
        return self._once("coldstart", lambda: Ledger(COLDSTART))

    @property
    def registries(self):
        return REGISTRIES

    @property
    def registry_denials(self):
        return REGISTRY_DENIALS

    @property
    def sessions(self):
        return self._once("sessions", lambda: real_sessions(self.ledger.rows))

    @property
    def recent(self):
        return self._once("recent", lambda: [
            r for r in self.sessions
            if 0 <= (age_days(r.get("ts", "")) if age_days(r.get("ts", "")) is not None else 99) <= 7])

    @property
    def undated(self):
        """Session rows whose timestamp no age can be computed from.

        `ctx.recent` maps an unreadable timestamp to the sentinel 99, which
        means "old", so 99 rows with garbage timestamps silently left the
        7-day window and "1/1 sessions >= 90% warm-read; below floor: none"
        was printed over a ledger 99 of whose 100 rows nothing had measured.
        check_correction_latency already knew this ("an age that could not be
        computed is not an age of zero"); its siblings now read the same rule.
        """
        return self._once("undated", lambda: [
            r for r in self.sessions if age_days(r.get("ts") or "") is None])

    @property
    def future(self):
        """Rows dated after now. An age below zero is not an age; see AGE_IS_BROKEN."""
        return self._once("future", lambda: [
            r for r in self.sessions
            if (age_days(r.get("ts", "")) or 0) < 0])

    @property
    def days(self):
        def build():
            out = {}
            for r in self.recent:
                out[r.get("ts", "")[:10]] = out.get(r.get("ts", "")[:10], 0) + 1
            return out
        return self._once("days", build)


# --------------------------------------------------------------------------
# The checks. One function each, (verdict, evidence) out, no printing.
# --------------------------------------------------------------------------

def check_ledger_coverage(ctx):
    bad = ctx.ledger.problem("session coverage")
    if bad:
        return bad
    if ctx.undated:
        return "FAIL", ("%d session row(s) carry no readable timestamp (%s), so their age was "
                        "never measured and coverage cannot be counted over them"
                        % (len(ctx.undated),
                           ", ".join(repr(r.get("ts")) for r in ctx.undated[:4])))
    if ctx.future:
        return "FAIL", ("%d session row(s) are dated in the future (%s): %s"
                        % (len(ctx.future), ", ".join(repr(r.get("ts")) for r in ctx.future[:4]),
                           AGE_IS_BROKEN))
    if not ctx.recent:
        return "NO-DATA", ("%d ledger line(s), none in the last 7d, so coverage was measured over "
                           "zero recent sessions" % len(ctx.ledger.rows))
    return "PASS", "%d sessions across %d active days last 7d" % (len(ctx.recent), len(ctx.days))


def check_schema_uniform(ctx):
    bad = ctx.ledger.problem("schema uniformity")
    if bad:
        return bad
    old = [r for r in ctx.ledger.rows if r.get("schema") != 2]
    if old:
        return "FAIL", "%d of %d lines are pre-schema-2" % (len(old), len(ctx.ledger.rows))
    return "PASS", "%d ledger line(s) read, 0 pre-schema-2 lines remain" % len(ctx.ledger.rows)


def check_cache_economy(ctx):
    bad = ctx.ledger.problem("cache economy")
    if bad:
        return bad
    if ctx.undated:
        return "FAIL", ("%d session row(s) carry no readable timestamp (%s), so they fell out of "
                        "the 7-day window unmeasured, and a ratio over the rest would read as "
                        "covering them" % (len(ctx.undated),
                                           ", ".join(repr(r.get("ts")) for r in ctx.undated[:4])))
    flagged, measured, unusable = [], 0, []
    for r in ctx.recent:
        cr, cw = r.get("cache_read", 0), r.get("cache_write", 0)
        # A cache counter is a number. Left unchecked, "" and None reached the
        # arithmetic and the check died inside the guard, which is honest but
        # unreadable; a value that is not a count is a broken field and says so.
        # The shared numeric(), not a hand isinstance test: json.load accepts
        # bare Infinity, `cr + cw > 0` is True over one, the ratio is NaN, and
        # `NaN < floor` is False, so an infinite counter bought "1/1 sessions
        # >= 90% warm-read". An infinity is not a measurement (sbe_checks).
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or numeric(v) is None for v in (cr, cw)):
            unusable.append("%s (%r/%r)" % (str(r.get("session_id", "?"))[:8], cr, cw))
            continue
        if cr + cw > 0:
            measured += 1
            ratio = 100.0 * cr / (cr + cw)
            if ratio < CACHE_RATIO_FLOOR:
                flagged.append("%s %.0f%%" % (r.get("session_id", "?")[:8], ratio))
    if unusable:
        return "FAIL", ("%d session(s) record cache fields that are not counts (%s), so the warm-read "
                        "ratio could not be computed over them"
                        % (len(unusable), ", ".join(unusable[:4])))
    if not measured:
        return "NO-DATA", "no sessions with cache fields last 7d"
    unmeasured = len(ctx.recent) - measured
    return ("PASS" if not flagged else "FAIL",
            "%d/%d sessions >= %.0f%% warm-read; below floor: %s%s"
            % (measured - len(flagged), measured, CACHE_RATIO_FLOOR, flagged or "none",
               "" if not unmeasured else
               "; %d recent session(s) carry no cache fields and were not measured, so this "
               "ratio does not cover them" % unmeasured))


def check_vault_log_per_active_day(ctx):
    bad = ctx.ledger.problem("session logs per active day")
    if bad:
        return bad
    if not ctx.days:
        return "NO-DATA", ("no active days in the last 7d, so no day was checked for a session log; "
                           "'no missing logs' over zero days is not compliance")
    # filename date OR mtime date counts, so a dated backfill note clears an old miss
    # Discovery first: a Sessions directory that exists and cannot be entered
    # hides the very log that would clear a day this check is about to name,
    # and glob returns fewer files rather than an error over it.
    session_files, denied = glob_with_denials(SESSIONS_GLOB)
    if denied:
        return "FAIL", ("%d session folder(s) exist and cannot be entered (%s); a log inside one "
                        "would change this verdict, so no day is reported over a set discovery "
                        "silently truncated" % (len(denied), ", ".join(denied[:4])))
    log_days, blank, logs = set(), [], 0
    for f in session_files:
        # ACCESS first: a chmod 000 log, a directory wearing a log's name, or a
        # FIFO is not a log this check read, and opening a FIFO would hang here.
        if evidence_problem(f):
            blank.append("%s (%s)" % (os.path.basename(f), evidence_problem(f)))
            continue
        try:
            # A zero-byte file, a file holding only its own headings, and a file
            # whose every line under those headings says TODO are all not a
            # session log. Counting any of them let `touch` satisfy the day and
            # the check then reported "0 days without any log" over a file with
            # nothing written in it.
            body = open(f, encoding="utf-8", errors="replace").read()
            # The rendered body: a log whose whole content sits inside an HTML
            # comment renders as nothing, and its comment lines used to count
            # as content here while all_vacuous stripped them two lines up.
            rendered = without_comments(body)
            if all_vacuous(body) or not [l for l in rendered.splitlines()
                                         if l.strip() and not l.lstrip().startswith("#")]:
                blank.append(os.path.basename(f))
                continue
        except OSError:
            blank.append(os.path.basename(f))
            continue
        logs += 1
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if m:
            log_days.add(m.group(1))
        try:
            # The UTC date, because the active days it is compared against come
            # from UTC timestamps: a local-time date here made a log written near
            # midnight cover the wrong day in some timezones and no day in others.
            log_days.add(datetime.datetime.fromtimestamp(
                os.path.getmtime(f), datetime.timezone.utc).date().isoformat())
        except OSError:
            continue  # sbe: allow-silent the log was already counted by its filename date; a file whose mtime vanished mid-check contributes no second date
    missing = sorted(d for d in ctx.days if d not in log_days)
    note = ("; %d file(s) in the session folders hold nothing readable and were not counted as "
            "logs (%s)" % (len(blank), ", ".join(sorted(blank)[:4]))) if blank else ""
    # The count printed is the count of LOG FILES, not of dates. One file used to
    # contribute up to two entries to a set of dates (its filename date and its
    # mtime date) and the sentence printed that set's size as "2 dated session
    # log(s)" over a folder holding one file.
    return ("PASS" if not missing else "FAIL",
            "%d active day(s) checked against %d session log file(s) with content (a log covers "
            "its filename date and its last-modified date); days without any log: %s%s"
            % (len(ctx.days), logs, missing or "none", note))


def _registry_lines(ctx, max_age_days=None):
    """(lines, read, old, unreadable, ahead) across the configured registries.

    `ahead` names registry files whose mtime is in the future. Without it, one
    `touch -t 203001010000` made a file fresh forever: every fence in it was
    younger than two days by arithmetic and the hygiene check said so.

    `old` and `unreadable` used to be one list, consumed only on the NO-DATA
    branch, so a chmod 000 on the registry files holding the violations shrank
    the set in silence and turned two FAILs into two PASSes whose sentences said
    "untagged in: none". Skipping a file for AGE is a deliberate filter; failing
    to OPEN a file that is sitting there is the ACCESS axis, and the callers turn
    it into a FAIL, because a registry this check cannot read is a broken record
    and never a smaller registry.
    """
    lines, read, old, unreadable, ahead = [], 0, [], [], []
    for p in ctx.registries:
        problem = evidence_problem(p)
        if problem:
            unreadable.append("%s (%s)" % (os.path.basename(p), problem))
            continue
        try:
            age = (datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 86400
        except OSError as e:
            unreadable.append("%s (%s)" % (os.path.basename(p), type(e).__name__))
            continue
        if age < 0:
            ahead.append(os.path.basename(p))
            continue
        if max_age_days is not None and age > max_age_days:
            old.append(os.path.basename(p))
            continue
        try:
            # Rendered: a fence line inside an HTML comment is invisible to the
            # reader of the registry, so it is invisible to the fence checks.
            body = without_comments(open(p, encoding="utf-8", errors="replace").read()).splitlines()
        except OSError as e:
            unreadable.append("%s (%s)" % (os.path.basename(p), type(e).__name__))
            continue
        read += 1
        for line in body:
            lines.append((p, age, line.strip()))
    return lines, read, old, unreadable, ahead


#: A fence line DECLARES ownership, per STATE.template.md's own grammar, one of
#: three ways: a `files:` scope, an `owner-session:` field, or the template's
#: `agent: <id>` field (colon-terminated, never the bare word "agent" showing
#: up anywhere in a sentence). Each is matched with a leading `\b` and a
#: required colon, so "three-agent fleet" and "docs-refresh agent fence closed
#: clean" (both measured false positives under the old substring rule, see
#: below) never match: neither has a colon right after the word "agent".
_FENCE_FIELD = re.compile(r"\b(?:files|owner-session|agent)\s*:", re.I)

#: The one declaration this project's own registries write WITHOUT a colon
#: field: STATE.template.md's `(sole writer, session <id>)` parenthetical, and
#: the plain `SOLE WRITER` spelling measured in a real registry. Both contain
#: the two words "sole writer", so one case-insensitive phrase match covers
#: both spellings without inventing a fourth marker.
_SOLE_WRITER = re.compile(r"\bsole writer\b", re.I)


def _is_live_fence(s):
    # Both markdown bullets: `*` and `-` render identically, and this project's
    # own entity rule accepts both, so an operator whose registry uses asterisk
    # bullets used to get permanently green fence discipline over untagged live
    # fences.
    #
    # LIVENESS KEYS ON FENCE GRAMMAR, NOT ON THE WORD "AGENT". The old rule
    # ("agent" appears anywhere, case-insensitively) missed a real fence that
    # never spelled the word "agent" at all (a line naming its writer as
    # "SOLE WRITER of tracked files" with a `files:` scope but no "agent"
    # token) and fired on prose that merely mentions the word: a closure note
    # ("docs-refresh agent fence closed clean") and an incidental aside ("a
    # three-agent fleet"), both measured producing warnings on every write.
    # This rule instead asks whether the line DECLARES ownership through one
    # of the grammar markers STATE.template.md actually uses; see _FENCE_FIELD
    # and _SOLE_WRITER above for what counts and why each false case fails to
    # match either.
    if not s.startswith(("- ", "* ")):
        return False
    if "LANDED" in s or "ADOPTED" in s:
        return False
    return bool(_FENCE_FIELD.search(s)) or bool(_SOLE_WRITER.search(s))


def check_fence_hygiene(ctx):
    if ctx.registry_denials:
        # Before the empty test, because a denied parent directory is exactly
        # how the configured set silently became smaller: the files inside it
        # never entered ctx.registries, so "none readable" and even a PASS
        # saying "none" were sentences over evidence sitting on disk.
        return "FAIL", ("%d director(y/ies) named by BROTHERSBE_REGISTRIES exist and cannot be "
                        "entered (%s); the registry files inside never entered discovery, so a "
                        "verdict over the rest would read as covering them"
                        % (len(ctx.registry_denials), ", ".join(ctx.registry_denials[:4])))
    if not ctx.registries:
        return "NO-DATA", "set BROTHERSBE_REGISTRIES to enable; nothing was opened"
    lines, read, old, unreadable, ahead = _registry_lines(ctx)
    if ahead:
        return "FAIL", ("%d registry file(s) carry a modification time in the future (%s): %s"
                        % (len(ahead), ", ".join(sorted(ahead)[:4]), AGE_IS_BROKEN))
    if unreadable:
        # Never a smaller registry: the verdict this check would print over the
        # remaining files reads as covering the whole configured set.
        return "FAIL", ("%d of %d configured registry file(s) exist and could not be read (%s); a "
                        "registry this check cannot open is a broken record, not an absent one, and "
                        "a staleness verdict over the rest would read as covering it"
                        % (len(unreadable), len(ctx.registries), ", ".join(sorted(unreadable)[:4])))
    if not read:
        return "NO-DATA", ("%d registry path(s) configured, none readable, so no fence line was "
                           "examined" % len(ctx.registries))
    stale = sorted({os.path.basename(p) for p, age, s in lines if _is_live_fence(s) and age > 2})
    fences = sum(1 for _, _, s in lines if _is_live_fence(s))
    if not fences:
        return "NO-DATA", ("%d registry file(s) read, none carrying a fence line, so staleness was "
                           "measured over zero fences" % read)
    return ("PASS" if not stale else "FAIL",
            "%d fence line(s) in %d registry file(s); live-looking fences older than 2d in: %s"
            % (fences, read, stale or "none"))


def check_correction_latency(ctx):
    bad = ctx.corrections.problem("correction latency")
    if bad:
        return bad
    # `age_days(...) or 0` read an unreadable timestamp as an age of zero, so a
    # correction with "" or no ts at all counted as fresh and the check reported
    # "0 older than 7d" over rows whose age nothing had measured. An age that
    # could not be computed is not an age of zero.
    undated = [c for c in ctx.corrections.rows if age_days(c.get("ts") or "") is None]
    if undated:
        return "FAIL", ("%d of %d correction candidate(s) carry no readable timestamp (%s), so their "
                        "latency was never measured and this check cannot report on it"
                        % (len(undated), len(ctx.corrections.rows),
                           ", ".join(repr(c.get("ts")) for c in undated[:4])))
    ahead = [c for c in ctx.corrections.rows if age_days(c.get("ts", "")) < 0]
    if ahead:
        return "FAIL", ("%d of %d correction candidate(s) are dated in the future (%s): %s"
                        % (len(ahead), len(ctx.corrections.rows),
                           ", ".join(repr(c.get("ts")) for c in ahead[:4]), AGE_IS_BROKEN))
    old = [c for c in ctx.corrections.rows if age_days(c.get("ts", "")) > 7]
    return ("PASS" if not old else "FAIL",
            "%d candidate(s) total, %d older than 7d unprocessed"
            % (len(ctx.corrections.rows), len(old)))


def check_budget_vs_tier(ctx):
    """Every recent live fence line carries its declared tier.

    This used to append the skill's OWN STATE.md to the registry list on every
    run, so pointing the scorer at an unrelated empty directory printed a green
    fence-discipline line sourced from the author's machine. It reads the
    configured registries and nothing else.
    """
    if ctx.registry_denials:
        return "FAIL", ("%d director(y/ies) named by BROTHERSBE_REGISTRIES exist and cannot be "
                        "entered (%s); the registry files inside never entered discovery, so a "
                        "tier-tag verdict over the rest would read as covering them"
                        % (len(ctx.registry_denials), ", ".join(ctx.registry_denials[:4])))
    if not ctx.registries:
        return "NO-DATA", ("set BROTHERSBE_REGISTRIES to enable; no registry was opened, so no fence "
                           "line was checked for a tier tag")
    lines, read, old, unreadable, ahead = _registry_lines(ctx, max_age_days=7)
    if ahead:
        return "FAIL", ("%d registry file(s) carry a modification time in the future (%s): %s"
                        % (len(ahead), ", ".join(sorted(ahead)[:4]), AGE_IS_BROKEN))
    if unreadable:
        return "FAIL", ("%d of %d configured registry file(s) exist and could not be read (%s); a "
                        "registry this check cannot open is a broken record, not an absent one, and "
                        "a tier-tag verdict over the rest would read as covering it"
                        % (len(unreadable), len(ctx.registries), ", ".join(sorted(unreadable)[:4])))
    if not read:
        return "NO-DATA", ("%d registry path(s) configured, none touched in the last 7d or none "
                           "readable, so no fence line was checked" % len(ctx.registries))
    tagged, untagged = 0, []
    for p, _, s in lines:
        if not _is_live_fence(s):
            continue
        if re.search(r"\btier T[123]\b", s):
            tagged += 1
        else:
            untagged.append(os.path.basename(p))
    if tagged + len(untagged) == 0:
        return "NO-DATA", "no fence lines in the %d registry file(s) touched last 7d" % read
    return ("PASS" if not untagged else "FAIL",
            "%d recent fence line(s) tier-tagged across %d registry file(s), untagged in: %s"
            % (tagged, read, sorted(set(untagged)) or "none"))


def check_prediction_seals(ctx):
    problem = evidence_problem(OPERATOR_MODEL)
    if problem:
        return "FAIL", ("%s is %s; a prediction ledger that exists and cannot be read is a broken "
                        "record, not an absent one" % (OPERATOR_MODEL, problem))
    if not os.path.isfile(OPERATOR_MODEL):
        return "NO-DATA", "no %s, so no prediction ledger was opened" % OPERATOR_MODEL
    p = prediction_counts()
    if p["sealed"] == 0:
        return "NO-DATA", "%s holds no sealed predictions" % OPERATOR_MODEL
    return ("PASS" if p["sealed"] >= 5 else "FAIL",
            "%d sealed (target >= 5)%s" % (p["sealed"], p.get("note", "")))


def check_felt_outcome_ratings(ctx):
    if ctx.ratings.errors:
        return ctx.ratings.problem("felt-outcome ratings")
    # The shared rule, not isinstance: `isinstance(NaN, float)` and
    # `isinstance(True, int)` are both True, so a NaN score and a boolean score
    # each counted as a rating, and six NaNs cleared "6 distinct ratings".
    # numeric() refuses NaN, infinity and booleans in one place.
    rated = [x for x in ctx.ratings.rows if numeric(x.get("score")) is not None]
    if not rated:
        return "NO-DATA", "no scored ratings in %s" % RATINGS
    # The shared dedupe rule, on the rating's CONTENT. The writer stamps a
    # fresh timestamp on every row, so deduplicating whole rows could never
    # fold anything: six copies of one 5/5 rating of one task, differing only
    # in microseconds, cleared "6 distinct ratings". What makes two ratings
    # one rating is the same score of the same task with the same note.
    content = [{k: v for k, v in x.items() if k not in ("ts", "session_id")} for x in rated]
    # derivation_fold, the reduction check_adr already uses, because the plain
    # fold left a trailing period buying a second rating: "good" and "good."
    # are one note, and six spellings of one rating cleared "6 distinct".
    unique, dupes = distinct(content, reduce=derivation_fold)
    return ("PASS" if len(unique) >= 6 else "FAIL",
            "%d distinct ratings (target >= 6 for alignment 10)%s"
            % (len(unique), "" if not dupes else
               "; %d rating(s) identical apart from their timestamps counted once" % dupes))


def check_review_cadence(ctx):
    if ctx.reviews.errors:
        return ctx.reviews.problem("review cadence")
    ahead = [x for x in ctx.reviews.rows if (age_days(x.get("ts", "")) or 0) < 0]
    if ahead:
        # Checked BEFORE max(), because max() picks the future row and a genuinely
        # stale review sitting in the same ledger is masked by it.
        return "FAIL", ("%d review row(s) are dated in the future (%s): %s"
                        % (len(ahead), ", ".join(repr(x.get("ts")) for x in ahead[:4]),
                           AGE_IS_BROKEN))
    # Parsed AGES, never max() over raw timestamp strings: any row whose ts
    # sorted above a date lexically won max(), age_days returned None for it,
    # and one typo silently deleted every real review in the file in the
    # direction of "you have no reviews". An unreadable row is DISCLOSED, and
    # the latest review is the smallest parsed age.
    undated = [x for x in ctx.reviews.rows if age_days(x.get("ts") or "") is None]
    if undated:
        return "FAIL", ("%d review row(s) carry no readable timestamp (%s), so the latest review "
                        "cannot be identified and cadence cannot be measured over this file"
                        % (len(undated), ", ".join(repr(x.get("ts")) for x in undated[:4])))
    ages = [age_days(x.get("ts", "")) for x in ctx.reviews.rows]
    if not ages:
        return "NO-DATA", "no review recorded in %s" % REVIEWS
    a = min(ages)
    return ("PASS" if a <= 7 else "FAIL", "last review: %.1fd ago" % a)


# BrotherSBE silent-failure lints: patterns that hide an error so a wrong result
# passes for a right one. Scoped to the caller's worktree, opt-in via a path arg
# or the SBE_LINT_ROOT env var, so the tool never scans unrelated trees. Language
# builtins and heuristics only, no network, no third-party deps.
#
# The conflict-skipping upsert used to be written as
# `\.execute\([^)]*\).*ON\s+CONFLICT.*DO\s+NOTHING`, which required a Python
# `.execute(` on the same line. `.sql` is the FIRST non-Python extension L11 says
# this lint scans and warehouse work is one of the seven work profiles this skill
# claims, so the one lint aimed at that profile could not fire on that profile's
# files: the statement in a .sql file, the same statement split over two lines,
# and the Python form that puts the SQL in a variable were all invisible. It now
# reads the SQL wherever it is written and in whatever host language, and stops
# at the statement's semicolon so that a legitimate on-conflict clause that
# updates the row instead of silently discarding it is not swept in with it.
LINT_PATTERNS = [
    (re.compile(r"except\s*:"), "bare except (catches everything, hides the real error)", None),
    (re.compile(r"except\s+\w[\w.]*\s*:\s*(#.*)?$\s*pass", re.M), "except-then-pass (swallows the error)", None),
    # The swallow shape that actually destroyed data in this repository:
    # `except X:` then `continue` or `return None` dropped ledger lines from a
    # REWRITER, and the two patterns above could not see it. Legal only in a
    # reader that never rewrites, which is what the inline waiver must say.
    (re.compile(r"except\s+\w[\w.]*\s*:\s*(#.*)?$\s*(?:continue|return\s+None)\b", re.M),
     "except-then-continue/return-None (drops the record, hides the error)", None),
    # Two patterns for one class, because the single pattern that shipped needed a
    # Python `.execute(` on the same line, and `.sql` is the FIRST non-Python
    # extension L11 says this lint scans. The one lint aimed at warehouse work
    # could not fire on a warehouse file: an insert statement whose upsert
    # clause discards the conflicting row without a word said about it,
    # written in a .sql file, the same statement split over two lines, and the Python form
    # that puts the SQL in a variable were all invisible. The second pattern reads
    # the SQL wherever it is written, across newlines, and needs no host language.
    # No character budget between the two halves: `[^;]{0,300}?` gave up after
    # 300 characters, so one long insert whose upsert clause silently discards a row
    # was reported clean while the law said the pattern "stops at the statement's
    # semicolon". `[^;]` already stops there, which is the whole bound this needs.
    (re.compile(r"(?is)\bon\s+conflict\b[^;]*?\bdo\s+nothing\b"), "conflict-skipping upsert without a logged skip count", None),
    # Fire-and-forget subprocess only: the call starts a statement (result discarded)
    # and carries no check=True. An assigned result (out = subprocess.run(...)) can be
    # inspected, so it is not a silent swallow and is not flagged.
    # The lookahead reads the whole call, not up to the first `)`. `[^)]*` ended
    # at the closing paren of a NESTED call, so
    # `subprocess.run(shlex.split(cmd), check=True)` was reported as a swallowed
    # exit code: a lint firing on correct code is how a gate gets switched off.
    # Two levels of nesting are matched, which covers the forms people write; a
    # third would still be a false positive and the `# sbe: allow-silent` escape
    # is the honest answer to that, in the diff where a reader sees it.
    (re.compile(r"^[ \t]*subprocess\.(run|call|Popen)\("
                r"(?!(?:[^()]|\((?:[^()]|\([^()]*\))*\))*check\s*=\s*True)", re.M),
     "discarded subprocess result without check=True (exit code is swallowed)", None),
    # SCOPED TO .swift, and the scope is the fix. `try!` is Swift syntax and
    # cannot occur in Python, SQL, Ruby, JavaScript, TypeScript or Go source. Run
    # unscoped, this pattern matched the ENGLISH WORD in ordinary prose: the
    # docstring line "Give it a try!" in a pure-Python file was reported as a
    # discarded error, at gate severity, which under --strict blocks a merge.
    # Found 2026-07-31 by running this lint against pallets/click, whose
    # examples/colors/colors.py carries exactly that sentence. This file already
    # records the same lesson twice, about the subprocess and ON CONFLICT
    # patterns: a lint firing on correct code is how a gate gets switched off.
    (re.compile(r"try\s*!"), "force-try (Swift try! discards the error)", (".swift",)),
]

SCANNABLE = (".py", ".sql", ".swift", ".rb", ".js", ".ts", ".go")
EXEMPTION = "sbe: allow-silent"
# How many of a list this evidence line prints before it says how many it did not.
NAMED_IN_EVIDENCE = 5


def _first_named(items, sep="; ", n=NAMED_IN_EVIDENCE):
    """The first n items, and how many were not named.

    L11's OUTPUT says each hit names its file and line. Six hits printed five and
    the sixth vanished out of a sentence that reads as the whole list, which is
    the truncated-set defect this project keeps finding one level lower.
    """
    shown = sep.join(items[:n])
    return shown if len(items) <= n else shown + "%sand %d more not named" % (sep, len(items) - n)


def silent_failure_lints(ctx=None):
    """Scan operator source for error-swallowing patterns, returning (verdict, evidence).

    Opt-in via a dir arg or SBE_LINT_ROOT. A line carrying `# sbe: allow-silent
    <reason>` is exempt: the exemption is VISIBLE in the diff and auditable, which
    is the override philosophy applied to lints (a swallow is legal only when a
    human named why). The linter's own file is skipped, because a file that defines
    these patterns as strings would match itself.

    Three verdicts, not two. A run that opened no file cannot have found anything,
    so it reports NO-DATA naming the reason and the evidence says what was scanned
    rather than the word "clean", which asserts the opposite of what happened. A
    file whose every match was exempted was examined and found nothing of its own,
    so when that is true of every file scanned the verdict is NO-DATA too, and the
    exemption count is in the evidence either way: "clean" over a set of suppressed
    hits is the same sentence as a PASS over an empty manifest. A positional
    argument that is not a directory is a broken invocation and FAILs: silently
    ignoring a mistyped path was how a permanently green lint that had never opened
    a file stayed invisible."""
    root = os.environ.get("SBE_LINT_ROOT")
    roots = []
    for a in sys.argv[1:]:
        if a.startswith("-"):
            continue
        if os.path.isdir(a):
            roots.append(a)
            root = a
        else:
            return "FAIL", ("%r is not a directory, so the lint root was never scanned; a mistyped path "
                            "must not read as a clean scan" % a)
    if len(roots) > 1:
        # The last one used to win in silence: the first directory was never
        # scanned and never mentioned, while the sentence named only the
        # survivor. A mistyped path FAILs loudly two lines up; a second valid
        # path is refused just as loudly.
        return "FAIL", ("%d lint roots given (%s) and only one can be scanned per run; the extra "
                        "one(s) would be silently unexamined, so this run refuses instead"
                        % (len(roots), ", ".join(roots)))
    if not root:
        return "NO-DATA", ("no lint root: pass a directory or set SBE_LINT_ROOT. Nothing was opened, "
                           "so there is nothing to call clean")
    if not os.path.isdir(root):
        return "FAIL", "SBE_LINT_ROOT=%s is not a directory, so nothing was scanned" % root
    hits, exempt, self_skipped, empties, unopened = [], [], [], [], []
    scanned, with_matches, all_exempt, empty_files = 0, 0, 0, 0
    unread_kinds = collections.Counter()
    pruner = Pruner()
    # onerror: a chmod 000 directory under the lint root used to vanish from the
    # walk, from the count, and from the sentence, so one chmod was a silent
    # bypass of the gate this project calls unwaivable. It is now a FAIL below.
    for dp, dns, fns in os.walk(root, onerror=pruner.onerror):
        # Shared with the other two walkers, and by CONTENT rather than by name:
        # a virtualenv named .venv-whisper put third-party code through THIS gate,
        # which is the one gate a .sbe-exempt cannot waive, and then pruning by
        # name meant renaming a directory hid a repository's own source from it.
        dns[:] = pruner(dp, dns)
        for fn in sorted(fns):
            path_here = os.path.join(dp, fn)
            # The linter declares the patterns as strings, so it must not match
            # itself. By PATH, not by basename: comparing basenames skipped any
            # file called sbe_score.py in the CALLER's tree, so a user's own file
            # with that name was never opened and "1 file(s) scanned, clean" was
            # printed over a directory holding two, one of which carried a bare
            # `except` clause with a `pass` and nothing else. And the skip is reported, because a completeness
            # sentence over a set the tool truncated in silence is the class this
            # gate exists to catch.
            #
            # samefile, not a path-string comparison: realpath resolved
            # symlinks and nothing else, so the same file reached through a
            # case-different spelling (TOOLS/ on the case-insensitive
            # filesystem macOS ships by default) was two identities, the tool
            # scanned ITSELF, and the unwaivable gate FAILed an honest tree
            # with four "defects" that were its own regex literals, while the
            # self-skip disclosure vanished from the sentence. The identity of
            # a file is the file: samefile compares inodes, which covers case,
            # symlinks, hardlinks, bind mounts and every spelling nobody has
            # typed yet in one call. A path that vanishes between the walk and
            # this stat is a named problem, never a silent skip.
            # The one truncation this function performed in SILENCE. It
            # discloses the self-skip, the unreadable file, the pruned
            # directory and the empty file, and then a bare `continue` here
            # removed every file whose name did not end in one of seven
            # extensions, reaching no list and no sentence: fourteen files,
            # thirteen carrying a real catchable hit, printed "1 file(s)
            # scanned, clean". A Kotlin, Rust, Java, C#, Scala or PHP estate
            # passed this gate by existing, and `.sh` being outside the tuple
            # meant the two shell tools this project ships had never been read
            # by its own unwaivable lint.
            #
            # The enumeration itself is unavoidable: the patterns are written
            # for the languages they name, and pretending to lint a language
            # no pattern reads would be a worse sentence than declaring the
            # gap. So the rule this project already holds for unavoidable
            # enumerations applies instead: everything outside it FAILS
            # LOUDLY rather than passing silently. What is not opened is
            # counted, named by kind, and carried into every verdict below,
            # and the word "clean" is withdrawn when the count is not zero.
            # Hidden files are skipped BEFORE the unread tally, the same way
            # hidden directories are already pruned from the walk. Not because
            # they are uninteresting: because this counter's value ends up
            # PASTED INTO TRACKED DOCUMENTATION, and a check compares the
            # pasted number against a live one. A `.DS_Store` that macOS
            # writes the moment somebody opens a folder in Finder moved the
            # `tools/` figure from 5 to 6, a regeneration baked that 6 into a
            # shipped guide, and the guide then matched on exactly one machine
            # and failed in every clean checkout. Three sessions measured one
            # commit and got three answers before anyone found the cause.
            #
            # A number that changes when a file manager opens a directory is
            # environment state wearing a measurement's clothes, and it must
            # not reach a document. The dot-file convention is the whole
            # mechanism here: this drops what the operating system left, never
            # what a person committed. A hidden file somebody genuinely wants
            # linted is a real gap and is named in the check's own limits
            # rather than silently swept, because `SCANNABLE` decides what
            # gets READ and this line only decides what gets COUNTED as
            # unread.
            if fn.startswith("."):
                continue
            if not fn.endswith(SCANNABLE):
                unread_kinds[os.path.splitext(fn)[1].lower() or "(no extension)"] += 1
                continue
            try:
                is_self = os.path.samefile(path_here, __file__)
            except OSError as e:
                unopened.append("%s (vanished or unstattable during the walk: %s)"
                                % (os.path.relpath(path_here, root), type(e).__name__))
                continue
            if is_self:
                self_skipped.append(os.path.relpath(path_here, root))
                continue
            path = path_here
            # The ACCESS axis: a chmod 000 source file used to fall out of the
            # lint, out of the count, and out of the sentence via a bare
            # `continue`, so one chmod silently bypassed the gate this project
            # calls unwaivable. A source file that exists and cannot be read is
            # named and FAILs below; a FIFO or device is refused without open(),
            # which would block forever.
            problem = evidence_problem(path)
            if problem:
                unopened.append("%s (%s)" % (os.path.relpath(path, root), problem))
                continue
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError as e:
                unopened.append("%s (%s)" % (os.path.relpath(path, root), type(e).__name__))
                continue
            scanned += 1
            src = "\n".join(lines)
            # A file with the right extension holding nothing, or holding only a
            # placeholder token, carries no source to examine. Reporting either
            # as "clean" is the same sentence as a PASS over an empty manifest.
            if not src.strip() or all_vacuous(src) or vacuous(src):
                empty_files += 1
                empties.append(os.path.relpath(path, root))
            file_hits, file_exempt = 0, 0
            file_ext = os.path.splitext(path)[1].lower()
            for pat, desc, only_exts in LINT_PATTERNS:
                # A pattern that names a language is never run against another
                # one. `only_exts` of None means the shape is language-
                # independent and every scannable extension is read.
                if only_exts is not None and file_ext not in only_exts:
                    continue
                for m in pat.finditer(src):
                    first = src[:m.start()].count("\n")
                    last = first + src[m.start():m.end()].count("\n")
                    # The exemption may sit on any line the match spans: on an
                    # except-then-pass, the natural place to write it is the
                    # `pass` line, and reading only the first line of the match
                    # made the documented escape hatch look broken.
                    span = lines[first:last + 1]
                    # A match that lives only in a comment describes the shape in
                    # words rather than committing it, the way this very file's
                    # own LINT_PATTERNS section does. Every line the match spans
                    # must be a full-line comment (first non-space char `#`) for
                    # this to apply, so a match that starts in a comment and
                    # reaches real code is still a hit.
                    if all(l.lstrip().startswith("#") for l in span):
                        continue
                    # The reason is the exemption. L11 writes the marker as
                    # `# sbe: allow-silent <reason>` and the reason was
                    # decoration: a bare marker suppressed the finding, which is
                    # an off switch rather than a reviewed exception, and it is
                    # the one gate a .sbe-exempt cannot waive. Read with the
                    # shared answered(), so `# sbe: allow-silent tbd` is refused
                    # here for the same reason "tbd" is refused as a snapshot id.
                    #
                    # Only the FIRST and LAST line of the span count. The upsert
                    # pattern crosses newlines, so its span could enclose an
                    # unrelated hit, and a reason written about THAT hit waived
                    # this one too: one marker, two swallows. The natural places
                    # to write the marker are the line the match starts on and
                    # the line it ends on, and those are the two that are about
                    # this match.
                    edges = span[:1] + (span[-1:] if len(span) > 1 else [])
                    marked = [l for l in edges if EXEMPTION in l]
                    reason = next((r for r in (answered(l.split(EXEMPTION, 1)[1]) for l in marked)
                                   if r), None)
                    # A reason is a justification a human can review, so it is
                    # held to a floor: "x", "ok" and "0" waived hits, and so did
                    # a reason that was just the marker pasted again.
                    if reason and (EXEMPTION in reason or len(reason) < 8
                                   or len(reason.split()) < 2):
                        reason = None
                    if marked and reason:
                        file_exempt += 1
                        exempt.append("%s:%d" % (os.path.relpath(path, root), first + 1))
                        continue
                    file_hits += 1
                    hits.append("%s:%d %s%s"
                                % (os.path.relpath(path, root), first + 1, desc,
                                   "" if not marked else
                                   " (an `%s` marker with no reason after it waives nothing; write "
                                   "what makes this swallow legal)" % EXEMPTION))
            if file_hits or file_exempt:
                with_matches += 1
                if not file_hits:
                    all_exempt += 1
    # Everything this scan did NOT open, on every verdict it prints. A pruned
    # directory and a self-skipped file are both files a "clean" sentence would
    # otherwise be claiming over.
    note = pruner.note(lambda f: f.endswith(SCANNABLE))
    unread_total = sum(unread_kinds.values())
    if unread_total:
        # Named by KIND and by count, so a reader can see whether the gap is a
        # language this lint has no pattern for or their whole estate.
        kinds = ["%s %d" % (ext, n) for ext, n in
                 sorted(unread_kinds.items(), key=lambda kv: (-kv[1], kv[0]))]
        note += ("; %d file(s) under %s were not opened because this lint has no pattern that "
                 "reads their kind (%s); its patterns are written for %s, so this verdict covers "
                 "those kinds and says nothing about the rest"
                 % (unread_total, root, _first_named(kinds, ", "), " ".join(SCANNABLE)))
    if self_skipped:
        # Named but never COUNTED: a directory where the walk found its own
        # source under thirteen of its fourteen names (a hardlink or a
        # symlink alias reaches the same inode more than once) printed "1
        # file(s) scanned ... clean" with the thirteen listed by name and no
        # number attached, so the withdrawal two branches down never fired
        # (it only checked unread_total) and "clean" read as covering all
        # fourteen. Counted here like every other skip reason, and folded
        # into the same withdrawal below.
        note += ("; this tool's own source was not scanned (%d file(s): %s), because it declares "
                 "these patterns as strings and would match itself"
                 % (len(self_skipped), ", ".join(sorted(self_skipped))))
    # Evidence the lint was refused access to beats every other verdict: a
    # verdict over the files it could open would read as covering the tree, and
    # one chmod must never turn a FAIL into a PASS or into an absence.
    if unopened or pruner.denied:
        refused = sorted(set(unopened)) + sorted(set(pruner.denied))
        return "FAIL", ("%d file(s) or director(y/ies) under %s exist and could not be read (%s); "
                        "source this lint cannot open is source this verdict cannot cover, so it "
                        "cannot call the tree clean; %d file(s) were scanned%s"
                        % (len(refused), root, _first_named(refused, "; "), scanned, note))
    if not scanned:
        return "NO-DATA", ("lint root %s holds no scannable source (%s), so nothing was opened%s"
                           % (root, " ".join(SCANNABLE), note))
    if empty_files == scanned:
        # Files with the right extensions and nothing in them were opened and
        # reported "clean", which is the same sentence as a PASS over an empty
        # manifest. A scan of empty files examined no source.
        return "NO-DATA", ("%d file(s) scanned under %s and every one of them is empty or holds "
                           "nothing but a placeholder, so no line of source was examined and there "
                           "is nothing to call clean%s" % (scanned, root, note))
    # A file with nothing in it is not source anybody examined, and in a MIXED run
    # it was counted inside "N file(s) scanned, clean" with nothing saying so. The
    # all-empty run already had its own verdict; this is the same sentence for the
    # rest of the runs.
    if empties:
        note += ("; %d of the %d file(s) scanned hold nothing to examine (%s), so the count above is "
                 "not a count of files with source in them"
                 % (len(empties), scanned, _first_named(empties, ", ")))
    if hits:
        return "FAIL", ("%d hit(s) in %d file(s) scanned: %s%s"
                        % (len(hits), scanned, _first_named(hits), note))
    if exempt and all_exempt == scanned:
        return "NO-DATA", ("%d file(s) scanned under %s and every match in every one of them was "
                           "suppressed by an inline `%s` comment (%s); a scan whose every finding was "
                           "waived examined nothing it was allowed to report, so it is not clean%s"
                           % (scanned, root, EXEMPTION, _first_named(exempt, ", "), note))
    if exempt:
        # The clean-file count is the whole argument for PASS here rather than the
        # NO-DATA one branch up: source WAS examined and found clean somewhere.
        # It was left to the reader to work out from a truncated list of waived
        # lines, and SKILL.md's sentence about this repository's own run had it
        # wrong in both numbers for as long as nothing printed it.
        return "PASS", ("%d file(s) scanned under %s, 0 unexempted hit(s), %d suppressed by an inline "
                        "`%s` comment (%s), %d file(s) holding no match at all%s"
                        % (scanned, root, len(exempt), EXEMPTION, _first_named(exempt, ", "),
                           scanned - with_matches, note))
    # "clean" is the word the reader carries away and it reads as a claim about
    # the TREE. It is withdrawn the moment this run removed a file from
    # consideration: what it found is still worth a PASS, because it did open
    # source and that source held nothing, but the sentence says which files
    # that verdict is about. A language this lint has no pattern for is a gap
    # in coverage, not evidence of a defect, so this is not a FAIL; failing
    # every polyglot estate would switch the gate off, which is how a gate
    # stops protecting anything.
    if unread_total or self_skipped:
        return "PASS", ("%d file(s) scanned under %s and clean in what was opened, which is not "
                        "the same as a clean tree%s" % (scanned, root, note))
    return "PASS", "%d file(s) scanned under %s, clean%s" % (scanned, root, note)


# The citation inventory: every external URL cited in the shipped documentation
# (README.md, SKILL.md, docs/) must have an entry in docs/CITATIONS.md carrying
# four answered fields: the claim, the population it measured, its date or
# version, and its limit. Why this exists: this repository's own design document
# once deployed a 2024 benchmark score in the present tense over a page whose
# live leaderboard had long moved past it, and quoted a follow-up interval while
# omitting the point estimate it surrounded. A citation whose scope lives only
# in its author's head rots silently, which is this project's headline failure
# class applied to its own prose. The check is offline BY DESIGN: it proves
# coverage and that the scope fields are answered, it can never prove a page
# still says what the entry recorded, and its own sentences state that limit.
CITATION_INVENTORY = os.path.join("docs", "CITATIONS.md")
CITATION_FIELDS = ("claim", "population", "date", "limit")
_URL_RX = re.compile(r"https?://[^\s<>\"')\]]+")
# Markdown wraps a URL in delimiters that are not part of it: a backtick from
# inline code, a comma from prose, an asterisk or underscore from emphasis. Left
# attached, `https://example.com/x` and https://example.com/x read as two
# different URLs, so one of them looks uninventoried and the honest repair looks
# like adding a second entry for the same page, which the duplicate rule then
# rejects. Trimming here keeps the scan set the set of pages a reader can visit.
_URL_TRAILING = ".,;:`*_~"
_HTML_COMMENT_RX = re.compile(r"<!--.*?-->", re.S)
_FIELD_RX = re.compile(r"-\s*(claim|population|date|limit)\s*:\s*(.*)$")


def _doc_urls(text):
    """Every external URL in this markdown body, comments stripped, punctuation trimmed.

    Comments are stripped first because a commented-out paragraph renders as
    nothing: a URL a reader cannot see is not a citation, and an inventory whose
    entries sit inside a comment declares nothing.
    """
    return [m.group(0).rstrip(_URL_TRAILING)
            for m in _URL_RX.finditer(_HTML_COMMENT_RX.sub("", text))]


def _parse_citations(text):
    """{"entries": {url: {field: value}}, "problems": [str]} from the inventory body.

    An entry is a `## <url>` heading followed by `- field: value` bullets. A
    duplicate heading for the same URL is a broken record, not a merge: two
    entries reading differently would let a reader pick the scope they prefer.
    """
    entries, problems, current = {}, [], None
    for i, line in enumerate(_HTML_COMMENT_RX.sub("", text).splitlines(), 1):
        if line.startswith("## "):
            url = line[3:].strip().rstrip(".,;:")
            if url in entries:
                problems.append("line %d declares a second entry for %s" % (i, url))
            current = entries.setdefault(url, {})
            continue
        m = _FIELD_RX.match(line.strip())
        if m and current is not None:
            current[m.group(1)] = m.group(2)
    return {"entries": entries, "problems": problems}


#: How many days the ecosystem comparison page may go unreviewed before this
#: check grades it stale. Seven, because that page's own promise to the reader
#: is a weekly review, and a promise nothing measures is not a promise.
ECOSYSTEM_REVIEW_DAYS = 7

#: The marker the page carries, and the only thing this check reads from it.
ECOSYSTEM_MARKER = "Last reviewed:"


def check_ecosystem_freshness(ctx=None):
    """The comparison page's stated weekly review, made measurable.

    Returns (verdict, evidence). Three states kept distinct on purpose: no
    page at all is NO-DATA because there is nothing to be stale; a page with
    no readable marker FAILs, because an unreadable date is a broken record
    rather than an absent one and a page that cannot be checked must not read
    as fresh; a future date FAILs for the same reason it does everywhere else
    in this file, since an age measured against tomorrow is not an age."""
    root = os.environ.get("SBE_CITATION_ROOT")
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not root:
        return "NO-DATA", ("no root named, so no ecosystem page could be located and "
                           "nothing here is a statement about its freshness")
    path = os.path.join(root, "docs", "ECOSYSTEM.md")
    if not os.path.isfile(path):
        return "NO-DATA", ("no docs/ECOSYSTEM.md under %s, so there is no comparison "
                           "page whose review date could be stale" % root)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError as e:
        return "FAIL", "docs/ECOSYSTEM.md could not be read (%s)" % e
    # Read a VISIBLE line only. The first version put the marker inside an
    # HTML comment, and the honesty sweep proved the hole immediately: its
    # hollowing commented the declaration out and this check still PASSed,
    # because a regex over the whole head does not care whether a reader can
    # see the line. A review date the reader cannot see is also worse for the
    # page itself, so the marker moved into the prose and this loop skips any
    # line carrying a comment opener.
    marker = None
    in_comment = False
    for line in head.splitlines():
        # Track comment state ACROSS lines, not within one: the honesty sweep
        # hollows this example by wrapping the declaration in an HTML comment
        # whose opener sits on its own line, and a per-line test walked
        # straight past that and still PASSed.
        if in_comment:
            if "-->" in line:
                in_comment = False
                line = line.split("-->", 1)[1]
            else:
                continue
        while "<!--" in line:
            before, rest = line.split("<!--", 1)
            if "-->" in rest:
                line = before + rest.split("-->", 1)[1]
            else:
                line = before
                in_comment = True
                break
        marker = re.search(re.escape(ECOSYSTEM_MARKER) + r"\s*(\d{4}-\d{2}-\d{2})", line)
        if marker:
            break
    if not marker:
        return "FAIL", ("docs/ECOSYSTEM.md carries no visible '%s YYYY-MM-DD' line in "
                        "its first 4096 bytes (a line inside an HTML comment does not count), so its stated weekly review cannot be "
                        "measured; an unreadable date is a broken record, never a fresh "
                        "one" % ECOSYSTEM_MARKER)
    stamp = marker.group(1)
    try:
        reviewed = datetime.datetime.strptime(stamp, "%Y-%m-%d").date()
    except ValueError:
        return "FAIL", "docs/ECOSYSTEM.md names %r, which is not a date" % stamp
    today = datetime.date.today()
    age = (today - reviewed).days
    if age < 0:
        return "FAIL", ("docs/ECOSYSTEM.md is reviewed-dated %s, which is in the future; "
                        "%s" % (stamp, AGE_IS_BROKEN))
    if age > ECOSYSTEM_REVIEW_DAYS:
        return "FAIL", ("the comparison page was last reviewed %s, %d day(s) ago, past "
                        "the %d-day review it promises its own readers. Re-read every "
                        "tool description against its source, then move the marker."
                        % (stamp, age, ECOSYSTEM_REVIEW_DAYS))
    return "PASS", ("the comparison page was reviewed %s, %d day(s) ago, inside its "
                    "%d-day promise" % (stamp, age, ECOSYSTEM_REVIEW_DAYS))


def check_citation_inventory(ctx=None):
    """Every cited external URL carries its scope, returning (verdict, evidence).

    Reads SBE_CITATION_ROOT; unset, it defaults to this repository's own root,
    so a bare run covers the shipped docs with no configuration. Set empty, it
    reports NO-DATA: no root was named, so no URL set exists to cover. The
    stated limit rides on the verdict sentence because it is the whole honesty
    of this check: structure and coverage are proven offline, live page content
    is never read, and no network connection is ever opened.

    When the root ships a CHECKSUMS.sha256 manifest naming markdown files that
    are not on disk, the verdict is NO-DATA naming that root: the shipped file
    list and the shipped bytes disagree, so the document set to cover cannot be
    established. That is a packaging defect of the named tree, which for the
    default root is the installation itself, and it is never a finding about
    the repository being scored (row E29).
    """
    root = os.environ.get("SBE_CITATION_ROOT")
    self_note = ""
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Named, because a run over a user's repository printed this check's
        # verdict about a DIFFERENT tree (the installed skill's own) with
        # nothing on the line saying so, and a reader composing the report
        # read it as a claim about the directory they passed.
        self_note = (" (the installed skill's own tree, this check's default; set "
                     "SBE_CITATION_ROOT to point it at another root)")
    if not root:
        return "NO-DATA", ("no citation root named: SBE_CITATION_ROOT is empty, so no document "
                           "was opened and no URL set exists to cover")
    if not os.path.isdir(root):
        return "FAIL", ("SBE_CITATION_ROOT=%s is not a directory, so no document was scanned; a "
                        "mistyped root must not read as a covered citation set" % root)
    inv_path = os.path.join(root, CITATION_INVENTORY)
    inv_norm = os.path.normcase(os.path.normpath(inv_path))
    # The scanned set is DERIVED, never curated: the old set was two names
    # plus one directory (README.md, SKILL.md, docs/*.md), and SECURITY.md,
    # CHANGELOG.md, PUBLISH-CHECKLIST.md and seven other shipped documents
    # were never opened, so a citation planted in a shipped top-level page
    # passed while the honest inventory entry for it was called stale. When
    # the root ships a checksums manifest, the manifest IS the shipped-file
    # list and its markdown entries are the set; otherwise every .md under
    # the root is walked (hidden directories skipped), so the set can only
    # widen with the tree, never lag a hand-maintained tuple.
    docs, unopened, absent = [], [], []
    pruner = Pruner()
    manifest = os.path.join(root, "CHECKSUMS.sha256")
    manifest_rels = []
    if os.path.isfile(manifest) and not evidence_problem(manifest):
        try:
            for line in open(manifest, encoding="utf-8", errors="replace"):
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1].strip().endswith(".md"):
                    manifest_rels.append(parts[1].strip())
        except OSError:
            manifest_rels = []
    if manifest_rels:
        scan_scope = "every markdown file in the CHECKSUMS.sha256 manifest"
        for rel in sorted(manifest_rels):
            p = os.path.join(root, rel)
            problem = evidence_problem(p)
            if problem:
                unopened.append("%s (%s)" % (rel, problem))
            elif os.path.lexists(p):
                docs.append(p)
            else:
                # An absence, NOT a broken claim. evidence_problem's own rule
                # (tools/sbe_checks.py) is that a path which EXISTS and cannot
                # be read is named as broken, while a path that is absent is
                # the caller's NO-DATA. This branch used to push absences into
                # `unopened`, so a manifest naming a file nobody shipped came
                # out as a gate-severity FAIL whose sentence said those paths
                # "exist and could not be read", which was not true of a single
                # one of them. The installed plugin 3.7.3 hit it with 291 such
                # names and every user running the scorer over their own clean
                # repository read a gate FAIL about the vendor's tree.
                absent.append(rel)
    else:
        scan_scope = "every markdown file under the root"
        for dp, dns, fns in os.walk(root, onerror=pruner.onerror):
            dns[:] = [d for d in pruner(dp, dns) if not d.startswith(".")]
            for fn in sorted(fns):
                if fn.endswith(".md"):
                    docs.append(os.path.join(dp, fn))
    scanned, found = 0, {}
    for p in docs:
        if os.path.normcase(os.path.normpath(p)) == inv_norm:
            continue
        problem = evidence_problem(p)
        if problem:
            unopened.append("%s (%s)" % (os.path.relpath(p, root), problem))
            continue
        try:
            body = open(p, encoding="utf-8", errors="replace").read()
        except OSError as e:
            unopened.append("%s (%s)" % (os.path.relpath(p, root), type(e).__name__))
            continue
        scanned += 1
        for url in _doc_urls(body):
            found.setdefault(url, []).append(os.path.relpath(p, root))
    inv_problem = evidence_problem(inv_path)
    inv_exists = os.path.lexists(inv_path)
    entries, entry_problems = {}, []
    if inv_exists and not inv_problem:
        try:
            parsed = _parse_citations(open(inv_path, encoding="utf-8", errors="replace").read())
            entries, entry_problems = parsed["entries"], parsed["problems"]
        except OSError as e:
            inv_problem = "cannot be read (%s)" % type(e).__name__
    # A URL mentioned in the inventory's own prose is a citation too: it must
    # either be an entry heading or be covered by one, or it hides from the scan.
    if entries:
        for url in _doc_urls(open(inv_path, encoding="utf-8", errors="replace").read()):
            if url not in entries:
                found.setdefault(url, []).append(CITATION_INVENTORY)
    note = pruner.note(lambda f: f.endswith(".md"))
    offline = ("; scope structure and coverage only, never live page content: this check opens "
               "no network connection")
    if unopened or pruner.denied or inv_problem:
        refused = sorted(set(unopened)) + sorted(set(pruner.denied))
        if inv_problem:
            refused.append("%s (%s)" % (CITATION_INVENTORY, inv_problem))
        return "FAIL", ("%d path(s) under %s exist and could not be read (%s); a document this "
                        "check cannot open can cite a URL this verdict would miss, so it cannot "
                        "call the citation set covered; %d document(s) were scanned%s%s"
                        % (len(refused), root, _first_named(refused, "; "), scanned, note, offline))
    if absent:
        # NO-DATA, never FAIL and never a silent fall back to walking the tree.
        # The manifest IS the shipped-file list, so a manifest naming files the
        # tree does not carry means the scan scope cannot be established at all,
        # and a scope nobody can establish is an absence of evidence. It is also
        # never a statement about the repository the user asked about: the
        # manifest and the missing files both belong to the tree named here,
        # which for the default root is the installation itself.
        return "NO-DATA", ("the CHECKSUMS.sha256 manifest under %s%s names %d markdown "
                           "file(s) that are not on disk (%s), so the shipped file list and "
                           "the shipped bytes disagree and the document set to cover cannot "
                           "be established; that is a defect of THAT tree's own packaging, "
                           "never of the repository being scored, and an unestablished scope "
                           "is not a covered citation set; %d document(s) were scanned%s%s"
                           % (root, self_note, len(absent),
                              _first_named(sorted(absent), "; "), scanned, note, offline))
    if not scanned:
        return "NO-DATA", ("no markdown document under %s%s (scanned: %s), so no "
                           "URL set exists to cover%s%s" % (root, self_note, scan_scope,
                                                            note, offline))
    if entry_problems:
        return "FAIL", ("%s holds %d duplicate entr(y/ies) (%s); a URL with two scope entries "
                        "lets a reader pick the one they prefer%s%s"
                        % (CITATION_INVENTORY, len(entry_problems),
                           _first_named(sorted(entry_problems), "; "), note, offline))
    uncovered = sorted(u for u in found if u not in entries)
    if uncovered and not inv_exists:
        return "FAIL", ("%d external URL(s) across %d document(s) under %s and no inventory at "
                        "%s; every cited URL needs an entry answering claim, population, date "
                        "and limit: %s%s%s"
                        % (len(found), scanned, root, CITATION_INVENTORY,
                           _first_named(uncovered, "; "), note, offline))
    if uncovered:
        return "FAIL", ("%d of %d external URL(s) have no entry in %s: %s; a citation without "
                        "its population, date and limit is a claim wearing a reference's "
                        "clothes%s%s"
                        % (len(uncovered), len(found), CITATION_INVENTORY,
                           _first_named(uncovered, "; "), note, offline))
    unanswered = []
    for url in sorted(entries):
        gaps = [f for f in CITATION_FIELDS if answered(entries[url].get(f)) is None]
        if gaps:
            unanswered.append("%s (no answered %s)" % (url, ", ".join(gaps)))
    if unanswered:
        return "FAIL", ("%d entr(y/ies) in %s carry unanswered scope field(s): %s; a scope field "
                        "holding a placeholder scopes nothing%s%s"
                        % (len(unanswered), CITATION_INVENTORY, _first_named(unanswered, "; "),
                           note, offline))
    stale = sorted(u for u in entries if u not in found)
    if stale:
        # "Stale" is scoped to what was SCANNED, in those words: the old
        # sentence said "no shipped document cites" over a scan that opened
        # eleven of twenty-one shipped documents, so it punished the honest
        # entry for a citation sitting in a document outside the tuple.
        return "FAIL", ("%d entr(y/ies) in %s name a URL no document this check scanned cites "
                        "(%s; scanned %d document(s): %s); a stale entry is scope nobody reads, "
                        "and it hides the next uncovered URL behind a growing list%s%s"
                        % (len(stale), CITATION_INVENTORY, _first_named(stale, "; "),
                           scanned, scan_scope, note, offline))
    if not found:
        return "NO-DATA", ("%d document(s) scanned under %s and none cites an external URL, so "
                           "there is nothing to cover%s%s" % (scanned, root, note, offline))
    return "PASS", ("%d external URL(s) across %d document(s) scanned under %s%s (%s), each "
                    "with an inventory entry in %s answering claim, population, date and "
                    "limit%s%s"
                    % (len(found), scanned, root, self_note, scan_scope,
                       CITATION_INVENTORY, note, offline))


COLDSTART_PROXY = "model-as-beginner"

# The metrics a run must actually record for that run to count as a measurement.
# Named here rather than inline because the honesty sweep proved the point: a
# PASS that counted rows instead of counting ANSWERS survived a run whose every
# metric had been emptied, and then reported "measured over 1 run".
COLDSTART_METRICS = ("commands_typed", "internal_docs_opened",
                     "context_bytes_at_start", "turns_to_first_artifact")


class _NoBatch(Exception):
    """Carries the verdict a registered check should return when no batch is readable."""

    def __init__(self, verdict, evidence):
        Exception.__init__(self, evidence)
        self.verdict = verdict
        self.evidence = evidence


def _latest_batch(ctx):
    """The newest readable batch row. Raises _NoBatch when there is none.

    It RAISES rather than returning a (verdict, evidence) pair, and the source
    lint in evals/test_no_data_class.py is why: a helper that can hand back a
    verdict is a second place verdicts come from, and no scenario in that file
    reaches a helper that sits in no registry. Verdicts leave this module only
    from the registered check functions below.

    Newest by parsed age, never by max() over raw timestamp strings, for the
    reason check_review_cadence states: a row whose timestamp sorts high
    lexically wins max() while age_days returns None for it.
    """
    problem = ctx.coldstart.problem("cold-start usability")
    if problem:
        raise _NoBatch(problem[0], problem[1])
    dated = [(age_days(r.get("measured_at") or ""), r) for r in ctx.coldstart.rows]
    readable = [(a, r) for a, r in dated if a is not None]
    if not readable:
        raise _NoBatch("FAIL", "no cold-start batch carries a readable measured_at, so the "
                               "newest measurement cannot be identified")
    return min(readable, key=lambda pair: pair[0])[1]


def check_coldstart_receipt(ctx):
    try:
        row = _latest_batch(ctx)
    except _NoBatch as no_batch:
        return no_batch.verdict, no_batch.evidence
    if answered(row.get("proxy")) is None:
        return "FAIL", ("the newest cold-start batch records no proxy label. The label is "
                        "mandatory because a model playing a beginner is not a human study, "
                        "and a receipt that drops it asserts more than it measured")
    if row.get("proxy") != COLDSTART_PROXY:
        return "FAIL", ("the newest cold-start batch records proxy %r, which is not %r"
                        % (row.get("proxy"), COLDSTART_PROXY))
    runs = [r for r in (row.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return "NO-DATA", ("the newest cold-start batch holds zero runs, so usability was "
                           "measured over nothing and no verdict follows from it")
    if row.get("complete") is not True:
        return "NO-DATA", ("the newest cold-start batch is marked incomplete, which is what a run "
                           "that hit its spend ceiling writes; a partial measurement supports no "
                           "verdict")
    # A run counts only when it finished AND recorded at least one metric. Counting
    # rows instead of answers is exactly how a PASS survives an emptied fixture.
    # answered(), not stated(): a metric recorded as "-" or "TODO" is present and
    # non-empty and still names no measurement. stated() let punctuation through
    # and the check then reported a PASS over a run that measured nothing.
    usable = [r for r in runs
              if r.get("completed") is True
              and any(answered(r.get(m)) is not None for m in COLDSTART_METRICS)]
    if not usable:
        return "NO-DATA", ("the newest cold-start batch holds %d run(s) but none of them both "
                           "finished and recorded a metric, so there is nothing to report on"
                           % len(runs))
    if answered(row.get("head_sha")) is None:
        return "FAIL", ("the newest cold-start batch names no head_sha, so nothing pins what it "
                        "measured")
    return "PASS", ("cold start measured over %d usable run(s) of %d at %s, proxy %s"
                    % (len(usable), len(runs), row.get("head_sha"), COLDSTART_PROXY))


def check_coldstart_thresholds(ctx):
    # The receipt is read BEFORE the threshold question, and the order is the
    # whole point: with the branches the other way round, an unreadable receipt
    # hid behind an undeclared threshold and a broken record reported NO-DATA.
    # A broken record is a broken claim, never an absence.
    try:
        row = _latest_batch(ctx)
    except _NoBatch as no_batch:
        return no_batch.verdict, no_batch.evidence
    if not COLDSTART_THRESHOLDS:
        return "NO-DATA", ("no cold-start threshold is declared, so the measured numbers are "
                           "recorded and nothing is asserted about them. A threshold set before "
                           "a baseline exists is an appetite, not a measurement")
    runs = [r for r in (row.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return "NO-DATA", "no runs in the newest cold-start batch to compare against thresholds"
    breaches = []
    for metric, limit in sorted(COLDSTART_THRESHOLDS.items()):
        values = [r.get(metric) for r in runs if isinstance(r.get(metric), (int, float))]
        if not values:
            return "NO-DATA", ("no run records %s, so the threshold for it compares against "
                               "nothing" % metric)
        median = sorted(values)[len(values) // 2]
        if median > limit:
            breaches.append("%s median %s exceeds %s" % (metric, median, limit))
    if breaches:
        return "FAIL", "; ".join(breaches)
    return "PASS", ("%d threshold(s) met across %d run(s)"
                    % (len(COLDSTART_THRESHOLDS), len(runs)))


def _rel(p):
    """A registry path as the honesty test can rebuild it under a throwaway vault."""
    return os.path.relpath(p, VAULT)


# One minimal worked fixture per check, holding only the fields that check's PASS
# sentence asserts over. Minimal is the point: every leaf here is a leaf the
# honesty test may empty, and a PASS that survives an emptied leaf is a sentence
# claiming something nothing read. %(now)s, %(today)s and %(dir)s are substituted
# by the test when it writes the fixture into its throwaway vault.
_FENCE_REGISTRY = {"files": {"STATE.md": "# State\n## Fences\n"
                                         "- agent: builder | tier T2 | objective: ship the refund gate\n"},
                   "env": {"BROTHERSBE_REGISTRIES": "%(dir)s/*.md"}}
# Five DISTINCT predictions. The fixture used to be one prediction across five
# dates, which certified the dedupe bypass as the worked example of correct
# evidence: the threshold counts predictions, not rows.
_PREDICTIONS = ("# Operator model\n## Prediction ledger\n"
                "date | prediction | seal | scored on | hit\n"
                "2026-07-01 | the queue depth stays under 1k | seal-1 | review | yes\n"
                "2026-07-02 | the backlog drains by Friday | seal-2 | review | yes\n"
                "2026-07-03 | the cache ratio holds above 90 | seal-3 | review | no\n"
                "2026-07-04 | the retry storm does not recur | seal-4 | review | yes\n"
                "2026-07-05 | the export lands before 09:00 | seal-5 | review | yes\n")
# One doc citing one URL, one inventory entry answering all four scope fields.
# The inventory fixture starts at its `##` heading on purpose: a decorative
# top-level heading would give the hollowing sweep a section whose removal
# changes nothing, and a scenario that reproduces the fixture is not a scenario.
_CITATION_DOC = ("# Demo doc\n\n"
                 "A claim resting on 12 percent of runs (https://example.com/study).\n")
_CITATION_ENTRY = ("## https://example.com/study\n"
                   "- claim: 12 percent of runs showed the behavior\n"
                   "- population: 128 runs of one model under one harness\n"
                   "- date: 2025-06-05\n"
                   "- limit: single source, one model family\n")

CHECKS = {
    "ledger-coverage": Check(
        check_ledger_coverage, reads=(LEDGER,), kind="jsonl", severity="soft",
        full_fixture={"files": {_rel(LEDGER): [{"session_id": "sess-0001", "ts": "%(now)s"}]}}),
    "schema-2-uniform": Check(
        check_schema_uniform, reads=(LEDGER,), kind="jsonl", severity="soft",
        full_fixture={"files": {_rel(LEDGER): [{"schema": 2, "session_id": "sess-0001"}]}}),
    "cache-economy": Check(
        check_cache_economy, reads=(LEDGER,), kind="jsonl", severity="soft",
        full_fixture={"files": {_rel(LEDGER): [{"session_id": "sess-0001", "ts": "%(now)s",
                                                "cache_read": 95, "cache_write": 5}]}}),
    "vault-log-per-active-day": Check(
        check_vault_log_per_active_day, reads=(LEDGER,), kind="jsonl", severity="soft",
        full_fixture={"files": {_rel(LEDGER): [{"session_id": "sess-0001", "ts": "%(now)s"}],
                                "10-Projects/demo/Sessions/%(today)s-session.md":
                                    "# Session log\nWhat happened today.\n"}}),
    "fence-hygiene": Check(check_fence_hygiene, reads=("BROTHERSBE_REGISTRIES",), kind="tree", severity="soft",
                           empty_fixture={"files": {"STATE.md": ""}, "value": "%(dir)s/*.md"},
                           full_fixture=_FENCE_REGISTRY),
    "correction-latency": Check(
        check_correction_latency, reads=(CORRECTIONS,), kind="jsonl", severity="soft",
        full_fixture={"files": {_rel(CORRECTIONS): [{"ts": "%(now)s"}]}}),
    "budget-vs-tier": Check(check_budget_vs_tier, reads=("BROTHERSBE_REGISTRIES",), kind="tree", severity="soft",
                            empty_fixture={"files": {"STATE.md": ""}, "value": "%(dir)s/*.md"},
                            full_fixture=_FENCE_REGISTRY),
    "prediction-seals": Check(check_prediction_seals, reads=(OPERATOR_MODEL,), kind="text", severity="soft",
                              full_fixture={"files": {_rel(OPERATOR_MODEL): _PREDICTIONS}}),
    "felt-outcome-ratings": Check(
        check_felt_outcome_ratings, reads=(RATINGS,), kind="jsonl", severity="soft",
        # Six DISTINCT rows. The fixture used to be one row repeated six times,
        # which is exactly the defect the shared dedupe rule closed: it certified
        # a threshold by writing the same rating out six times.
        full_fixture={"files": {_rel(RATINGS): [{"score": s} for s in (5, 4, 3, 2, 1, 0)]}}),
    "review-cadence": Check(
        check_review_cadence, reads=(REVIEWS,), kind="jsonl", severity="soft",
        full_fixture={"files": {_rel(REVIEWS): [{"ts": "%(now)s"}]}}),
    "ecosystem-freshness": Check(
        check_ecosystem_freshness, reads=("SBE_CITATION_ROOT",), kind="tree",
        severity="soft",
        empty_fixture={"files": {"docs/README.md": "no comparison page here\n"},
                       "value": "%(dir)s"},
        full_fixture={"files": {"docs/ECOSYSTEM.md":
                                "# map\n\nLast reviewed: %(today)s\n"},
                      "env": {"SBE_CITATION_ROOT": "%(dir)s"},
                      "value": "%(dir)s"}),
    "coldstart-receipt": Check(
        check_coldstart_receipt, reads=(COLDSTART,), kind="jsonl", severity="soft",
        full_fixture={"files": {_rel(COLDSTART): [
            {"head_sha": "0123456789abcdef0123456789abcdef01234567",
             "measured_at": "%(now)s", "proxy": "model-as-beginner", "complete": True,
             "runs": [{"completed": True, "commands_typed": 2}]}]}}),
    "coldstart-thresholds": Check(
        check_coldstart_thresholds, reads=(COLDSTART,), kind="jsonl", severity="soft",
        full_fixture=None,
        no_full_fixture=("no threshold is declared yet, so no evidence can make this check PASS. "
                         "It reports NO-DATA by construction until a baseline run sets the bars, "
                         "and that is the honest state rather than a green line")),
    "silent-failure-lints": Check(silent_failure_lints, reads=("SBE_LINT_ROOT",), kind="tree", severity="gate",
                                  empty_fixture={"files": {"notes.txt": "no scannable source here\n"},
                                                 "value": "%(dir)s"},
                                  full_fixture={"files": {"src/ok.py": "def f():\n    return 1\n"},
                                                "env": {"SBE_LINT_ROOT": "%(dir)s/src"}}),
    "citation-inventory": Check(check_citation_inventory, reads=("SBE_CITATION_ROOT",), kind="tree",
                                severity="gate",
                                empty_fixture={"files": {"README.md": "# Notes\n\nA shipped doc "
                                                                      "naming no external URL.\n"},
                                               "value": "%(dir)s"},
                                full_fixture={"files": {"README.md": _CITATION_DOC,
                                                        "docs/CITATIONS.md": _CITATION_ENTRY},
                                              "env": {"SBE_CITATION_ROOT": "%(dir)s"}}),
}


def _resolved_sources(check):
    """Where this check's evidence lives on THIS machine.

    Resolved the way each check resolves it, from the same environment, so the
    grouping below reports what the run actually read rather than what a
    registry entry hints at. An env-var entry in `reads` names a source the
    operator points at; anything else is already a path.
    """
    out = []
    for r in check.reads:
        if r == "BROTHERSBE_REGISTRIES":
            out.extend(REGISTRIES)
        elif r == "SBE_LINT_ROOT":
            out.append(os.environ.get("SBE_LINT_ROOT") or
                       next((a for a in sys.argv[1:]
                             if not a.startswith("-") and os.path.isdir(a)), ""))
        elif r == "SBE_CITATION_ROOT":
            # Unset, this check reads the INSTALLED SKILL'S OWN TREE, which is
            # why it belongs on the other side of the split on anybody else's
            # repository: its verdict is true, and it is not about their code.
            v = os.environ.get("SBE_CITATION_ROOT")
            out.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                       if v is None else v)
        else:
            out.append(r)
    return [p for p in out if p]


def scanned_root():
    """The directory this run is REPORTING ON, which is not always the one it
    is RUN FROM. The split below groups checks by whether they opened a file
    inside it, so anchoring it on the working directory silently inverted the
    whole report whenever somebody ran this tool from its own checkout against
    another tree: the citation check, reading THIS repository's docs, was filed
    under "these verdicts are about the code here", while the lint that had just
    read the caller's tree was filed under "not a statement about the code in
    this directory". The one line the reader came for sat beneath the heading
    that disowned it. Precedence matches `_resolved_sources`: SBE_LINT_ROOT
    first, then the positional directory, then the working directory."""
    env = os.environ.get("SBE_LINT_ROOT")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    for a in sys.argv[1:]:
        if not a.startswith("-") and os.path.isdir(a):
            return os.path.abspath(a)
    return os.path.abspath(os.getcwd())


def _reads_this_tree(check, here):
    """True when at least one of this check's sources exists inside `here`."""
    for p in _resolved_sources(check):
        try:
            a = os.path.abspath(p)
        except (OSError, ValueError):
            continue
        if not os.path.exists(a):
            continue
        if a == here or a.startswith(here + os.sep):
            return True
    return False


SCORE_USAGE = (
    "usage: sbe_score.py [directory] [--strict] [--strict-soft] [--repo-only]\n"
    "  reads: the registries, receipts and source tree the checks name; a\n"
    "    directory argument sets the lint root (SBE_LINT_ROOT overrides).\n"
    "  writes: nothing.\n"
    "  flags:\n"
    "    --strict          make a gate-severity FAIL block the exit code\n"
    "    --strict-soft     make graded (soft) FAILs block as well; implies --strict\n"
    "    --repo-only       do not run checks whose evidence lives outside the\n"
    "                      scanned directory (a vault, a session ledger, this\n"
    "                      installation's own tree); name them instead of\n"
    "                      running them, so nothing about this machine can\n"
    "                      reach the output (finding E2.3, 2026-08-31)\n"
    "    -h, --help        print this and exit 0 without examining anything"
)


def main():
    # Flags used to be skipped wholesale wherever argv was read, so `-h` ran a
    # real scan instead of printing help, and a mistyped flag was silently
    # ignored rather than refused: the same defect class that let
    # sbe_telemetry's `data-export --help` run a real export.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        for usage_line in SCORE_USAGE.splitlines():
            say(usage_line)
        sys.exit(0)
    for a in sys.argv[1:]:
        if a.startswith("-") and a not in ("--strict", "--strict-soft", "--repo-only"):
            for usage_line in SCORE_USAGE.splitlines():
                say(usage_line)
            say("sbe_score: unrecognized flag %r; refusing rather than running past it" % a)
            sys.exit(2)
    repo_only = "--repo-only" in sys.argv
    # `here` is needed to classify checks BEFORE running them under
    # --repo-only, not only to group them afterward: the whole point of the
    # flag (finding E2.3, 2026-08-31: a stranger's first run printed this
    # installation's own home path) is that a vault- or registry-fed check's
    # body never executes and never gets a chance to print anything, rather
    # than being run and then filtered out of a report that already saw it.
    here = scanned_root()
    # A check whose classification raises is treated as reading elsewhere,
    # the conservative side under --repo-only: uncertainty about where a
    # check's evidence lives must never default to running it, or the one
    # case this flag exists for (an unreadable classification) is exactly
    # the case it fails open on.
    elsewhere_names = []
    to_run = []
    for name in CHECKS:
        try:
            reads_here = _reads_this_tree(CHECKS[name], here)
        except Exception:
            reads_here = False
        if repo_only and not reads_here:
            elsewhere_names.append(name)
        else:
            to_run.append(name)
    # Building the context is itself guarded. It used to sit outside run_guarded,
    # so a context that could not be built took every check with it. A check whose
    # context cannot be built is a FAILED check, one line each, and the checks that
    # do not need the broken part still run.
    try:
        ctx = Ctx()
        ctx_error = ""
    except Exception as e:  # sbe: allow-silent the exception becomes the FAIL evidence on every check below, and is not swallowed
        ctx, ctx_error = None, ("the evidence context could not be built: %s: %s. A check whose "
                                "context could not be built examined nothing, and is reported here "
                                "as a failure rather than disappearing"
                                % (type(e).__name__, e))
    # Gate-severity checks print FIRST. The documented one-liner ("scan for
    # silent swallows") used to open with ten NO-DATA lines about vault
    # telemetry paths unrelated to the tree being linted, with the line the
    # reader came for eleventh; a hurried engineer stops reading, and
    # composition is part of a report's honesty. Registry order is kept
    # within each severity group.
    ordered = ([n for n in to_run if CHECKS[n].severity == "gate"]
               + [n for n in to_run if CHECKS[n].severity != "gate"])
    results = [(name,) + (("FAIL", ctx_error) if ctx is None
                          else run_guarded(name, CHECKS[name], ctx))
               for name in ordered]
    width = max((len(n) for n, _, _ in results), default=len("(none)"))
    fails = sum(1 for _, v, _ in results if v == "FAIL")
    # THE SPLIT, and the reason it exists. Severity ordering was not enough. On
    # a first run against a repository that has installed nothing, the one line
    # in the report that is TRUE ABOUT THE READER'S OWN CODE printed eleventh,
    # underneath ten NO-DATA lines about a vault path that does not exist on
    # their machine and a citation check reading the installed skill's own tree.
    # A reader who stops at line three has read nothing about their repository
    # and has no way to know it. So the lines are grouped by whether the check
    # opened a file in THIS directory, the heading says which is which, and a
    # group that examined nothing here says so before its first line. Nothing
    # about the verdicts changes: no aggregation, no score, no new PASS.
    mine = [r for r in results if _reads_this_tree(CHECKS[r[0]], here)]
    elsewhere = [r for r in results if r not in mine]

    def block(rows):
        for n, v, e in rows:
            # one_line: evidence quotes ledger fields and file names, and a value
            # carrying a newline must not get to write its own report lines.
            say("%-*s  %-7s  %s [severity: %s]" % (width, n, v, one_line(e), CHECKS[n].severity))

    say("CHECKS THAT OPENED A FILE IN %s (%d of %d): these verdicts are about the code here."
        % (here, len(mine), len(results)))
    if mine:
        block(mine)
    else:
        say("  (none: no check found evidence inside this directory, so nothing below is "
            "a statement about the code here)")
    print("")
    if repo_only:
        # Nothing about `elsewhere_names` below is a path: only the short,
        # static check names the CHECKS registry declares, which is exactly
        # what keeps this branch honoring the flag's own promise.
        if elsewhere_names:
            say("CHECKS SKIPPED, NOT RUN (--repo-only) (%d of %d): their evidence lives "
                "outside %s (a vault, a session ledger, this installation's own tree), "
                "so they were never executed and nothing about this machine reached this "
                "output: %s. Omit --repo-only to run and print them."
                % (len(elsewhere_names), len(CHECKS), here, ", ".join(sorted(elsewhere_names))))
        else:
            say("CHECKS SKIPPED, NOT RUN (--repo-only) (0 of %d): every registered check "
                "reads inside %s, so none were skipped." % (len(CHECKS), here))
        print("")
    else:
        absent = sum(1 for n, _v, _e in elsewhere
                     if not any(os.path.exists(p) for p in _resolved_sources(CHECKS[n])))
        say("CHECKS FED BY A VAULT OR REGISTRY OUTSIDE %s (%d of %d, of which %d have no source on "
            "this machine at all): a verdict here is not a statement about the code in this "
            "directory." % (here, len(elsewhere), len(results), absent))
        if elsewhere:
            block(elsewhere)
        print("")
    say("%d checks: %d PASS, %d FAIL, %d NO-DATA. LLM judge scores only the residue."
        % (len(results), sum(1 for _, v, _ in results if v == "PASS"), fails,
           sum(1 for _, v, _ in results if v == "NO-DATA")))
    # Two modes, same checks: the local hook stays advisory (exit 0, never blocks a
    # session), but strict runs exit nonzero so CI can block a merge. Which FAILs
    # block is the severity each check declared at write time, printed on its
    # verdict line: `--strict` blocks on gate severity (here, the lints), and soft
    # severity blocks only under the opt-in `--strict-soft` (which implies
    # --strict), so a graded practice check cannot stop a merge unless the
    # repository asked for exactly that. The shipped workflow passes both flags.
    gate_fails = sum(1 for n, v, _ in results if v == "FAIL" and CHECKS[n].severity == "gate")
    soft_fails = fails - gate_fails
    strict_soft = "--strict-soft" in sys.argv
    strict = "--strict" in sys.argv or strict_soft
    if strict and gate_fails:
        say("STRICT: %d gate-severity check(s) failed; exiting nonzero to fail the CI gate." % gate_fails)
        sys.exit(1)
    if strict_soft and soft_fails:
        say("STRICT-SOFT: %d soft-severity check(s) failed; exiting nonzero because "
              "--strict-soft opts graded checks into blocking." % soft_fails)
        sys.exit(1)
    if strict and soft_fails:
        say("NOTE: %d soft-severity check(s) failed. --strict blocks on gate severity only; "
              "add --strict-soft to block on these too." % soft_fails)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # A broken checker must not silently waive the gate. In strict mode a crash
        # blocks, exactly as in sbe_gate.py; advisory mode still never blocks a session.
        say("sbe_score: error %r" % (e,))
        sys.exit(1 if ("--strict" in sys.argv or "--strict-soft" in sys.argv) else 0)
