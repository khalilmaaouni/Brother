# BrotherMode weekly review (run when the session-start nag fires; ~20 minutes)

1. `python3 ~/.claude/skills/brothermode/tools/bm_telemetry.py scorecard`
2. `python3 ~/.claude/skills/brothermode/tools/bm_score.py` (code-graded checks first;
   the LLM judge scores ONLY the residue the code cannot decide)
3. `python3 ~/.claude/skills/brothermode/tools/bm_telemetry.py prediction-audit` and
   `... registry-check <active STATE.md paths>`
4. Score all 9 metrics against RUBRIC.md (floor gates first; a floor fail voids scores).
   Evidence per line; self-cap 8 without the rubric's named external evidence.
   JUDGE ISOLATION (arxiv.org/abs/2410.21819, self-preference bias): the scorer must
   be a FRESH session or subagent given ONLY the evidence bundle (scorecard output,
   ledger extract, git log, registry-check), never the sessions that did the work.
   ANCHORED SCORING (eugeneyan.com/writing/llm-evaluators): score by comparison
   against LAST week's evidence bundle (better/same/worse per metric), not naked
   absolute numbers; absolute scores follow from the comparison.
   ANTI-GOODHART SPOT-CHECK (tianpan.co Goodhart post): before scoring, randomly
   sample 2 claims from the week's session logs and verify them against raw
   evidence (fence line vs git history, gate line vs xcresult); a fabricated claim
   voids the week's scores like a floor fail.
5. Filter telemetry/corrections.jsonl candidates: real corrections become laws with
   a because: clause (founder's underlying reason); false positives get deleted.
   Confirm or retire provisional laws against execution evidence (Voyager gating:
   a law unconfirmed by a later run stays provisional; 60d unconfirmed demotes).
   Before approving a candidate, run `python3 tools/bm_learn.py repeat-check <id>`:
   if it repeats a rule you already settled, the answer is not a second copy of
   the rule, it is the repair the class names.
5b. GROUNDED CORRECTION REVIEW (Loop 8). Run
   `python3 tools/bm_learn.py loop-failures --since 30d`. Read it instead of any
   sentence about corrections trending up or down. Every line is a count of rows
   that exist, so the honest reading is line by line:
   - repeated settled corrections: the same instruction given twice.
   - retrieval misses: the rule existed and never reached the acting model. Fix
     retrieval, not the rule text.
   - compliance failures: the rule was shown and skipped. Fix the behaviour or
     raise the severity to gate.
   - bad-rule candidates: the rule was followed and the work still went wrong.
     This is the only class where editing the rule is the right repair.
   - scope errors: retrieved into work it did not fit. Narrow the scope.
   - unresolved contradictions, rules never retrieved, and rules retrieved but
     always marked not relevant: dead or fighting rules, deprecate or narrow.
   - rework and escaped defects linked to rules, with unattributed outcomes
     listed SEPARATELY and never folded into a rule's record. These are
     outcomes, not instructions: they grade a rule and they never count under
     repeated settled corrections, because you did not say anything twice.
     Running the same rework command twice is one event, not two.
   - NOT MEASURED lines: sample size or attribution is insufficient. Write "not
     measured" in the review. Never estimate a number the rows cannot support.
   For any rule you are about to edit, first run
   `python3 tools/bm_learn.py rule-outcomes <rule-id>`; those are counts, not a
   success rate, and no denominator here is large enough to be one.
6. Read <vault>/50-Reference/pending-amendments.md; land EXACTLY ONE
   consolidation commit to SKILL.md (delta edits only, never a rewrite: ACE context
   collapse, arxiv.org/abs/2510.04618) or record an explicit no-change with reason.
   FIRST verify last review's amendment against the signal it named and revert
   it if not strictly better; rejected candidates keep their reason in the note,
   never re-proposed without new evidence (SkillOpt validation gating).
   EVALUATOR-OPTIMIZER (Anthropic building-effective-agents): the drafter writes
   the delta; a FRESH-context critic session gates it against RUBRIC.md and the
   week's ledger before it commits; unresolved disagreement goes to the founder.
7. Harvest auto-memory: scan ~/.claude/projects/*/memory/MEMORY.md for entries worth
   promoting to the vault (pointer lines stay, substance moves).
8. Proportionality pass: OVERTHOUGHT/UNDERTHOUGHT/CARRIED-NOISE flags in project
   OUTCOMES.md files; adjust triage defaults if a pattern holds (3+ occurrences).
9. Felt-outcome batch (ratify the channel with your founder first): send the
   founder ONE message listing the week's 3 to 5 delivered things, each with a
   1-to-5 ask (60 seconds total). Record each reply via
   `... bm_telemetry.py rate --score N --task "..."`. Unanswered stays unrated,
   never inferred; two consecutive unanswered batches get raised as a rubric
   question (the alignment metric cannot move without this feed), because: a
   human taste signal is the one thing the ledger cannot fake.
10. Failure-watch list (pending-amendments file, bottom section): check each.
11. Close: `python3 ~/.claude/skills/brothermode/tools/bm_telemetry.py review-mark "<one-line summary>"`
    (review-mark appends to reviews.jsonl automatically); write one session log in
    the vault.
