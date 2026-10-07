"""brother_state: where a tool that keeps records beside itself keeps them. ONE rule, in one place.

WHY THIS EXISTS. Measured 2026-10-06 in a copy holding only bundle/, which is all an installed plugin carries: the
engine (brother_run.py) and the annotations store (annotations_store.py), run the way the shipped prose gives, each
wrote under the parent of its own folder. In a checkout that parent is the repository. In an install it is the plugin
root, which the host replaces on update, so a run's records and a correction "kept from then on" were kept until the
next update. Meanwhile the brother-run launcher computed a per user default of its own, so the two ways of starting
the same engine kept one run's records in two places and "continue" through the door could not see them.

Two tools each re-deriving "am I installed?" is how that happened, so the question is answered here once and every
tool asks: brother_run.default_runs_root returns state_root(), annotations_store takes its ROOT from it, and decide.py
reads the store through annotations_store's own name.

WHY NOT brother_paths.py, where the other path rules live: that file has two product copies held to it by a drift
check, each product with a checksums manifest of its own. This rule concerns only the tools mirrored into the bundle
runtime, so it stands alone, standard library only.

THE RULE.
  A development checkout (the tool is the source, <repository>/scripts/<tool>) keeps its records in its own
  repository, as it always has, whatever STATE_ROOT_ENV says.
  Everything else goes per user: an installed plugin (the shipped mirror under <plugin root>/runtime), and ALSO a
  layout that cannot be told apart, because unknown must never write into a folder an update deletes.
  STATE_ROOT_ENV names the per user place. A leading ~ is the user's home. A RELATIVE value is refused by name
  (SystemExit), never resolved against whatever folder the tool was started from, which is usually the very
  repository being worked on.

KNOWN LIMIT, stated rather than hidden: an install is told from a checkout by the tool's folder NAME. An engine in a
plugin folder literally named scripts would write beside itself. No known host layout does this.
"""
import os

#: Per user, the place the brother-run launcher has always used.
PER_USER_STATE_ROOT = os.path.join("~", ".claude", "brother-run")
#: The launcher's own name for another place, kept.
STATE_ROOT_ENV = "BROTHER_RUNS_ROOT"


def state_root(here, env=None):
    """(root, in a checkout) for a tool whose own folder is `here`. A `here` that is not a non empty str is refused
    with ValueError; an `env` that is not a mapping reads as naming nothing."""
    if not isinstance(here, str) or not here:
        raise ValueError("state_root wants the tool's own folder, a non empty str")
    if os.path.basename(here) == "scripts":
        return os.path.dirname(here), True
    try:
        raw = (os.environ if env is None else env).get(STATE_ROOT_ENV)
    except (AttributeError, TypeError):
        raw = None
    named = os.path.expanduser(raw.strip()) if isinstance(raw, str) else ""
    if named and not os.path.isabs(named):
        raise SystemExit("brother: %s=%s is not an absolute path (a leading ~ is allowed). A relative value would be "
                         "resolved against the folder the tool was started from, so it is refused: nothing was "
                         "read or written." % (STATE_ROOT_ENV, named))
    return os.path.abspath(named or os.path.expanduser(PER_USER_STATE_ROOT)), False
