"""A tiny CSV exporter. Used by the Rimuru smoke test - it has a real bug."""
import csv
import io


def to_csv(rows):
    """Render a list of dicts as CSV text, header row first.

    An empty result set should produce an empty string.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
