"""The one `--cwd` mechanism every `sbe` subcommand shares.

Before this module existed, `--cwd` was wired by hand into whichever
subcommand happened to need it (`work start`, `work finish`, `task open`,
`evidence run`), and every other subcommand refused it outright:
`./bin/sbe status --cwd .` printed `unrecognized arguments: --cwd`. A
caller running `sbe` from outside the repository it means to inspect had no
uniform way to say so. This module is the single place that flag is spelled,
so every command that gains it gains the SAME semantics rather than a new
one invented per call site.

Three shapes of command exist in this codebase, and each gets its own
helper below:

  - a command with ITS OWN argparse and an existing optional positional or
    `--path`-style flag (`sbe status <path>`, `sbe explain --path DIR`):
    `add_cwd_argument` adds the flag, `resolve` picks the effective target
    between the two, the existing argument winning when the caller actually
    typed one.

  - a command with ITS OWN argparse and a REQUIRED positional that names a
    file or dossier, not a repository (`sbe handover prepare <dossier>`):
    `resolve_relative` lets `--cwd` anchor a RELATIVE positional without
    displacing an absolute one, the same way a shell's cwd anchors a
    relative path without needing to be told the path is already rooted.

  - a command that hands its whole argv, unparsed, to a script under
    `tools/` this package cannot edit (`sbe gate`, `sbe design`, ...): those
    scripts already default to the process's own working directory when no
    directory argument is given (`os.getcwd()`, or an `os.path.isdir` scan
    over argv with the same fallback), so `split` pulls `--cwd VALUE` out of
    the argv before it reaches the script and the caller runs the script
    with its OS-level working directory set to that value instead. The
    script never has to know the flag exists.
"""
import os


#: The one help sentence every `--cwd` flag carries, unless a command's own
#: existing argument already explains the target well enough that repeating
#: it would just be noise (`sbe explain --path`, for instance, already says
#: what the directory is FOR; `add_cwd_argument` still takes an override for
#: exactly that case).
CWD_HELP = ("an alternate way to name the repository or directory this "
            "command operates on, for a caller running from somewhere else "
            "entirely; an existing path argument on the same command line "
            "wins when both are given, and the current directory is used "
            "when neither is")


def add_cwd_argument(parser, help_text=None):
    """Add `--cwd` to `parser`, once, with the shared help text above unless
    the caller has a better one for this specific command."""
    parser.add_argument("--cwd", default=None, help=help_text or CWD_HELP)


def resolve(explicit, cwd, default="."):
    """The effective target directory for a command that already has its
    own optional path argument (`explicit`, whatever it parsed to) plus the
    new `--cwd` (`cwd`, or `None`/empty when not given).

    `explicit` wins the moment it differs from `default`: that is the
    signal the caller actually typed something, as opposed to argparse
    filling in a default nobody asked for. Failing that, `cwd` is used when
    given. Failing THAT, `explicit` itself (which is `default` at this
    point) is returned, so a caller passing neither keeps behaving exactly
    as it did before this flag existed.

    This is why every argparse `add_argument` this module's callers touch
    also has its OWN default changed from `"."` to `None`: with the old
    `"."` default, `explicit` could never be told apart from a caller who
    typed `.` on purpose, and `--cwd` would have silently lost every tie.
    """
    if explicit is not None and explicit != default:
        return explicit
    if cwd:
        return cwd
    return explicit if explicit is not None else default


def resolve_relative(path, cwd):
    """`path`, anchored at `cwd` when `path` is relative and `cwd` is given;
    `path` unchanged otherwise (already absolute, or no `--cwd` at all).

    For a command whose own positional names a FILE or a DOSSIER rather
    than a repository (`sbe handover prepare <dossier>`), there is no
    competing default to arbitrate the way `resolve` does: the positional
    is required, so it is always "explicit". What `--cwd` can still do
    there is exactly what a shell's own working directory already does for
    any relative path typed at a prompt: anchor it. An absolute `path`
    is a caller who already knows exactly where the thing is, and `--cwd`
    saying otherwise is never allowed to override that.
    """
    if not cwd or os.path.isabs(path):
        return path
    return os.path.join(cwd, path)


def split(argv):
    """(remaining argv, cwd or None): pull the FIRST well-formed `--cwd
    VALUE` or `--cwd=VALUE` out of a raw argv list bound for a script this
    package does not own (`tools/sbe_*.py`), none of which may gain a
    second, competing definition of `--cwd`.

    A `--cwd` with nothing after it is left exactly where it was: this
    function only ever removes a flag it can also give a value for, so the
    script it was headed to still sees it, and refuses it exactly the way
    it refuses any other flag it does not recognize, rather than this
    module inventing a second, differently-worded usage error for the same
    mistake.
    """
    out = []
    cwd = None
    argv = list(argv)
    i = 0
    while i < len(argv):
        a = argv[i]
        if cwd is None and a == "--cwd" and i + 1 < len(argv):
            cwd = argv[i + 1]
            i += 2
            continue
        if cwd is None and a.startswith("--cwd="):
            cwd = a[len("--cwd="):]
            i += 1
            continue
        out.append(a)
        i += 1
    return out, cwd
