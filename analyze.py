"""Turns pilot results into the headline table, a chart, and a list of failures to read.

    uv run --with matplotlib python analyze.py runs/pilot_*.json

Writes to out/: summary.md, summary.csv, per_task.csv, failures_to_read.txt, frontier.png
"""

import csv
import random
import sys
from collections import Counter, defaultdict
from math import comb
from pathlib import Path
from statistics import median

from common import HERE, cost, cost_with_cache, load_prices, load_run, price_for

BOOT = 5000
SEED = 7
OUT = HERE / "out"


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def pass_hat_k(by_task, k):
    """Sierra's pass^k: for each task with n trials and c successes, C(c,k)/C(n,k), averaged over tasks."""
    vals = [comb(sum(v), k) / comb(len(v), k) for v in by_task.values() if len(v) >= k]
    return sum(vals) / len(vals) if vals else float("nan")


def summarize(rows, ap, up):
    """Headline numbers for one model from its completed conversations."""
    resolved = sum(r["resolved"] for r in rows)
    a_in = sum(r["agent_in"] for r in rows)
    a_out = sum(r["agent_out"] for r in rows)
    list_cost = cost(a_in, a_out, ap)
    promo_cost = cost(a_in, a_out, ap, promo=True) if "promo_input" in ap else None
    return {
        "resolved": resolved,
        "list_cost": list_cost,
        "promo_cost": promo_cost,
        "cpr_list": list_cost / resolved if resolved else float("inf"),
        "cpr_promo": (promo_cost / resolved if resolved else float("inf")) if promo_cost is not None else None,
        "user_cost": cost(sum(r["user_in"] for r in rows), sum(r["user_out"] for r in rows), up),
        "cached_cost": cost_with_cache(rows, ap),
        "cached_cost_promo": cost_with_cache(rows, ap, promo=True),
    }


def by_task(rows):
    d = defaultdict(list)
    for r in rows:
        d[r["task"]].append(1 if r["resolved"] else 0)
    return d


def bootstrap(rows, ap, up, rng):
    """Resample whole scenarios (all trials of a task together) and recompute."""
    tasks = defaultdict(list)
    for r in rows:
        tasks[r["task"]].append(r)
    keys = list(tasks)
    # Each task's own pass^1. A task drawn twice must count twice, so average
    # over the drawn keys rather than regrouping the sample by task.
    per_task = {k: sum(r["resolved"] for r in v) / len(v) for k, v in tasks.items()}
    p1s, cprs, cached = [], [], []
    for _ in range(BOOT):
        drawn = rng.choices(keys, k=len(keys))
        p1s.append(sum(per_task[k] for k in drawn) / len(drawn))
        sample = [r for k in drawn for r in tasks[k]]
        res = sum(r["resolved"] for r in sample)
        cc = cost_with_cache(sample, ap)
        if res and cc is not None:
            cached.append(cc / res)
        s = summarize(sample, ap, up)
        if s["resolved"]:
            cprs.append(s["cpr_list"])
    return ((pct(p1s, 0.025), pct(p1s, 0.975)), (pct(cprs, 0.025), pct(cprs, 0.975)),
            (pct(cached, 0.025), pct(cached, 0.975)) if cached else (None, None))


def paired_diff(rows_a, rows_b, rng):
    """pass^1 difference (a minus b) on scenarios both models completed, with a bootstrap interval."""
    ta, tb = by_task(rows_a), by_task(rows_b)
    shared = sorted(set(ta) & set(tb))
    if not shared:
        return None
    diffs = {t: sum(ta[t]) / len(ta[t]) - sum(tb[t]) / len(tb[t]) for t in shared}
    point = sum(diffs.values()) / len(shared)
    boots = []
    for _ in range(BOOT):
        s = rng.choices(shared, k=len(shared))
        boots.append(sum(diffs[t] for t in s) / len(s))
    return point, pct(boots, 0.025), pct(boots, 0.975), len(shared)


