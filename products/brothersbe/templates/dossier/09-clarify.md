# 09. Open questions

<!-- SBE-TEMPLATE-UNFILLED 09-clarify: this section is still the shipped example.
     Replace it with your own design, then delete this comment. While it is
     here, `sbe_design.py placeholder` FAILs and names this file. -->

## What this is, and who owns it
A reviewer asked for a discussion before anyone planned the work, did not get
one, and nothing recorded that the conversation had been skipped. That is the
complaint this file answers. The other artifacts capture what was decided. This
one captures what was NOT yet decided, while it is still undecided, which is the
only window in which the answer is cheap.

Anyone raises a row: the analyst who cannot tell which of two readings is meant,
the engineer who finds the requirement silent on a case the code must handle,
the tester who cannot see how a rule would be shown true. Whoever can answer,
answers, and signs the row by filling Answer and Answered.

The point is that an unanswered row BLOCKS. At tier two and above,
`sbe_design.py clarify` refuses a verdict while any row here is still open, so a
skipped conversation becomes a refusal instead of a silence. That is the whole
difference between this file and an open-questions list somebody maintains: a
list rots quietly, and a gate cannot.

A design with genuinely nothing open says so by having a table with no rows.
That is different from having no file at all, which reads NO-DATA: nobody
checked, rather than nobody had a question.

## Questions
| ID | Question | Asked by | Asked of | Answer | Answered |
|---|---|---|---|---|---|
| Q1 | When a customer record exists twice with different addresses, which one is the one the invoice uses? | the analyst | the process owner | The most recently verified address, not the most recently edited one. | 2026-08-29 |
| Q2 | If the upstream feed omits the tax code entirely, does the row fail or pass with a default? | the engineer | the analyst |  |  |

Q2 above is deliberately left open, so the shipped example demonstrates the
refusal rather than describing it. A dossier carrying this file unchanged will
fail its clarify check, which is the same discipline every other template here
follows with its own unfilled marker.
