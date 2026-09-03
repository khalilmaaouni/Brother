# Definition of done

LOAD WHEN: a task is about to be called done, a review is being run, or acceptance of any piece of work is being decided.

A task is accepted only when every point below holds. "Done" is never a state
a worker declares about itself; it is the result of this checklist passing.

1. The user value is stated.
2. The change uses the project's canonical objects, not a private side format.
3. The writer scope is declared (which files this work was allowed to change).
4. The task has a time and work-budget forecast (references/forecasting.md).
5. The implementation is isolated where appropriate (a separate draft
   workspace for risky or parallel work).
6. Tests are added at the right level.
7. A calibration proves the test can fail for the intended defect.
8. Verification runs after the final edit, not before it.
9. An independent reviewer evaluates substantial work; the writer and the
   reviewer are never the same without a stated exception.
10. Documentation generated from facts is updated, never hand-edited around.
11. Beginner language is reviewed against references/terminology.md.
12. Existing installs can migrate, or receive a clear refusal, never silent
    breakage.
13. The project pulse reflects the result (references/pulse.md).
14. Actual time and tokens are recorded when available; "not measured" is the
    honest entry when they are not.
15. Remaining uncertainty is stated.

Point 8 restates the safety floor law and never overrides it: no completion
claim without a verifying command run after the last edit, quoted.

Each point above resolves to pass, not-yet, or NO-DATA. NO-DATA applies
when the evidence could not be read or the check did not run, never when
the check ran and failed. NO-DATA is never a pass and never a block on its
own: it names what could not be read and the next action that would
produce the evidence.
