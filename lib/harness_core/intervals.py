"""Confidence intervals shared by the replay analysis and the usage reports.

Standard library only, and no model call.
"""
import math

Z95 = 1.959963984540054  # the two-sided 95% normal quantile


def wilson(passes, n, z=Z95):
    """The Wilson score interval on a proportion, `(low, high)`; `(None, None)` for no attempts."""
    if not n:
        return None, None
    p = passes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)
