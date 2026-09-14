"""The export path. NOT named in TASK.md's prompt to the competitor.

A second, independent caller of chunk(), exercising the two boundary
cases the visible test never touches: an empty list, and a length that
divides evenly into the chunk size.
"""
from util import chunk


def export_batches(items, batch_size):
    """Return the chunks a downstream writer would receive, one call per
    batch."""
    return chunk(items, batch_size)
