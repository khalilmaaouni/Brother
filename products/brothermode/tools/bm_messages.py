#!/usr/bin/env python3
"""bm_messages: the one catalog for founder-facing reason strings that more
than one renderer needs word for word.

WHY THIS EXISTS (RF-4, GAP-19, docs/handover/2026-08-11-morning/
03-RULES-AND-PROCESS-FIXES.md)
  The unattended preflight's reason-code rewrites used to live twice: once
  as tools/bm_controller.py's own UNATTENDED_REFUSAL_HELP, and once as a
  hand-typed copy inside tools/bm_visual.py's REFUSAL_HELP, checked for
  equality by tools/test_bm_visual.py. That deliberately duplicated wording
  drifted twice in one night, each drift costing a red suite, because a
  founder-facing wording change made in one file has no way to reach the
  other short of somebody remembering to paste it twice.

  This module is the fix: the wording lives here exactly once, and every
  renderer that needs it imports the same object rather than retyping it.
  tools/bm_controller.py assigns its UNATTENDED_REFUSAL_HELP from this
  module rather than defining its own dict; tools/bm_visual.py folds the
  same object into its own REFUSAL_HELP. tools/test_bm_visual.py's copy
  check now asserts IDENTITY, not equality: the two surfaces cannot merely
  happen to agree today, they share the one object, so they cannot drift
  at all.

  WHY A NEW MODULE AND NOT AN IMPORT OF bm_controller.py DIRECTLY.
  tools/bm_controller.py is one of the two shipping modules allowed to
  import `subprocess` (tools/test_bm.py's own scan names exactly the two).
  tools/bm_view.py loads tools/bm_visual.py to render a page, so if
  bm_visual.py imported bm_controller.py to reach this catalog, subprocess
  would ride along into the render path. This module owns nothing but
  string constants: no I/O, no subprocess, no writes, so either side can
  load it at no such cost.

  SCOPE. This module owns exactly the strings tools/test_bm_visual.py's
  copy-equality test already covered: the unattended-preflight reason-code
  rewrites and the two command strings their next_action text quotes. It
  is not a sweep of every founder-facing string in this project; that
  broader sweep is Loop X1's own stated later step (RF-4's own note in the
  fix list above), not this change's job.

  The reason-code CONSTANTS themselves (UNATTENDED_NO_IDENTITY and its
  siblings) stay defined in tools/bm_controller.py, unmoved: a structural
  scanner in tools/test_bm_visual.py (_controller_reason_codes) discovers
  them by reading bm_controller.py's own module-level source with ast,
  matching a literal string assignment there. Moving them here would blind
  that scanner, which would be a real regression this change must not
  cause. Only the REWRITES (the three-part tuples keyed by the reason-code
  strings) move; the codes are quoted here as literals, not imported, so
  this module keeps zero dependency on bm_controller.py in either
  direction.

Python 3.9, standard library only, no imports at all: every value below is
a literal.

No em or en dashes anywhere in this file, its comments, or its output.
"""

#: The exact remediation for the two fence conditions, as the founder would
#: type it. Quoted inline below rather than restated, so the command a
#: founder is told to run and the command tools/bm_controller.py's own
#: raise sites tell them to run can never read differently.
UNATTENDED_FENCE_MODE_COMMAND = "export BM_FENCE_MODE=enforced"
UNATTENDED_FENCE_STRICT_COMMAND = "export BM_FENCE_STRICT=1"

#: reason code -> (what we were doing, what is wrong, what to do next), the
#: same three-part shape tools/bm_visual.py's REFUSAL_HELP uses for every
#: other code, so a founder meets a sentence rather than a code regardless
#: of which renderer is asking. Keyed by the LITERAL reason-code string
#: rather than by importing tools/bm_controller.py's UNATTENDED_* name
#: constants: this module has no dependency on bm_controller.py, and the
#: literal here is the exact string those constants hold.
UNATTENDED_REFUSAL_HELP = {
    "unattended-no-identity": (
        "getting ready to run on its own, with nobody watching",
        "the run has no stable name for itself, or its records could not "
        "be read, so a second attempt could not tell that it was the same "
        "run coming back",
        "Give the run the same session name every time you start it, and "
        "check that BrotherMode is set up in this folder."),
    "unattended-fence-advisory": (
        "checking that the file protection is really switched on",
        "the protection is in advise-only mode, which means that when it "
        "cannot check a write it lets the write through and prints a note "
        "nobody is awake to read",
        "Switch it to refusing mode in the same terminal you start the run "
        "from, then start again: " + UNATTENDED_FENCE_MODE_COMMAND),
    "unattended-fence-not-strict": (
        "checking that the file protection refuses unclaimed edits",
        "edits to files no piece of work has claimed would be allowed "
        "through, which is the shape an unwatched run's mistakes take most "
        "often",
        "Turn the stricter setting on in the same terminal you start the "
        "run from, then start again: " + UNATTENDED_FENCE_STRICT_COMMAND),
    "unattended-fence-not-proven": (
        "checking that the write fence actually refuses a write, by "
        "running it, not only that the settings meant to turn it on are "
        "set",
        "the fence hook's refusal could not be shown by actually running "
        "it: either it ran and let a foreign Edit write through, or it "
        "could not be run and checked here at all, and the detail line "
        "below tells those two apart",
        "read the detail line: if the hook ran and did not refuse, that "
        "is a defect in tools/bm_fence_hook.py to fix before running "
        "unattended; if it could not be run at all, fix that first. "
        "Either way, this only checks the hook binary itself: it cannot "
        "prove that the runtime you are about to run under actually "
        "calls it on every write it makes."),
    "unattended-no-repository": (
        "finding the version history this run could be undone from",
        "this folder is not a tracked project, or it is not sitting on a "
        "named branch, so there would be no way to put the files back",
        "Run it from a tracked project on its own branch, and check that "
        "branch out by name first."),
    "unattended-dirty-tree": (
        "checking that nothing unfinished is already in the way",
        "files are already changed here, and an unwatched run on top of "
        "them makes your work and its work impossible to tell apart "
        "afterwards",
        "Save or set aside what you were doing, or name each changed file "
        "when you start so it is on record that you meant to leave it."),
    "unattended-foreign-claim": (
        "checking that nobody else is holding the files this run will "
        "write",
        "another session already claimed one of them, and two writers over "
        "one file is the failure this product exists to prevent",
        "Wait for that session to finish, or have it hand the files over, "
        "then start again."),
    "unattended-no-snapshot": (
        "taking the safety copy this run could be rewound to",
        "the safety copy could not be taken, so there would be nothing to "
        "rewind to if the run went wrong",
        "Check that the project's version history is healthy, then start "
        "again."),
}
