"""Checks for stats.py, against values from a table rather than against itself.

Writing one's own distribution tails is a way to be quietly wrong: the code
runs, returns a number, and the number is off in the third decimal where nobody
looks. So every assertion below is anchored to something published — a critical
point of chi-square or t, a Fisher exact worked out by hand, a rank test whose
answer is forced by symmetry.
"""
import os, sys

sys.path.insert(0, os.path.abspath("."))
import stats

FAILED = []
ok = lambda c, m: print(("  PASS  " if c else "  FAIL  ") + m) or (c or FAILED.append(m))


def close(a, b, tol=5e-4):
    return a is not None and abs(a - b) < tol


print("\n--- chi-square, against its critical points ---")
# Every table of critical values carries these: the value of x at which the
# upper tail is exactly 0.05, for one to four degrees of freedom.
for x, df in ((3.8415, 1), (5.9915, 2), (7.8147, 3), (9.4877, 4)):
    p = stats.chi2_sf(x, df)
    print(f"    chi2_sf({x}, {df}) = {p:.5f}")
    ok(close(p, 0.05), f"x={x} on {df} df is the 5% point")
for x, df in ((6.6349, 1), (9.2103, 2), (11.3449, 3), (13.2767, 4)):
    ok(close(stats.chi2_sf(x, df), 0.01), f"x={x} on {df} df is the 1% point")
ok(close(stats.chi2_sf(0.4549, 1), 0.50), "and the median of chi-square with 1 df")
ok(stats.chi2_sf(0, 3) == 1.0 and stats.chi2_sf(-1, 3) == 1.0,
   "nothing below zero, and zero is the whole distribution")

print("\n--- the normal tail ---")
ok(close(stats.norm_sf(1.6449), 0.05), "1.6449 cuts 5% off the top")
ok(close(stats.norm_sf(1.9600), 0.025), "1.96 cuts 2.5%, which is the two-sided 5%")
ok(close(stats.norm_sf(0), 0.5), "and zero splits it in half")

print("\n--- Student t, two-sided ---")
# The 5% two-sided critical points, the row of the t table everyone has seen.
for t, df in ((2.2281, 10), (2.0860, 20), (2.0423, 30), (12.7062, 1)):
    p = stats.t_sf2(t, df)
    print(f"    t_sf2({t}, {df}) = {p:.5f}")
    ok(close(p, 0.05), f"t={t} on {df} df is the two-sided 5% point")
ok(close(stats.t_sf2(0, 10), 1.0), "a statistic of zero is as unremarkable as it gets")

print("\n--- Fisher exact ---")
# Fisher's tea taster: four cups of each, three of each guessed right. The
# two-sided p is 17/35 exactly, and it is the example the method is taught on.
p = stats.fisher_exact_2x2(3, 1, 1, 3)
print(f"    tea tasting [[3,1],[1,3]] -> {p:.6f}  (17/35 = {17 / 35:.6f})")
ok(close(p, 17 / 35, 1e-9), "the tea tasting table gives exactly 17/35")
ok(close(stats.fisher_exact_2x2(4, 0, 0, 4), 2 / 70, 1e-9),
   "and all eight cups right gives 2/70, the two tables at least as extreme")
ok(close(stats.fisher_exact_2x2(5, 5, 5, 5), 1.0, 1e-9),
   "a table with nothing in it to see returns 1")

print("\n--- ranks and ties ---")
ok(stats.ranks([10, 20, 30]) == [1.0, 2.0, 3.0], "plain ranks")
ok(stats.ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0], "ties share their average")
ok(stats.ranks([5, 5, 5]) == [2.0, 2.0, 2.0], "and an all-tie is all the middle")

print("\n--- Mann-Whitney ---")
same = stats.mann_whitney([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6])
print(f"    identical groups -> U={same['u']} z={same['z']:.3f} p={same['p']:.3f}")
ok(same["u"] == 18 and close(same["p"], 1.0, 0.02),
   "two identical groups sit at U = n1*n2/2 and p near 1")
apart = stats.mann_whitney([1, 2, 3, 4, 5], [11, 12, 13, 14, 15])
print(f"    disjoint groups  -> U={apart['u']} p={apart['p']:.5f}")
ok(apart["u"] == 0 and apart["p"] < 0.02,
   "two groups that do not overlap give U=0 and a small p")
ok(stats.mann_whitney([], [1, 2]).get("p") is None, "an empty group has no answer")

print("\n--- Kruskal-Wallis ---")
flat = stats.kruskal([[1, 2, 3], [1, 2, 3], [1, 2, 3]])
print(f"    three identical groups -> H={flat['h']:.3f} p={flat['p']:.3f}")
ok(close(flat["h"], 0.0, 1e-9) and close(flat["p"], 1.0, 1e-9),
   "three identical groups leave H at zero")
ok(stats.kruskal([[1, 2, 3], [7, 8, 9], [14, 15, 16]])["p"] < 0.05,
   "three separated groups do not")
ok(stats.kruskal([[1, 2, 3]])["p"] is None, "one group is not a comparison")

print("\n--- Spearman ---")
up = list(range(1, 11))
ok(close(stats.spearman(up, up)["rho"], 1.0), "a variable against itself is 1")
ok(close(stats.spearman(up, up[::-1])["rho"], -1.0), "and against its reverse, -1")
mono = stats.spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 25])
ok(close(mono["rho"], 1.0),
   "Spearman follows the order and not the shape: a square curve is still 1")
noise = stats.spearman([1, 2, 3, 4, 5, 6, 7, 8], [3, 1, 4, 2, 6, 5, 8, 7])
print(f"    rho={noise['rho']:.4f} p={noise['p']:.4f}")
ok(noise["rho"] > 0.7 and noise["p"] < 0.05, "a strong but imperfect order shows up")
ok(stats.spearman([1, 1, 1, 1], [1, 2, 3, 4])["rho"] is None,
   "a variable that never varies correlates with nothing")

print("\n--- chi-square of independence ---")
# Perfect independence: every cell is exactly its expected value.
indep = stats.chi_square([[10, 10], [10, 10]])
ok(close(indep["chi2"], 0.0) and close(indep["p"], 1.0) and close(indep["v"], 0.0),
   "a table where nothing depends on anything gives chi2 = 0 and V = 0")
dep = stats.chi_square([[20, 0], [0, 20]])
print(f"    perfect association -> chi2={dep['chi2']:.1f} V={dep['v']:.2f} p={dep['p']:.2e}")
ok(close(dep["v"], 1.0), "a perfect association gives V = 1")
ok(dep["df"] == 1 and dep["p"] < 1e-8, "on one degree of freedom, and a tiny p")
small = stats.chi_square([[1, 2], [3, 1]])
ok(small["expected_below_5"] == 4,
   "and it counts the cells whose expected count is too small to trust")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
