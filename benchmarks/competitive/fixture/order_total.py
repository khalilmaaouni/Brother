"""The order-total path. This is the caller the task instructions name."""
from pricing import apply_discount


def compute_total(price, discount_pct):
    """Total for one order line after its percent discount."""
    return apply_discount(price, discount_pct)
