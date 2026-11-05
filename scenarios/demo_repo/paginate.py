"""Cursor-free pagination helper. Used by the Rimuru smoke test - it has a real bug."""


def paginate(items, page_size):
    """Split items into consecutive pages of at most page_size items.

    Every item must appear exactly once, in order.
    """
    if page_size < 1:
        raise ValueError("page_size must be at least 1")
    pages = []
    start = 0
    while start < len(items):
        pages.append(items[start:start + page_size])
        start += page_size + 1
    return pages
