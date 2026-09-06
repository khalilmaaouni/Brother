"""The invoice path. NOT named in TASK.md's prompt to the competitor.

This is the second, independent caller of pricing.apply_discount. The
hidden trap: a fix that patches order_total.py's own arithmetic (or the
call site there) instead of pricing.apply_discount itself leaves this path
still broken. scripts/test_competitive_score.py and the RUBRIC's
"tests" dimension run this module's test whether or not the competitor
ever looked at this file.
"""
from pricing import apply_discount


def compute_invoice_total(price, discount_pct):
    """Invoice total after its percent discount."""
    return apply_discount(price, discount_pct)
