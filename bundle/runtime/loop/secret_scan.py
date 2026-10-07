"""secret_scan: the one table of what a credential looks like.

WHY THIS FILE EXISTS. Two guards look for passwords and keys before work leaves
the machine: one when a change is committed, one when it is pushed. Each kept
its own list of what a secret looks like, and the lists disagreed in both
directions, so a build passed one gate and was refused by the other. This module
is the single definition both read.

TWO TABLES, ON PURPOSE.

FAMILIES is the union. It is the rule for text that is being ADDED to the
repository: what the commit gate scans, and what the push gate scans on the
added lines of an outgoing range.

STRICT_FAMILIES is the narrower table the push gate already carried, kept
verbatim and in the same order. It is the rule for text that is only CONTEXT or
REMOVED. It stays, because a context line is either already on the remote or was
added earlier in the same range, and narrowing a security gate is not this
module's call to make.

Every STRICT match is also a UNION match. A test holds that invariant, so the
two can never again disagree in the direction that let a value through the
commit gate and saw it refused at the push.

WHY THE PATTERNS ARE WRITTEN WITH HEX ESCAPES. This file must contain the shapes
it forbids in order to forbid them, and a scanner that matches its own source
refuses every change that touches it. Writing the first character of a shape as
a hex escape keeps the regex identical at runtime while the source never carries
the bare literal. Never "simplify" these back to plain text.

EVERY PUBLIC FUNCTION CHECKS ITS ARGUMENT FIRST. A public function handed
something that is not text raises ScanInputError, never a bare interpreter
exception and never a count of zero. A scanner that answers zero for input it
could not read is the exact confusion this estate keeps paying for.
"""
import re
from typing import List


class ScanInputError(ValueError, TypeError):
    """A public function was handed something that is not text.

    It is BOTH a ValueError and a TypeError on purpose. The module's own
    vocabulary calls it a refusal, and ValueError is what this estate reads as
    one; the behaviour callers already caught was a TypeError. Inheriting from
    both means neither caller has to change.
    """


def _require_str(value, name):
    """Refuse anything that is not text, BEFORE any work is done.

    This is the one validation every public function routes through. It is
    private on purpose: a caller handed a second argument in the wrong place
    must get a loud interpreter error, not a quiet refusal.
    """
    if isinstance(value, str):
        return value
    raise ScanInputError(
        "%s expects text, got %s" % (name, type(value).__name__))


