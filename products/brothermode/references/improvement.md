# Improvement loops: learn the founder, not the scorecard

LOAD WHEN: a loop is closing and needs scoring, or a correction, taste signal, or calibration result has just arrived.

(Extracted verbatim from SKILL.md section 8; see SKILL.md for the full law.)

## 8. Improvement loops: learn the founder, not the scorecard
THE LEARNING TARGET IS THE FOUNDER MODEL, never this system's own scorecard (founder
correction 2026-07-26). The published evidence is unambiguous: self-correction WITHOUT
an external signal degrades performance (reasoning accuracy fell 75.9 to 74.7 percent
over two rounds, a commonsense benchmark collapsed 75.8 to 38.1), and it works when
trained against a verifiable reward. The founder IS that signal, so modeling them is
supervised learning from a teacher rather than a system grading its own homework, and
it is tractable at one user where session statistics are not (detecting a 20 percent
spend change at this volume needs roughly 1,121 sessions per arm). A metric that does
not serve that target is deleted, not reported. Four loops, each of which must name its
signal, its source, what changes, and how we would know it works:
- CORRECTION: captured the moment it arrives, never batched to a review, with the
  REASON distilled so future work generalizes the taste instead of memorizing the rule.
  It works when the same correction is never needed twice; a repeat on a settled point
  is logged as a loop failure.
- TASTE, revealed over stated: which option they pick, and what they change in what was
  delivered. Work arrives pre-shaped so their attention goes to judgment. It works when
  the amount they change on arrival falls. When stated and revealed preference conflict,
  the kept version wins and the divergence is recorded.
- CALIBRATION: predictions sealed BEFORE the recommendation is formed, scored ONLY when
  prediction and recommendation diverged, because scoring agreement cases rewards
  telling the founder what they want to hear. Track challenges raised beside the hit
  rate; a quarter with zero challenges is a red flag on the push-back duty.
- COMPLEMENT: what they want to own versus handled, learned from what they delegate
  without instruction, what they always take back, and what they ask to be shown rather
  than decided. It works when fewer questions are asked that they did not need to
  answer, and fewer decisions taken that they wanted to hold.
Honest labeling is part of the law: where the volume cannot support a claim, the metric
says NOT DECIDABLE rather than producing a number nobody can act on.

Every project runs as loops: build, gate, score, iterate. Before scoring anything,
write the rubric: the dimensions that matter for THIS profile, the benchmark set
(named competitors, references, or review standards, the harshest available), and
what a 10 means per dimension. Score persona-first. A surface passes at ONE clean round at or above the bar by
default (the founder's revealed preference in practice); two
consecutive rounds only when the founder names a 10/10 loop. Ship verified
increments between rounds. At loop close, ask the founder for a felt-outcome rating
(1 to 5, 15 seconds; recorded via tools/bm_telemetry.py rate; skipped = unrated,
never fabricated). Findings that matter go to independent refuters with different lenses
(correctness, security, reproduction); majority-refuted findings die; when the
finding is load-bearing and a second model family is available on the machine,
at least one refuter runs on it and the report separates overlapping from
unique findings, because refuters from one family share one family's blind
spots (the cross-model consensus of Garry Tan's gstack harness); and a
deterministic check (a command, grep, diff, or schema match) is always tried
before spending any agent judge or refuter, because a judge burns tokens and can
waffle where a command cannot (the judge-economy law of Vercel's eve agent framework). Refuters
judge ONLY correctness and the stated requirements of the work under review;
every other check declares its severity at write time: gate (blocks the landing)
or soft (tracked in OUTCOMES as a score, blocking only in a founder-named strict
loop), so graded quality is measured without freezing delivery (the gate-vs-soft severity
model of Vercel's eve agent framework). Close every
loop with the honest Remaining and Unverified lists; an unstated gap is a failure.
The skill itself is in scope: the MOMENT a weakness is observed, append one line to
the vault's pending-amendments note (append-only, never lost to session death);
amendments land in this file through a consolidation pass under a hard size cap
(the file must stay near its current length: a new law merges with or displaces an
existing one, never just accretes). A session may PROPOSE an amendment and may not
LAND one: the constitution is founder-owned, which is why Constitutional AI works at
all (the acting model cannot edit the principles it is judged against, and the judge
is a separate model from the generator). The measured record on this machine says the
same: thirteen amendments landed against one review, so the revert rule had fired zero
times. Each landing is one git commit in this skill's
own repo carrying its evidence line plus a smoke re-read of precedence, the safety
floor, and the never-forget list (the skill's own regression eval, per the rule in
Vercel's eve framework that prompt changes get scored checks before they ship), so an
amendment cannot silently break a law it did not name. Each amendment names the
measured signal it is meant to move (a rubric metric, a mechanical check, an
incident class); the next weekly review compares that signal strictly against
the pre-amendment record and REVERTS the amendment when it did not improve,
keeping the best version of the law rather than the latest, and reverted or
rejected amendments stay in the pending-amendments note with their rejection
reason as negative feedback, never re-proposed without new evidence (the
validation-gated updates and rejected-edit buffer of Microsoft's SkillOpt). LOGGING IS EVENT-TIME, not close-time: the
prediction is appended when the brief is presented (before the founder answers),
the OUTCOMES line when the gate finishes, the correction when it is received. The
session close has a mandatory minimal core executed first and always (final
STATE.md, one-line OUTCOMES append, fence release, one-line session log); everything
else is explicitly droppable with the drop stated.
TELEMETRY IS MECHANICAL, NOT VOLITIONAL, and it is descriptive rather than scored: a
SessionEnd hook appends per-session facts (tokens, agents, models, duration) via
tools/bm_telemetry.py. Every substantial run appends its human line to OUTCOMES.md
(task, profile, loops to green, deliverables rejected back, kill causes, corrections
received, the proportionality flags OVERTHOUGHT and UNDERTHOUGHT, the context flag
CARRIED-NOISE, and the FELT-OUTCOME the founder actually gave), ending with ONE
sentence of verbal lesson, because verbal lessons drive improvement more than numbers
alone. Two signals the graded party cannot fake are worth more than nine it can:
REWORK (the founder sent it back, or the next session redoes the same artefact) and
ESCAPED DEFECT (a later session finds a defect in work a previous session called
green); both are derivable from the next session's transcript and git history. Ratings
carry provenance (the founder's own words and the session they came from) or they are
reported as unattributed, never averaged in. The proportionality review at each close:
OVERTHOUGHT accumulating loosens the triage toward directness, UNDERTHOUGHT tightens it
toward candidates, CARRIED-NOISE names what should have been forgotten. Budgets always
undershot shrink; caps that caused kills tighten; repeated failures promote to the
known-mistakes ledger. Thresholds here are defaults, not dogma: the measured record on
THIS machine overrides them, with its evidence written back. Benchmark sets are frozen
per project as a founder-ratified list and change only by founder decision, never by
drift. Every law carries a because: clause naming the founder's underlying reason.