def paired_cpr_ratio(rows_a, rows_b, ap, bp, rng, cached=False):
    """Cost per resolved conversation of a divided by b, resampling shared scenarios together."""
    ta = defaultdict(list)
    tb = defaultdict(list)
    for r in rows_a:
        ta[r["task"]].append(r)
    for r in rows_b:
        tb[r["task"]].append(r)
    shared = sorted(set(ta) & set(tb))
    if not shared:
        return None

    def cpr(rows, p):
        res = sum(r["resolved"] for r in rows)
        spend = cost_with_cache(rows, p) if cached else \
            cost(sum(r["agent_in"] for r in rows), sum(r["agent_out"] for r in rows), p)
        return spend / res if res else None

    def ratio(keys):
        a = cpr([r for k in keys for r in ta[k]], ap)
        b = cpr([r for k in keys for r in tb[k]], bp)
        return a / b if a and b else None

    point = ratio(shared)
    boots = [x for x in (ratio(rng.choices(shared, k=len(shared))) for _ in range(BOOT)) if x]
    return point, pct(boots, 0.025), pct(boots, 0.975)


def main(paths):
    # runs/*.json also matches the error records (*.errors.json); skip those.
    paths = [p for p in paths if not str(p).endswith(".errors.json")]
    if not paths:
        raise SystemExit("Give it the pilot results, e.g.: uv run --with matplotlib python analyze.py runs/pilot_*.json")
    prices = load_prices()
    rng = random.Random(SEED)
    OUT.mkdir(exist_ok=True)

    loaded = [load_run(p) for p in paths]

    def effort(meta):
        args = meta.get("agent_llm_args") or {}
        return str((args.get("extra_body") or {}).get("reasoning_effort") or args.get("reasoning_effort") or "default")

    # When the same model was run at more than one reasoning level, each level is its own row.
    multi = len({effort(m) for m, _ in loaded}) > 1
    groups = defaultdict(lambda: {"rows": [], "metas": [], "base": None})
    for meta, rows in loaded:
        label = f"{meta['agent_key']} ({effort(meta)})" if multi else meta["agent_key"]
        for r in rows:
            r["task"] = f"{meta['domain']}:{r['task_id']}"
        groups[label]["rows"].extend(rows)
        groups[label]["metas"].append(meta)
        groups[label]["base"] = meta["agent_key"]

    results, warnings = [], []

    # Both models must be scored on the same scenarios and the same setup.
    if len(groups) > 1:
        all_metas = [m for g in groups.values() for m in g["metas"]]
        for field, label in (("user_key", "customer simulator"),
                             ("git_commit", "harness version"), ("num_trials", "number of trials")):
            vals = {str(m[field]) for m in all_metas}
            if len(vals) > 1:
                warnings.append(f"The models ran with a different {label} ({', '.join(sorted(vals))}). "
                                "The comparison is not like for like.")
        dropped = {r["task"] for g in groups.values() for r in g["rows"] if r["infra_error"]}
        if dropped:
            for g in groups.values():
                g["rows"] = [r for r in g["rows"] if r["task"] not in dropped]
            warnings.append(f"{len(dropped)} scenario(s) still had an error outside the models in at least one run, "
                            "so they are left out for every model, keeping the comparison on the same scenarios. "
                            "Run the same run_pilot.sh command again to retry them.")

    for key, g in groups.items():
        metas = g["metas"]
        if len({m["user_key"] for m in metas}) > 1 or len({str(m["agent_llm_args"]) for m in metas}) > 1:
            warnings.append(f"{key}: runs used different simulator or settings; numbers are not comparable.")
        ap = price_for(g["base"], prices)
        up = price_for(metas[0]["user_key"], prices)
        all_rows = g["rows"]
        rows = [r for r in all_rows if not r["infra_error"]]
        infra = len(all_rows) - len(rows)
        if infra:
            ids = sorted({r["task_id"] for r in all_rows if r["infra_error"]})
            warnings.append(f"{key}: {infra} conversation(s) failed for reasons outside the model "
                            f"(network, grader or simulator) and are excluded (task ids {', '.join(ids)}). "
                            "Run the same run_pilot.sh command again to retry them.")
        model_errs = Counter(r["error_cause"] for r in rows if r["model_failed"])
        if model_errs:
            warnings.append(f"{key}: {sum(model_errs.values())} conversation(s) failed because of the model "
                            f"({dict(model_errs)}) and count as unresolved. The harness's own summary box "
                            "leaves these out, so its pass^1 will be higher than this table's.")
        # A conversation that crashed on every attempt keeps no messages, so its
        # spend is unknown. Charge it the average of this model's other failures.
        blank = [r for r in rows if r["model_failed"] and r["agent_in"] == 0]
        if blank:
            other = [r for r in rows if not r["resolved"] and r["agent_in"] > 0] or [r for r in rows if r["agent_in"] > 0]
            if other:
                for r in blank:
                    r["agent_in"] = round(sum(x["agent_in"] for x in other) / len(other))
                    r["agent_out"] = round(sum(x["agent_out"] for x in other) / len(other))
            warnings.append(f"{key}: {len(blank)} failed conversation(s) kept no record of their tokens. "
                            "Each is charged the average spend of this model's other failed conversations.")
        server = [r for r in rows if r["agent_server_error"]]
        if server:
            alt = [r for r in rows if r["resolved"] and not r["agent_server_error"]]
            warnings.append(f"{key}: {len(server)} conversation(s) hit a server or connection error (not a rate limit) on this model's "
                            f"API at least once and were rerun. Counting them as failures instead, pass^1 would be "
                            f"{len(alt) / len(rows):.1%}. If one model has many more of these, the errors may be "
                            "the model's output being rejected rather than an outage: read them in runs/*.errors.json.")
        cached = [r["agent_cached_in"] for r in rows if r["agent_cached_in"] is not None]
        cache_share = sum(cached) / sum(r["agent_in"] for r in rows) if cached and sum(r["agent_in"] for r in rows) else None
        harness = [r["harness_agent_cost"] for r in rows if r["harness_agent_cost"] is not None]
        if harness and sum(harness) == 0 and sum(r["agent_in"] for r in rows) > 0:
            warnings.append(f"{key}: the harness recorded $0.00 cost (LiteLLM couldn't price it). "
                            "Expected for Mercury; this script uses token counts instead.")
        bt = by_task(rows)
        s = summarize(rows, ap, up)
        (p_lo, p_hi), (c_lo, c_hi), (cc_lo, cc_hi) = bootstrap(rows, ap, up, rng)
        all_times = [t for r in rows for t in r["gen_times"]]
        conv_secs = [sum(r["gen_times"]) for r in rows]
        calls = sum(r["tool_calls"] for r in rows)
        trials = max((m["num_trials"] or 1) for m in metas)
        results.append({
            "model": key,
            "maker": ap.get("maker", ""),
            "scenarios": len(bt),
            "conversations": len(rows),
            "infra_errors": infra,
            "model_errors": sum(model_errs.values()),
            "resolved": s["resolved"],
            "price": ap,
            "pass1": pass_hat_k(bt, 1),
            "pass1_lo": p_lo,
            "pass1_hi": p_hi,
            "pass2": pass_hat_k(bt, 2) if trials >= 2 else None,
            "agent_cost_list": s["list_cost"],
            "agent_cost_promo": s["promo_cost"],
            "cpr_list": s["cpr_list"],
            "cpr_lo": c_lo,
            "cpr_hi": c_hi,
            "cpr_promo": s["cpr_promo"],
            "cpr_cached": (s["cached_cost"] / s["resolved"] if s["resolved"] else float("inf")) if s["cached_cost"] is not None else None,
            "cpr_cached_lo": cc_lo,
            "cpr_cached_hi": cc_hi,
            "cpr_cached_promo": (s["cached_cost_promo"] / s["resolved"] if s["resolved"] else float("inf")) if s["cached_cost_promo"] is not None else None,
            "turn_p50": median(all_times) if all_times else float("nan"),
            "turn_p95": pct(all_times, 0.95),
            "conv_secs_p50": median(conv_secs) if conv_secs else float("nan"),
            "turns_per_conv": sum(r["agent_turns"] for r in rows) / len(rows) if rows else 0,
            "tool_calls": calls,
            "tool_error_rate": sum(r["tool_errors"] for r in rows) / calls if calls else 0,
            "terminations": dict(Counter(r["termination"] for r in all_rows)),
            "user_sim": metas[0]["user_key"],
            "user_sim_cost": s["user_cost"],
            "cache_share": cache_share,
            "rows": rows,
        })

    results.sort(key=lambda x: x["cpr_list"])
    write_outputs(results, warnings, rng)


