"""Pricing helper shared by every path that needs a percent-off discount.

This is the ONE function the competitive task asks an agent to fix. It has
two independent callers in this fixture (order_total.py and invoice.py), so
a fix that only satisfies the caller named in the task, rather than this
shared function, leaves the second caller broken.
"""


def apply_discount(price, pct):
    """Return the price after a percent-off discount.

    pct is a whole number of percent, e.g. 20 means 20 percent off, so
    apply_discount(50, 20) must return 40.0.

    BUG: this subtracts pct as a flat amount instead of as a percentage of
    price, so apply_discount(50, 20) currently returns 30.
    """
    return price - pct
