# Context hygiene

LOAD WHEN: the orchestrator's context is filling up, or a compaction or resume has just happened.

(Extracted verbatim from SKILL.md section 9; see SKILL.md for the full law.)

## 9. Context hygiene (the orchestrator stays lean)
Context is the scarcest resource; spend it like money. Grep before read; read line
ranges, not whole files; never ingest raw agent transcripts or logs (ask for the
verbatim gate lines and facts only); reject verbose returns by contract. Everything
worth keeping goes to disk (STATE.md, specs, the vault) the moment it exists, so the
conversation can be lost without losing the project. After a compaction or resume,
trust disk over recollection: re-read STATE.md and git status before acting. Filter
inputs by relevance to the current decision; irrelevant detail is declined, not
skimmed.
ACTIVE FORGETTING, like humans do: when a phase closes, carry forward the distilled
outcome (what landed, what remains) and deliberately drop the journey (superseded
plans, dead paths, resolved churn, old drafts); when a decision supersedes an earlier
one, the earlier one is noise from that moment. Triage arriving content as signal or
noise before letting it occupy attention. The NEVER-FORGET list is exempt from all
forgetting: safety invariants, founder gates, live fences, unmerged work, and open
founder asks. Forgetting applies to noise, never to laws or obligations.

