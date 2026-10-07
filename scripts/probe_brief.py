"""probe_brief: aim one probe run at one lane, written whole or not at all.

THE FAILURE THIS EXISTS FOR. A probe run costs wall clock and a review, and it
is worth either only when it was aimed at something: one lane, one
specification, one build. The brief is that aim written down, so somebody else
can repeat the run, and so the text can be screened before any of it is handed
to a runner.

WHAT IT REFUSES, and why each refusal is here:

  * a lane that is missing, empty, or not a string, because a brief for no lane
    aims at nothing and a caller that got one would run something else
  * a specification path that is missing, unreadable, a directory, or not
    utf-8, raised as ValueError before any write, because a brief built on a
    specification that was never read is a guess
  * a build path or an out path carrying a newline, a carriage return or a
    null byte, because such a path is two values wearing one name and whatever
    reads it next decides which half is real
  * any text carrying a private term, judged with the same fail closed reader
    the build grader uses, so an unreadable names list blocks rather than
    passes
  * a brief over MAX_BRIEF_BYTES, because a brief nobody can read is not a
    brief

HOW IT REFUSES. A withheld brief is a returned string that starts with REFUSED
and no file is written or replaced. A specification this module could not read
is a ValueError. main() returns 1 for both and 2 when the arguments are missing
or the wrong shape.

Python 3, standard library only. Nothing here starts a process and nothing here
reaches the network.
"""
import os
import sys

#: The private terms reader belongs to the build grader, and this module reuses
#: it rather than keeping a second list that could drift away from the first.
#: Without it this module cannot screen text at all, so a missing grade_build
#: refuses at import time rather than running with the screen switched off.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import grade_build  # noqa: E402

#: Every refusal this module returns starts with this, so a caller can tell a
#: withheld brief from a written one without reading either.
REFUSED = "REFUSED: "

#: A brief larger than this is refused: large enough for a real specification,
#: small enough that a reader is still in the room.
MAX_BRIEF_BYTES = 195000

#: The exit codes main() returns, named so a caller and a test can read them.
EXIT_WRITTEN = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

#: The one line printed when the arguments are missing or the wrong shape.
USAGE = "usage: probe_brief.py <spec> <lane> <build> <out>"


def private_terms_block(text):
    """True when text carries a private term, and TRUE whenever unsure.

    The reader is the build grader's, which fails closed: a names file that
    cannot be read, and a text that is not a string, both come back as a hit. A
    blocked brief is withheld, never sent out with an unknown screen.
    """
    return grade_build.private_hits(text) > 0


def _refusal(reason):
    """The one shape of every returned refusal."""
    return REFUSED + reason


def _text_reason(value, label):
    """A reason when value is not a usable non empty string, else None."""
    if not isinstance(value, str):
        return "%s must be a string, got %s" % (label, type(value).__name__)
    if not value.strip():
        return "%s must not be empty" % label
    return None


def _path_reason(value, label):
    """A reason when value is not a usable single line path, else None."""
    why = _text_reason(value, label)
    if why is not None:
        return why
    for char in ("\n", "\r", "\0"):
        if char in value:
            return "%s carries a control character" % label
    return None


def _read_spec(spec_path):
    """The specification text, read as bytes and decoded once as utf-8.

    Every way this can fail is a ValueError raised before any write: a path
    that is not a string, a missing file, a directory, and bytes that are not
    utf-8.
    """
    if not isinstance(spec_path, str) or not spec_path.strip():
        raise ValueError("spec path must be a non empty string")
    try:
        with open(spec_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("spec file is unreadable: %s: %s" % (spec_path, exc))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("spec file is not utf-8: %s: %s" % (spec_path, exc))


def _brief_text(spec_path, lane, build_path, spec_text):
    """The brief laid out as text, before any screen and before any write."""
    return ("# probe brief\n"
            "\n"
            "lane: %s\n"
            "spec: %s\n"
            "build: %s\n"
            "\n"
            "## specification\n"
            "\n"
            "%s\n" % (lane, spec_path, build_path, spec_text))


def _write_atomically(out_path, text):
    """Write text to out_path in one rename, or return a refusal.

    The whole brief lands in a sibling file first and is renamed over the
    target, so a reader never sees half a brief and a failed write leaves
    whatever was already there alone.
    """
    body = text.encode("utf-8")
    parent = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(parent):
        return _refusal("out directory does not exist: %s" % parent)
    tmp = "%s.part-%d" % (out_path, os.getpid())
    try:
        with open(tmp, "wb") as handle:
            handle.write(body)
        os.replace(tmp, out_path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return _refusal("could not write %s: %s" % (out_path, exc))
    return text


def build_brief(spec_path, lane, build_path, out_path):
    """Write the probe brief for one lane to out_path and return that text.

    Raises ValueError when the specification cannot be read, before anything is
    written. Returns a refusal string, with nothing written or replaced, when
    the lane, a path, the private screen or the size limit refuses.
    """
    why = _text_reason(lane, "lane")
    if why is not None:
        return _refusal(why)
    why = _path_reason(build_path, "build path")
    if why is not None:
        return _refusal(why)
    why = _path_reason(out_path, "out path")
    if why is not None:
        return _refusal(why)
    spec_text = _read_spec(spec_path)
    text = _brief_text(spec_path, lane, build_path, spec_text)
    for label, value in (("lane", lane), ("build path", build_path),
                         ("brief", text)):
        if private_terms_block(value):
            return _refusal("private term in %s" % label)
    size = len(text.encode("utf-8"))
    if size > MAX_BRIEF_BYTES:
        return _refusal("brief is %d bytes, over the %d byte limit"
                        % (size, MAX_BRIEF_BYTES))
    return _write_atomically(out_path, text)


def main(argv=None):
    """Write the brief named on the command line.

    EXIT_WRITTEN when a brief was written, EXIT_REFUSED when it was withheld or
    the specification could not be read, EXIT_USAGE when the arguments are
    missing or the wrong shape.
    """
    args = sys.argv[1:] if argv is None else argv
    if not isinstance(args, (list, tuple)) or len(args) != 4:
        print(USAGE, file=sys.stderr)
        return EXIT_USAGE
    for item in args:
        if not isinstance(item, str):
            print(USAGE, file=sys.stderr)
            return EXIT_USAGE
    spec_path, lane, build_path, out_path = args
    try:
        result = build_brief(spec_path, lane, build_path, out_path)
    except ValueError as exc:
        print("%s%s" % (REFUSED, exc), file=sys.stderr)
        return EXIT_REFUSED
    if result.startswith(REFUSED):
        print(result, file=sys.stderr)
        return EXIT_REFUSED
    print("wrote %s (%d bytes)" % (out_path, len(result.encode("utf-8"))))
    return EXIT_WRITTEN


if __name__ == "__main__":
    sys.exit(main())