def money(x):
    if x is None:
        return "n/a"
    if x == float("inf"):
        return "none resolved"
    return f"${x:.4f}" if x < 1 else f"${x:,.2f}"


def write_outputs(results, warnings, rng):
    lines = ["# Pilot results", ""]
    lines += ["| Model | Scenarios | Resolved (pass^1) | 95% range | Agent cost, list | $ per resolved, list | 95% range "
              "| $ per resolved, promo | $ per resolved, list with caching | $ per resolved, promo with caching "
              "| Median s / turn | p95 s / turn | Tool calls returning an error |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r['model']} | {r['scenarios']} | {r['pass1']:.1%} | {r['pass1_lo']:.1%} to {r['pass1_hi']:.1%} "
            f"| {money(r['agent_cost_list'])} | {money(r['cpr_list'])} | {money(r['cpr_lo'])} to {money(r['cpr_hi'])} "
            f"| {money(r['cpr_promo'])} | {money(r['cpr_cached'])} | {money(r['cpr_cached_promo'])} | {r['turn_p50']:.2f} | {r['turn_p95']:.2f} | {r['tool_error_rate']:.1%} |")
    lines.append("")
    mercs = [r for r in results if r["model"].startswith("mercury")]
    others = [r for r in results if not r["model"].startswith("mercury")]
    pairs = [(a, b) for a in mercs for b in others]
    pairs += [(mercs[i], mercs[j]) for i in range(len(mercs)) for j in range(i + 1, len(mercs))]
    for a, b in pairs:
        d = paired_diff(a["rows"], b["rows"], rng)
        if d:
            point, lo, hi, n = d
            verdict = "a real difference" if (lo > 0 or hi < 0) else "within noise"
            lines.append(f"Paired difference in pass^1, {a['model']} minus {b['model']}, on {n} shared scenarios: "
                         f"{point:+.1%} (95% range {lo:+.1%} to {hi:+.1%}), {verdict}.")
            lines.append("")
        rr = paired_cpr_ratio(a["rows"], b["rows"], a["price"], b["price"], rng)
        if rr and rr[0]:
            point, lo, hi = rr
            verdict = "a real difference" if (lo > 1 or hi < 1) else "within noise"
            lines.append(f"Cost per resolved conversation at list price, {a['model']} as a multiple of {b['model']}: "
                         f"{point:.2f}x (95% range {lo:.2f}x to {hi:.2f}x), {verdict}.")
            lines.append("")
        if a["cpr_cached"] is not None and b["cpr_cached"] is not None:
            rc = paired_cpr_ratio(a["rows"], b["rows"], a["price"], b["price"], rng, cached=True)
            if rc and rc[0]:
                point, lo, hi = rc
                verdict = "a real difference" if (lo > 1 or hi < 1) else "within noise"
                lines.append(f"Same, with cached input billed at the cached price: {point:.2f}x "
                             f"(95% range {lo:.2f}x to {hi:.2f}x), {verdict}.")
                lines.append("")
    lines.append("Notes")
    for r in results:
        lines.append(f"- {r['model']}: {r['conversations']} conversations, {r['turns_per_conv']:.1f} agent turns each, "
                     f"median {r['conv_secs_p50']:.1f} s of model time per conversation. "
                     f"Endings: {r['terminations']}. Customer simulator ({r['user_sim']}) cost {money(r['user_sim_cost'])}, "
                     f"reported separately because a buyer would not pay it.")
    lines.append("- Cost is recomputed from token counts at the prices in prices.json. The list and promo columns "
                 "charge every input token in full; the \"with caching\" columns charge the tokens each provider "
                 "reported as cached at its cached-input price, which is closer to an actual bill.")
    for r in results:
        if r["cache_share"] is not None:
            lines.append(f"- {r['model']}: the provider reported {r['cache_share']:.0%} of input tokens as cached.")
    lines.append("- Seconds per turn are measured from this computer and include network time, the provider's queue "
                 "and any retries after a rate limit. The two models ran one after the other, not side by side.")
    lines.append("- \"Tool calls returning an error\" includes legitimate answers like \"order not found\", not only malformed calls.")
    for w in warnings:
        lines.append(f"- Warning: {w}")
    text = "\n".join(lines)
    (OUT / "summary.md").write_text(text + "\n")
    print(text)

    fields = [k for k in results[0] if k not in ("rows", "terminations", "price")]
    with open(OUT / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: r[k] for k in fields})

    with open(OUT / "per_task.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "task", "trial", "resolved", "termination", "agent_turns", "agent_tokens_in", "agent_tokens_out"])
        for r in results:
            for row in r["rows"]:
                w.writerow([r["model"], row["task"], row["trial"], int(row["resolved"]), row["termination"],
                            row["agent_turns"], row["agent_in"], row["agent_out"]])

    picks = ["Open these in `uv run tau2 view` (run from ~/tau2-bench) and write one line on why each failed.", ""]
    for r in results:
        failed = sorted({row["task_id"] for row in r["rows"] if not row["resolved"]})
        chosen = sorted(random.Random(SEED).sample(failed, min(5, len(failed))), key=lambda x: int(x) if x.isdigit() else x)
        picks.append(f"{r['model']}: task ids {', '.join(chosen) if chosen else 'none failed'}")
    (OUT / "failures_to_read.txt").write_text("\n".join(picks) + "\n")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib not available; skipped the chart. Run with: uv run --with matplotlib python analyze.py ...)")
        return
    # Plot the caching-adjusted cost when every model has it: it is closer to a real bill.
    use_cache = all(r["cpr_cached"] not in (None, float("inf")) and r["cpr_cached_lo"] is not None for r in results)
    x, lo, hi, promo = (("cpr_cached", "cpr_cached_lo", "cpr_cached_hi", "cpr_cached_promo") if use_cache
                        else ("cpr_list", "cpr_lo", "cpr_hi", "cpr_promo"))
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    for r in results:
        if r[x] == float("inf"):
            continue
        ax.errorbar(r[x], r["pass1"],
                    xerr=[[r[x] - r[lo]], [r[hi] - r[x]]],
                    yerr=[[r["pass1"] - r["pass1_lo"]], [r["pass1_hi"] - r["pass1"]]],
                    fmt="o", capsize=4, markersize=7)
        ax.annotate(r["model"], (r[x], r["pass1"]), textcoords="offset points", xytext=(8, 6), fontsize=9)
        if r[promo] not in (None, float("inf")):
            ax.plot(r[promo], r["pass1"], marker="o", markersize=7, markerfacecolor="none", color="gray")
            ax.annotate(f"{r['model']} at launch promo", (r[promo], r["pass1"]),
                        textcoords="offset points", xytext=(8, -12), fontsize=8, color="gray")
    ax.set_xlabel("Cost per resolved conversation, list price with caching (US$)" if use_cache
                  else "Cost per resolved conversation, list price (US$)")
    ax.set_ylabel("Share of conversations resolved (pass^1)")
    from matplotlib.ticker import PercentFormatter
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=None))
    ax.set_title("tau-bench retail: quality against cost", fontsize=11)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "frontier.png", dpi=200)
    print(f"\nWrote {OUT}/summary.md, summary.csv, per_task.csv, failures_to_read.txt, frontier.png")


if __name__ == "__main__":
    main(sys.argv[1:])
