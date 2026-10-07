#!/usr/bin/env python3
"""Make every state change on the plan board ATTRIBUTABLE: who changed it, by what mechanism, when, on what evidence.

usage (as a library, from any loop program):
    import provenance as P
    P.apply(unit, "close_unit.py", because="`python3 -m unittest x` exit 0")
    P.journal(dict(P.stamp("close_unit.py"), unit="BL2", was="OPEN", now="DONE"))

WHY THIS EXISTS. On 2026-09-21 a DONE state and a closure paragraph appeared in the launch plan at 21:25:12 and
nobody could say who wrote them. The audit found the reason rather than an anomaly: close_unit.py sets
unit["state"] = "DONE", stamps state_at, appends a generated closure paragraph, records NO AUTHOR, and runs
unattended on every loop pass. Across all 56 units the board carried no provenance field of any kind (only
"decided_by", on 2 of 56), git attributed every commit to the same human, and 58 sessions were live on this
machine, several on this estate. So the board could not answer "who changed this, when, by what mechanism, and
on what evidence". That was its NORMAL OPERATING MODE.

THE LIMIT, stated plainly because it decides what this module may be used for. A PROVENANCE FIELD IS SELF
ATTESTATION, NOT PROOF. Any writer can put any value in changed_by, including a false one, and nothing here
verifies it: there is no signature, no second party, no key. This CANNOT stop a hostile actor and must never be
described, in a report or a gate, as though it could. What it genuinely buys is that ACCIDENTAL and AUTOMATED
writes become attributable, and that is most of the real value here, because nearly every write on this board is
a concurrent session doing its job without knowing the other 57 exist. It turns a mystery into a question
someone can re-run.

WHY changed_because CARRIES THE WEIGHT. "DONE by close_unit.py" is weak: it says a program did it and stops
there. "DONE by close_unit.py on donecheck_bl.py exit 0" is A CLAIM SOMEONE CAN RE-RUN, which is the only kind
of attribution this estate counts. It is optional because a caller may genuinely have no command to name, and
strongly preferred because a stamp without it records the mechanism and loses the reason."""
import datetime
import json
import os

JOURNAL = os.path.expanduser("~/.claude/evidence/brother-board-transitions.jsonl")

# Env names searched IN THIS ORDER, all four MEASURED PRESENT on this machine 2026-09-21 (the list is not
# invented: `env | cut -d= -f1` was read first). Order is by how well the value distinguishes ONE writer from
# the other 57, which is the whole question the board could not answer:
#   CLAUDE_CODE_SESSION_ID       a uuid unique per agent session. The one id that separates two concurrent writers.
#   CLAUDE_CODE_HOST_SESSION_ID  the desktop window. Shared by an agent and its children, so it groups, not separates.
#   CLAUDE_PID                   the process. Unique while alive, reused after, and meaningless once it exits.
#   AI_AGENT                     the harness build string, IDENTICAL in every session on this machine, so it can
#                                never separate two writers. Last on purpose: it says only "an agent, not a hand".
_ENV_KEYS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_PID", "AI_AGENT")


def env_actor(environ=None):
    """(NAME, value) of the first plausible identity found in the environment, or None. Separate from
    resolve_actor so a caller (and the test) can ask WHAT WAS FOUND rather than only what was recorded."""
    env = os.environ if environ is None else environ
    for key in _ENV_KEYS:
        val = env.get(key)
        if isinstance(val, str) and val.strip():
            return key, val.strip()
    return None


def resolve_actor(actor=None, environ=None):
    """Resolve the actor, NEVER accept one blindly, and never silently invent a name.

    An env derived actor is recorded as "NAME=value", not as the bare value: a bare uuid or a bare pid could be
    read back as a person's identifier, and the field would then assert more than was actually known. Carrying
    the variable name says exactly what kind of id this is and where it came from, which is the difference
    between an attribution and a guess. With nothing at all the answer is "hand", which is a real statement
    (no automation identified itself) rather than an empty string a reader could skip over."""
    if isinstance(actor, str) and actor.strip():
        return actor.strip()
    found = env_actor(environ)
    return "%s=%s" % found if found else "hand"


def stamp(via, because=None, actor=None):
    """The four keys, and exactly the four keys, that make a board change attributable.

    via is the MECHANISM ("close_unit.py"), passed by the caller because only the caller knows which program it
    is; a module that guessed from sys.argv[0] would name a test runner or a shell wrapper."""
    # A REAL CLOCK READING, never a narrative time. The estate's standing law came from a progress page whose
    # timestamps were written from a session's sense of elapsed time and were four hours wrong. astimezone()
    # attaches the real offset, so the reading stays comparable across machines and across a DST boundary.
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return {"changed_by": resolve_actor(actor),
            "changed_via": str(via).strip() or "unknown-mechanism",
            "changed_at": now,
            "changed_because": because.strip() if isinstance(because, str) and because.strip() else None}


def apply(record, via, because=None, actor=None):
    """Merge a stamp into a dict and return it. Only the four provenance keys are touched; every other key the
    record already carries survives, because this runs on live plan units whose other fields are the work."""
    if not isinstance(record, dict):
        raise TypeError("apply() needs a dict to stamp, got %s" % type(record).__name__)
    record.update(stamp(via, because, actor))
    return record


def journal(entry, path=None):
    """Append ONE line of JSON to the append only transition journal. True when written, False when not.

    WHY A JOURNAL AT ALL. The board is MUTABLE CURRENT STATE: each write overwrites what the last one said, so
    the board can only ever answer what is true now. History has to be immutable and separate. Git gives file
    history but not SEMANTIC history, and an amend, a squash or a rebase loses it outright, which on this estate
    happens weekly.

    ATOMICITY, and its ceiling. Opened in append mode and written in ONE write() call, so two concurrent writers
    cannot interleave halves of a single line: with O_APPEND the kernel seeks to the end and writes as one
    operation. ponytail: this is the local filesystem guarantee, which is the case that exists here; a very long
    line over NFS could still tear, and the upgrade path is a lock file, not a bigger write.

    NEVER RAISES, and that direction is deliberate: a provenance failure must not block the work it describes. A
    full disk must not stop a unit closing. The cost is a silent gap, which is why the verdict is RETURNED: a
    caller that wants to know can check, and a False can never be mistaken for a written line."""
    try:
        line = json.dumps(entry) + "\n"          # default ensure_ascii escapes any newline inside a value, so
    except (TypeError, ValueError):              # one entry stays one line whatever the caller put in it
        return False
    try:
        target = path or JOURNAL
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except OSError:
        return False
