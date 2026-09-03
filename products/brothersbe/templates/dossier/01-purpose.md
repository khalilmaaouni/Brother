# 01. Purpose brief

<!-- SBE-TEMPLATE-UNFILLED 01-purpose: this section is still the shipped example.
     Replace it with your own design, then delete this comment. While it is
     here, `sbe_design.py placeholder` FAILs and names this file. -->

## Problem
What is broken or missing, stated without a solution in it.
Example: order confirmations reach the warehouse up to a day late, so
promised ship dates slip and support cannot tell a customer where an order
actually stands.

## Users
Who is affected, and what they do today instead.
Example: warehouse pickers wait for an overnight batch file instead of
seeing orders as they are placed; support agents re-key order status from
the checkout admin screen because there is no single source of truth.

## Success criteria
Observable conditions that mean this worked.
Example: the warehouse sees a new order within minutes of checkout, and
support can answer a status question from one screen instead of two.

## Non-goals
What this explicitly does not do.
Example: this does not change how payments are captured, and it does not
touch the returns process.

## What breaks if this is wrong
The blast radius, named.
Example: if order delivery to the warehouse stays unreliable, orders ship
late or not at all, and support has no way to know which ones until a
customer complains.
