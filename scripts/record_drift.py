"""record_drift: does the record still match reality after the work landed?

THE GAP THIS FILLS, named after a night that kept hitting it. This estate has
controls for whether work is green, whether a plan's commands are real, whether
evidence survives, and whether a node is decomposed. It had NONE for whether the
record still describes the world once the work is done. Every instance found on
2026-08-29 was found by a person asking or by a peer session, never by a check:

  * a node marked DONE whose artifact had been deleted
  * a status field reading SCHEDULED beside its own evidence field reading
    DECIDED
  * two complaints still reported NOT-ADDRESSED hours after the work closing
    them had shipped
  * eight rows open in one plan file and invisible in another
  * a claim that work was landed when it sat on an unpushed branch

Every one is the same shape: the work moved and the record did not. That is not
a bug in any of those tools, it is a missing check, and it is the cheapest kind
to write because it only compares two things this estate already stores.

WHAT IT COMPARES, and it never guesses beyond these:

  1. A node claiming DONE whose evidence names a commit: does that commit exist?
  2. A node whose status contradicts its own evidence text (a SCHEDULED row
     whose evidence says DECIDED, or the reverse).
  3. A complaint verdict against the status of every node declaring it closed:
     a complaint cannot be NOT-ADDRESSED when a DONE node closes it, and a
     complaint marked ADDRESSED by a node that is still open is worse.
  4. A node claiming to be landed or pushed whose commit is not reachable from
     the remote it names.

NO-DATA IS NEVER A PASS. A commit this cannot resolve, a repository it cannot
read, an evidence field with no commit in it: each is reported as unchecked
rather than clean, because "I could not tell" and "it is fine" are the two
sentences this whole estate keeps confusing.

IT REPORTS, IT DOES NOT REPAIR. Rewriting a record to match reality is a
judgement about which of the two is wrong, and that is a person's call: the work
may have been reverted rather than the record being stale.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(ROOT, "docs", "plan", "READINESS-ROADMAP-2026-08-29.json")

#: A git object id as this estate writes them in evidence prose: seven or more
#: hex characters standing alone. Short enough to match real abbreviations, long
#: enough that ordinary words do not qualify.
SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")

#: Words that assert a decision was taken, so a status still reading SCHEDULED
#: beside one of them is the contradiction found on this board today.
DECIDED_WORDS = ("DECIDED", "CLOSED", "DELIVERED", "LANDED", "SHIPPED")

CLOSED_STATUSES = ("DONE", "SUPERSEDED")
OPEN_ISH = ("SCHEDULED", "OPEN", "IN-FLIGHT")

#: A completion word describing ONE SUB-PART of a multi-part row is not a
#: claim that the whole row is done. Row H5's evidence reads "P1.1 landed
#: ... P1.2 to P1.4 remain" while the row itself stays correctly OPEN: the
#: seventh false-positive class, found the same way as the other six, by
#: reading what the checker flagged. Matches only when the sub-part id sits
#: directly before the word, in its own clause; a completion word describing
#: the ROW itself, unscoped, still drifts.
SUBPART_RE = re.compile(r"\bp\d+\.\d+\s+$")


def _decided_word_is_subpart_scoped_everywhere(word_lower, text_lower):
    """True only when EVERY appearance of word_lower in text_lower is
    immediately preceded by a sub-part id like 'p1.1 landed', so none of
    them claims the row itself is done."""
    start = 0
    seen = False
    while True:
        idx = text_lower.find(word_lower, start)
        if idx == -1:
            return seen
        seen = True
        if not SUBPART_RE.search(text_lower, 0, idx):
            return False
        start = idx + 1


#: The ninth false-positive class, found the same way as the other eight:
#: rows S6, S10 and S14 each explain, in so many words, that the status
#: STAYS what it already reads ("the status stays OPEN on purpose", "THE
#: ROW STAYS IN-FLIGHT"), then go on for paragraphs describing sub-work that
#: DID land or close, in prose this checker's own word list also catches
#: ("fail-closed policy", "a unit closed", "(E105, landed ...)", "closed the
#: last two positive misses"). An author who explicitly reaffirms the
#: current status in the same evidence has already answered the question
#: this check exists to ask; matching an incidental "closed" or "landed"
#: elsewhere in the same text and overruling that explicit sentence is the
#: checker inventing a violation the evidence already refutes.
def _status_is_explicitly_reaffirmed(status_lower, text_lower):
    return re.search(r"\bstays\s+%s\b" % re.escape(status_lower),
                      text_lower) is not None


def nodes(doc):
    return list(doc.get("rows", [])) + list(doc.get("features", []))


#: EVERY repository a commit named in this board's evidence might live in. The
#: first version checked ONE and reported seven false drifts, because a node
#: built in a sibling repository cites a commit this tree has never heard of.
#: That is the same cross-repository ambiguity this board already recorded as a
#: defect in the scheduler, reappearing in the tool written to catch drift, and a
#: checker that manufactures violations is worse than no checker: it sends
#: somebody to fix work that was already right and teaches everyone to ignore it.
KNOWN_REPOS = (
    ROOT,
    os.path.expanduser("~/Documents/BrotherModeUp"),
    os.path.expanduser("~/Documents/BrotherSBE"),
    # The private hub joined the estate 2026-08-30 (HUB-MIGRATION-PLAN):
    # evidence may cite hub commits, and a checker that cannot see the hub
    # calls delivered work local-only.
    os.path.expanduser("~/brother-hub"),
    # The token-shield checkout joined 2026-09-02: row I1's live delivery ran
    # the door against a clone of that public repository, so its evidence
    # cites commits this estate's own trees have never held.
    os.path.expanduser("~/SaveClaudeTokens"),
    # The founder's Kay Vault joined 2026-09-04: E55's evidence names commits
    # of that repository (remote origin github.com/khalilmaaouni/kay-vault,
    # private), and a checker blind to it reports a real, pushed commit as
    # drifted. If this path is absent on a machine, _commit_exists already
    # skips it (no .git found) rather than failing the check.
    os.path.expanduser("~/Documents/Kay Vault"),
)


def _commit_exists(sha, repo=None, runner=None):
    """True in ANY known repository, False in none of them, None when no
    repository could be read at all."""
    repos = [repo] if repo and repo not in KNOWN_REPOS else list(KNOWN_REPOS)
    if repo and repo not in repos:
        repos.insert(0, repo)
    checked = False
    for candidate in repos:
        if not os.path.exists(os.path.join(candidate, ".git")):
            # exists, not isdir: in a linked worktree .git is a FILE, and
            # isdir silently skipped this very repository (second instance
            # of the worktree-blindness class tonight, after integrate._Lock)
            continue
        runner_ = runner or (lambda cmd, **kw: subprocess.run(
            cmd, capture_output=True, text=True, cwd=candidate, timeout=20))
        try:
            proc = runner_(["git", "cat-file", "-e", sha + "^{commit}"])
        except Exception:  # noqa: BLE001
            continue           # sbe: allow-silent try the next repository
        checked = True
        if proc.returncode == 0:
            return True
    return False if checked else None



#: A hex string is only a COMMIT when the prose says so. Without this, the
#: checker reported four false drifts against plan FINGERPRINTS: this estate
#: writes plan versions as content hashes, so "mutating will_run moved
#: b99ad13d35cc to a62113e7318d" looks exactly like two commits to a pattern
#: that only knows hex. Requiring a nearby commit word is the cheap fix and it
#: is the fourth false-positive class this one checker produced before it was
#: honest, each one found by looking at what it flagged rather than trusting it.
COMMIT_WORDS = ("commit", "committed", "pushed", "landed", "at ", "sha", "rev")

#: A hex token labelled as a FILE DIGEST, not a commit. "corpus sha1
#: f3920b31b83f, unchanged" reads DRIFT on row E96 because "sha" above is a
#: substring of "sha1", so the word this checker uses to spot a commit is
#: also the word every digest label is built from. The eighth false-positive
#: class this one checker has produced, found 2026-09-04 the same way as the
#: other seven: by reading what it flagged.
DIGEST_WORDS = ("sha1", "sha256", "hash", "digest", "checksum")


def commit_shas(text, window=44):
    """Hex strings this text presents AS commits, not every hex string in it."""
    out = []
    for m in SHA_RE.finditer(text or ""):
        sha = m.group(1)
        if sha.isdigit():
            continue
        # A HEX TOKEN INSIDE A URL IS NOT A COMMIT CLAIM. The fifth false
        # positive class this checker produced: "Published at
        # https://.../artifact/5723e795-0dd5-..." trips the "at " word above and
        # then matches the first eight hex characters of a UUID. Walk back to
        # the nearest whitespace: if the word the token sits in carries a scheme
        # separator, the token is part of an address somebody wrote, not a
        # commit somebody claimed. Found 2026-08-29 by reading what it flagged.
        head = text[:m.start()]
        word_start = max(head.rfind(" "), head.rfind("\n"), head.rfind("\t")) + 1
        if "://" in text[word_start:m.end()]:
            continue
        before = (head[max(0, m.start() - window):] or "").lower()
        if any(w in before for w in DIGEST_WORDS):
            continue
        if any(w in before for w in COMMIT_WORDS):
            out.append(sha)
    return out


#: A commit that does not exist in any known repository is not always a lie:
#: row I1's live delivery ran the door against a FRESH CLONE of a public
#: repository under a temporary path, on purpose, and that clone is discarded
#: by design once the run ends. Its integration commits are real, they are
#: just nowhere this checker will ever be able to look. Reporting that as
#: DRIFT is the sixth false-positive class this one checker has produced
#: (URL hex, plan fingerprints, and the others noted above it): a run
#: artifact of a throwaway clone is not a claim about any kept repository.
def _ran_in_throwaway_clone(text):
    """True when a node's evidence describes a delivery run inside a
    temporary clone, so a commit it names may be unresolvable by design
    rather than missing."""
    low = str(text or "").lower()
    return ("/tmp/" in low or "temporary" in low) and "clone" in low


def check_evidence_commits(doc, repo=ROOT, runner=None):
    """A node claiming DONE whose evidence names a commit that does not exist."""
    out = []
    for n in nodes(doc):
        if (n.get("status") or "").upper() not in CLOSED_STATUSES:
            continue
        text = str(n.get("evidence") or "")
        shas = commit_shas(text)
        if not shas:
            continue
        throwaway = _ran_in_throwaway_clone(text)
        for sha in shas[:4]:
            exists = _commit_exists(sha, repo, runner)
            if exists is None:
                out.append(("NO-DATA", n.get("id"),
                            "could not check whether %s exists" % sha))
            elif not exists and throwaway:
                out.append(("NO-DATA", n.get("id"),
                            "commit %s is a run artifact of a discarded "
                            "temporary clone and cannot be resolved" % sha))
            elif not exists:
                out.append(("DRIFT", n.get("id"),
                            "is %s and its evidence names commit %s, which NONE of "
                            "the known repositories has"
                            % (n.get("status"), sha)))
    return out


def check_status_against_evidence(doc):
    """A status field contradicting its own evidence field, which a peer session
    found on this board today: SCHEDULED beside evidence reading DECIDED."""
    out = []
    for n in nodes(doc):
        status = (n.get("status") or "").upper()
        text = str(n.get("evidence") or "") + str(n.get("superseded_note") or "")
        if status in OPEN_ISH:
            # CASE INSENSITIVE. The first version matched only capitals, so
            # "the work shipped last night" read clean while "SHIPPED" drifted.
            # A record written in ordinary prose is still a record.
            low = text.lower()
            if _status_is_explicitly_reaffirmed(status.lower(), low):
                continue
            said = [w for w in DECIDED_WORDS
                    if w.lower() in low
                    and not _decided_word_is_subpart_scoped_everywhere(
                        w.lower(), low)]
            if said:
                out.append(("DRIFT", n.get("id"),
                            "status reads %s while its own evidence says %s"
                            % (status, ", ".join(said))))
    return out


def check_complaints(doc):
    """A complaint verdict that disagrees with the nodes claiming to close it."""
    tc = doc.get("team_complaints") or {}
    verdicts = tc.get("P_series_verified_2026_08_29") or {}
    if not verdicts:
        return [("NO-DATA", "complaints",
                 "the board carries no verified complaints, so none could be "
                 "compared against the work claiming to close them")]
    closers = {}
    for n in nodes(doc):
        for c in n.get("closes_complaint") or []:
            closers.setdefault(c, []).append(n)
    out = []
    for cid, entry in sorted(verdicts.items()):
        verdict = (entry.get("verdict") or "").upper()
        mine = closers.get(cid, [])
        done = [n for n in mine if (n.get("status") or "").upper() in CLOSED_STATUSES]
        openn = [n for n in mine if (n.get("status") or "").upper() not in CLOSED_STATUSES]
        if done and verdict == "NOT-ADDRESSED":
            out.append(("DRIFT", cid,
                        "reads NOT-ADDRESSED while %s is %s and claims to close "
                        "it: the work moved and the verdict did not"
                        % (done[0].get("id"), done[0].get("status"))))
        if verdict == "ADDRESSED" and openn and not done:
            out.append(("DRIFT", cid,
                        "reads ADDRESSED while the only node claiming to close "
                        "it (%s) is still %s, which is the worse direction"
                        % (openn[0].get("id"), openn[0].get("status"))))
    return out



def check_landed_claims(doc, runner=None):
    """A node claiming its work is LANDED or PUSHED whose commit is not on any
    REMOTE ref.

    The docstring promised this check and audit() never called it, so the tool
    reported "0 drifted" against a board it was not fully checking. That is the
    exact overclaim this whole estate spent a night hunting, produced by the
    newest tool built to hunt it.

    It is also the only one of the five recorded failures that a data model
    change cannot make impossible, because whether a commit reached a remote is
    a fact about a machine somewhere else: local git cannot know it without
    asking. So this check earns its place where the other three arguably do not.
    """
    out = []
    for n in nodes(doc):
        if (n.get("status") or "").upper() not in CLOSED_STATUSES:
            continue
        text = str(n.get("evidence") or "")
        low = text.lower()
        if not any(w in low for w in ("landed", "pushed", "on origin", "origin/main")):
            continue
        for sha in commit_shas(text)[:3]:
            reachable = _on_any_remote(sha, runner)
            if reachable is None:
                out.append(("NO-DATA", n.get("id"),
                            "claims %s is landed but no remote could be asked "
                            "about it" % sha))
            elif not reachable:
                out.append(("DRIFT", n.get("id"),
                            "claims work landed at %s, but that commit is on no "
                            "remote branch in any known repository: it is local "
                            "only, which is the difference between built and "
                            "delivered" % sha))
    return out


def _on_any_remote(sha, runner=None):
    """True when some remote branch contains it, False when none does, None when
    no repository could be asked."""
    asked = False
    for repo in KNOWN_REPOS:
        # exists, not isdir: in a linked worktree .git is a FILE (third
        # instance of the worktree-blindness class; _commit_exists above
        # carries the same fix with the incident note).
        if not os.path.exists(os.path.join(repo, ".git")):
            continue
        run = runner or (lambda cmd, **kw: subprocess.run(
            cmd, capture_output=True, text=True, cwd=repo, timeout=25))
        try:
            proc = run(["git", "branch", "-r", "--contains", sha])
        except Exception:  # noqa: BLE001
            continue       # sbe: allow-silent try the next repository
        asked = True
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return True
    return False if asked else None


def audit(doc, repo=ROOT, runner=None):
    """Every drift, worst first. Returns a list of (severity, subject, detail)."""
    found = []
    found += check_evidence_commits(doc, repo, runner)
    found += check_status_against_evidence(doc)
    found += check_complaints(doc)
    found += check_landed_claims(doc)
    found.sort(key=lambda f: (f[0] != "DRIFT", str(f[1])))
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--roadmap", default=ROADMAP)
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--strict", action="store_true",
                    help="also fail on NO-DATA, which is never a pass but is "
                         "not always actionable")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(args.roadmap, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: could not read %s: %s" % (args.roadmap, exc), file=sys.stderr)
        return 2

    found = audit(doc, args.repo)
    drift = [f for f in found if f[0] == "DRIFT"]
    nodata = [f for f in found if f[0] == "NO-DATA"]
    for severity, subject, detail in found:
        stream = sys.stderr if severity == "DRIFT" else sys.stdout
        print("%-8s %-10s %s" % (severity, subject, detail), file=stream)
    print("record-drift: %d node(s) and complaint(s) checked, %d drifted, "
          "%d unchecked" % (len(nodes(doc)) + 1, len(drift), len(nodata)))
    if drift:
        return 1
    if nodata and args.strict:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
