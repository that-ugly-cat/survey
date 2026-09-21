"""The distributions the exploration module needs, and nothing else.

This app carries no numpy and no scipy: the image is a slim Python and the
dependency list is short on purpose. What is needed here is two special
functions and the tails built on them, which is sixty lines rather than a
hundred megabytes.

Rolling one's own statistics is a way to be quietly wrong, so every function
below is checked in test_stats.py against values from a table — chi-square and
t critical points, a Fisher exact worked by hand — instead of against itself.

All p-values are two-sided unless the name says otherwise.
"""

import math

# Enough for the tails a survey of a few hundred people can produce; beyond it
# the series below stop converging usefully and the answer says so.
_ITMAX = 300
_EPS = 3.0e-12
_FPMIN = 1.0e-300


def _gamma_series(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x), by series. Good for x < a+1."""
    ap, total, term = a, 1.0 / a, 1.0 / a
    for _ in range(_ITMAX):
        ap += 1
        term *= x / ap
        total += term
        if abs(term) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_cf(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x), by continued fraction.
    Good for x >= a+1."""
    b, c, d = x + 1.0 - a, 1.0 / _FPMIN, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, _ITMAX):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def gammaq(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) = 1 - P(a, x)."""
    if x <= 0 or a <= 0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gamma_series(a, x)
    return _gamma_cf(a, x)


def chi2_sf(x: float, df: int) -> float:
    """P(X > x) for a chi-square with `df` degrees of freedom."""
    if df <= 0 or x <= 0:
        return 1.0
    return gammaq(df / 2.0, x / 2.0)


def norm_sf(z: float) -> float:
    """P(Z > z) for a standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _ITMAX):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x)
                     + math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf2(t: float, df: float) -> float:
    """Two-sided p for a Student t statistic."""
    if df <= 0:
        return 1.0
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]].

    The two-sided value is the sum of every table, with the same margins, no
    more likely than the one observed. That is the convention scipy uses and
    the one a textbook's worked example is computed under, which matters
    because it is the number this gets checked against.
    """
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1, col1 = a + b, a + c

    def prob(k):
        return math.exp(
            math.lgamma(row1 + 1) + math.lgamma(n - row1 + 1)
            + math.lgamma(col1 + 1) + math.lgamma(n - col1 + 1)
            - math.lgamma(n + 1) - math.lgamma(k + 1)
            - math.lgamma(row1 - k + 1) - math.lgamma(col1 - k + 1)
            - math.lgamma(n - row1 - col1 + k + 1))

    observed = prob(a)
    lo, hi = max(0, row1 + col1 - n), min(row1, col1)
    total = 0.0
    for k in range(lo, hi + 1):
        p = prob(k)
        if p <= observed * (1 + 1e-9):
            total += p
    return min(1.0, total)


def ranks(values) -> list:
    """Ranks, ties averaged. Every test here is a rank test, so this is the
    one place ties are handled and the one place to look when they are wrong."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = average
        i = j + 1
    return out


def _tie_correction(rs) -> float:
    counts = {}
    for r in rs:
        counts[r] = counts.get(r, 0) + 1
    return sum(t ** 3 - t for t in counts.values())


def mann_whitney(a: list, b: list) -> dict:
    """U with the normal approximation, tie-corrected and continuity-corrected.

    Exploratory, so the approximation is the point: an exact U for forty
    answers would be precise about a question nobody should be settling here.
    """
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return {"u": None, "p": None, "z": None}
    rs = ranks(list(a) + list(b))
    r1 = sum(rs[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u = min(u1, n1 * n2 - u1)
    mean = n1 * n2 / 2.0
    n = n1 + n2
    tie = _tie_correction(rs)
    var = (n1 * n2 / 12.0) * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return {"u": u, "p": 1.0, "z": 0.0}
    # The continuity correction can push a statistic sitting on the mean below
    # zero, which is an artefact of the correction and not a direction.
    z = max(0.0, (abs(u1 - mean) - 0.5) / math.sqrt(var))
    return {"u": u, "z": z, "p": min(1.0, 2 * norm_sf(z))}


def kruskal(groups: list) -> dict:
    """H over k groups, with the chi-square approximation on k-1 df."""
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return {"h": None, "p": None, "df": 0}
    flat = [v for g in groups for v in g]
    n = len(flat)
    rs = ranks(flat)
    h, at = 0.0, 0
    for g in groups:
        rank_sum = sum(rs[at:at + len(g)])
        h += rank_sum ** 2 / len(g)
        at += len(g)
    h = 12.0 / (n * (n + 1)) * h - 3 * (n + 1)
    tie = _tie_correction(rs)
    if tie and n > 1:
        h /= 1 - tie / (n ** 3 - n)
    return {"h": h, "df": k - 1, "p": chi2_sf(h, k - 1)}


def spearman(x: list, y: list) -> dict:
    """Rank correlation, with the t approximation for its p."""
    n = len(x)
    if n < 3:
        return {"rho": None, "p": None, "n": n}
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if den == 0:
        return {"rho": None, "p": None, "n": n}
    rho = num / den
    if abs(rho) >= 1.0:
        return {"rho": rho, "p": 0.0, "n": n}
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    return {"rho": rho, "p": t_sf2(t, n - 2), "n": n}


def chi_square(table: list) -> dict:
    """Chi-square of independence over a table of counts, with Cramér's V.

    `expected_below_5` is reported rather than acted on: it is the reason the
    caller may prefer Fisher, and the reason a result should be read with a
    raised eyebrow when it cannot.
    """
    rows = len(table)
    cols = len(table[0]) if rows else 0
    n = sum(sum(r) for r in table)
    if n == 0 or rows < 2 or cols < 2:
        return {"chi2": None, "p": None, "df": 0, "v": None, "n": n}
    row_totals = [sum(r) for r in table]
    col_totals = [sum(table[i][j] for i in range(rows)) for j in range(cols)]
    chi2, small = 0.0, 0
    for i in range(rows):
        for j in range(cols):
            e = row_totals[i] * col_totals[j] / n
            if e < 5:
                small += 1
            if e > 0:
                chi2 += (table[i][j] - e) ** 2 / e
    df = (rows - 1) * (cols - 1)
    v = math.sqrt(chi2 / (n * min(rows - 1, cols - 1))) if n and df else None
    return {"chi2": chi2, "df": df, "p": chi2_sf(chi2, df), "v": v, "n": n,
            "cells": rows * cols, "expected_below_5": small}
