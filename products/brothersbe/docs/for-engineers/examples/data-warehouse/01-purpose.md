# 01. Purpose brief

## Problem
Revenue reporting is assembled by hand from three exports every Monday. The
numbers disagree with the billing system often enough that finance re-checks
them, and nobody can say which of the three exports is authoritative for a
refunded subscription.

## Users
Finance analysts, who currently join exports in a spreadsheet. Product managers,
who read a dashboard built on a fourth copy of the same data and do not know it
lags by a week.

## Success criteria
One revenue mart, refreshed daily, whose subscription revenue total reconciles to
the billing system within one cent. Every analyst question answerable from the
mart without joining an export by hand.

## Non-goals
This does not change how invoices are generated, does not model tax, and does not
replace the finance close process.

## What breaks if this is wrong
A wrong revenue mart is worse than no mart, because a dashboard reads as
authoritative. Refund handling is the specific risk: a refund counted as revenue
overstates the month and nothing downstream would notice.
