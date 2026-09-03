# L17, L18 and L19: closing the run, and reviewing work

LOAD WHEN: a session is ending, a milestone is landing, or work is about to be reviewed, scored or judged.

(Extracted verbatim from SKILL.md, Laws L17, L18 and L19. The routing table in SKILL.md names when to load this file.)

### L17. The run closes on disk
WHEN: a session ends, or a milestone lands.
INPUTS: the telemetry ledger (the session lines of the last 7 days) and the vault session-log filenames and modification dates.
RULE: every day that carries a session in the ledger carries a session log in the vault, dated either by filename or by modification date. An active day with no log fails.
OUTPUT: proceed, or stop and ask (write the missing log before the session closes).
ENFORCED BY: `tools/sbe_score.py` (vault-log-per-active-day, fed by the `tools/sbe_telemetry.py` SessionEnd hook, which writes by hook and not by promise). The rest of the close is human review at `tools/WEEKLY-REVIEW.md`, because no check reads it: updated open items, an updated failures index, a closing scorecard whose every line names its evidence, the self-score cap of 8 with a 9 or 10 needing external evidence named (a passing CI run, a reviewer approval, a reproduced number), NO-DATA as a legal score, and the Remaining and Unverified lists stated rather than implied. The ledger-coverage check in the same tool counts sessions: it reports NO-DATA when no session is recent, PASS when sessions are, and FAILS when the ledger itself cannot be read, because a ledger holding a line that is not JSON is a broken record rather than an absent one. That last case blocks a merge wherever CI is pointed at a vault, which the shipped workflow does not do: it sets no `BROTHERSBE_VAULT`, so on a stock runner every ledger check is NO-DATA at exit 0 and this law blocks nothing until an operator points it at their own vault. This law used to say the check cannot fail, which was wrong about its own tool in the direction nobody checks for.

### L18. A deterministic check runs before any judge
WHEN: work is about to be reviewed, scored, or judged by a model, including by this project's own weekly review.
INPUTS: the work product, and the candidate deterministic checks: a command, a grep, a diff, a schema match.
RULE: before any model is asked to judge work, the deterministic check is tried first, and the record names which one answered. A question a command can answer is never spent on a judge.
OUTPUT: proceed, naming the check that answered; a judge sees only the residue no command decides.
ENFORCED BY: human review. `tools/WEEKLY-REVIEW.md` step 2 already orders the code-graded checks before the LLM judge, and that step is the one place this law has a mechanical echo; nothing verifies that any other review did this.

### L19. A review verdict counts only with an executed falsification
WHEN: any review, refutation, or audit verdict is about to be recorded.
INPUTS: the verdict, and the actions its author actually ran.
RULE: a verdict counts only when it names the falsification actually executed: re-ran the command, reproduced the defect, re-derived the number. Reasoning alone, however plausible, is NO-DATA rather than a finding.
OUTPUT: proceed with the named falsification, or record NO-DATA.
ENFORCED BY: human review. [RUBRIC.md](RUBRIC.md) metric 6 grades it at the weekly review and the review prompt reads for it; no tool parses a verdict for its falsification, so between reviews this is a stated discipline.
