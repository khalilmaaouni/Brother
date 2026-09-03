Status: UNAUDITED reference as of 2026-08-06. This page sits outside the mechanical truth guards and is scheduled for the documentation sweep; treat any dated or numeric claim inside with care until then.

# Why this file is short

It used to be 534 lines, about 10,000 tokens, and Claude Code loaded ALL of it
into every session before any work began, whether the task was a typo fix or a
full audit. That cost was paid on every trivial task and bought nothing on most of
them.

Nothing was deleted. Every line moved to references/ verbatim, verified
mechanically: 36,102 characters out, 36,102 characters in, difference zero. What
changed is WHEN it is read.

Two things stayed inline, and the reason each stayed is the design in miniature.
The triage stayed because it IS the routing decision, so deferring it would defer
the choice of what to defer. The safety floor stayed because it is unconditional,
and because most of it is now enforced by a hook rather than by prose you have to
remember. A law the machine checks does not also need reciting into your context.