#: The union. Ordered, named pairs of (family name, compiled pattern).
#: The eight structured alternatives keep their case, because their case IS the
#: format. The five word like alternatives are compiled with re.I, because
#: uppercase, mixed case and lowercase spellings of an assignment are the same
#: mistake.
#: The fenced private key header of the commit table is replaced here by the
#: push table's fence free header, which is a superset of it: the fenced form is
#: that header with five dashes on either side.
FAMILIES = (
    ("openai-key", re.compile(
        r"(?<![A-Za-z0-9_])\x73k-[A-Za-z0-9_-]{16,}")),
    ("aws-key-id", re.compile(r"\x41KIA[0-9A-Z]{8}")),
    ("aws-secret-assignment", re.compile(
        r"\x61ws_secret_access_key\s*=\s*\S{8}")),
    ("github-classic-token", re.compile(r"\x67hp_[A-Za-z0-9]{8}")),
    ("github-fine-grained-token", re.compile(
        r"\x67ith(?:ub)?_pat_[A-Za-z0-9_]{8}")),
    ("slack-token", re.compile(r"\x78ox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws-session-id", re.compile(r"\x41SIA[0-9A-Z]{8}")),
    ("private-key-header", re.compile(r"\x42EGIN [A-Z ]*PRIVATE KEY")),
    ("password-assignment", re.compile(r"\x70assword\s*=\s*\S{4}", re.I)),
    ("passwd-assignment", re.compile(r"\x70asswd\s*=\s*\S{4}", re.I)),
    ("bearer-token", re.compile(r"\x62earer [A-Za-z0-9._-]{16}", re.I)),
    ("api-key-assignment", re.compile(
        r"\x61pi[_-]?key\s*=\s*\S{8}", re.I)),
    ("secret-key-assignment", re.compile(
        r"\x73ecret[_-]?key\s*=\s*\S{8}", re.I)),
)

#: The bare patterns of FAMILIES, in the same order.
UNION = tuple(pattern for _, pattern in FAMILIES)

#: The push gate's own narrower table, moved verbatim and in the same order.
#: Names are given so a refusal can say which shape was seen without ever
#: printing the value.
STRICT_FAMILIES = (
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("aws-key-id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-classic-token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("private-key-header", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
)

#: The bare patterns of STRICT_FAMILIES, in the same order.
STRICT = tuple(pattern for _, pattern in STRICT_FAMILIES)


#: The stages at which a gate refuses a value. "commit" is the commit gate
#: scanning the added lines of a staged diff. "push-added" is the push gate
#: scanning the added lines of an outgoing range. "push-any" is the push
#: gate scanning the whole patch log, where context and removed lines live.
GATE_STAGES = ("commit", "push-added", "push-any")


def gate_refuses(text: str, stage: str) -> bool:
    """True when a gate at `stage` refuses `text`.

    This is the one place both gates must reach their verdict from, so a
    value the commit gate refuses cannot then be passed by the push gate,
    and the reverse cannot happen either.

    Refuses anything that is not text, and any stage not in GATE_STAGES,
    with ScanInputError. Never returns False for text it could not read.
    """
    _require_str(text, "gate_refuses")
    if stage not in GATE_STAGES:
        raise ScanInputError(
            "gate_refuses: unknown stage %r" % (stage,))
    if stage in ("commit", "push-added"):
        return count(text) > 0
    return any(pattern.search(text) for pattern in STRICT)

#: Values that match a shape but are public by construction: the example access
#: key id AWS prints in its own documentation, which the products' credential
#: fixtures and a teaching page reproduce on purpose. A VALUE allowlist, never a
#: path one. Written by CONCATENATION on purpose: a contiguous literal here
#: would be the exact shape this module exists to refuse.
KNOWN_PUBLIC_EXAMPLE_VALUES = ("AKIA" + "IOSFODNN7" + "EXAMPLE",)


def strip_public_examples(text: str) -> str:
    """The text with every KNOWN_PUBLIC_EXAMPLE_VALUES entry removed, so a
    shape search that follows cannot match a documented example."""
    _require_str(text, "strip_public_examples")
    for example in KNOWN_PUBLIC_EXAMPLE_VALUES:
        text = text.replace(example, "")
    return text


def count(text: str) -> int:
    """The number of UNION matches in text. Counts only, never the match.

    A gate that prints the value it caught has leaked it into the terminal, the
    log, and whatever scrapes them. This returns a number, and a number is all
    any caller ever gets.
    """
    _require_str(text, "count")
    return sum(len(p.findall(text)) for p in UNION)


def families(text: str) -> List[str]:
    """The sorted names of the UNION families that match text. Names only,
    never a matched value."""
    _require_str(text, "families")
    return sorted(name for name, pattern in FAMILIES if pattern.search(text))


def added_text(diff: str) -> str:
    """Every byte the diff ADDS: hunk content, plus the paths it touches.

    WHY NOT the one liner this replaced, which was
        line.startswith("+") and not line.startswith("+++")
    A real added line whose own content begins with two plus signs renders in a
    unified diff as a line beginning with THREE, and that rule threw it away as
    though it were a file header. Inside a hunk the first character is the
    marker and the rest is content, whatever the content looks like.

    PATHS ARE SCANNED TOO. A file NAME carrying a private term reaches a remote
    just as surely as a file body carrying one.

    FAIL DIRECTION: a line this function cannot classify is treated as added
    content and scanned, never skipped. Being scanned costs a false positive a
    human resolves; being skipped costs the thing a scanner exists to stop.

    THE HUNK END RULE. Inside a hunk, a line that starts with none of a space, a
    plus, a minus or a backslash (an empty line included) ends the hunk, and is
    then handled as a header line: skipped only when it starts with a NAMELESS
    prefix, otherwise its name part is scanned. Git never prints such a line
    inside a hunk of a staged diff, so commit behaviour is unchanged. In a
    `git log -p` patch it makes the next commit's header and message lines
    scanned instead of dropped, which is where a value added nowhere else was
    invisible before.

    KNOWN AND NOT COVERED, stated rather than implied: a binary file's contents
    never appear in a textual diff at all, so no text scanner can see them.
    """
    _require_str(diff, "added_text")
    out, in_hunk = [], False
    for line in diff.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if line.startswith("diff --git ") or line.startswith("index "):
            in_hunk = False
        elif in_hunk:
            if line.startswith("+"):
                out.append(line[1:])
                continue
            if line.startswith(" ") or line.startswith("-") \
                    or line.startswith("\\"):
                continue
            # A line nobody classifies ends the hunk and is scanned below.
            in_hunk = False
        if not line.startswith(NAMELESS):
            name = next((line[len(prefix):] for prefix in NAMED
                         if line.startswith(prefix)), line).strip()
            if name and name != "/dev/null":
                out.append(name)
    return "\n".join(out)


#: Header lines that carry no name. Skipped only so a hash or a mode is never
#: read as content.
NAMELESS = ("index ", "similarity index ", "dissimilarity index ",
            "old mode ", "new mode ", "new file mode ",
            "deleted file mode ")
#: Header prefixes stripped before the name is scanned. The +++ and --- strips
#: keep a header from ever being taken raw; the rest are tidiness, since the
#: whole line would be scanned anyway.
NAMED = ("diff --git ", "rename from ", "rename to ", "copy from ",
         "copy to ", "+++ ", "--- ")
