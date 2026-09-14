"""Chunking utility.

BUG: chunk() takes only as many full-size chunks as items divide evenly
into (len(items) // n), so it silently drops the final partial chunk
instead of including it. chunk(range(10), 3) should yield four chunks,
[3, 3, 3, 1], but this returns only the first three.
"""


def chunk(items, n):
    """Split items into a list of chunks, each of length n except
    possibly the last, which holds the remainder.
    """
    items = list(items)
    result = []
    full_chunks = len(items) // n
    for i in range(full_chunks):
        start = i * n
        result.append(items[start:start + n])
    return result
