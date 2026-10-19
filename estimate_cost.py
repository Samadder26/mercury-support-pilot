"""Measures real token use from smoke-test runs and projects what the pilot and
full run will cost. Run after `bash run_pilot.sh smoke`:

    uv run python estimate_cost.py runs/smoke_*.json
"""

import sys
from statistics import mean

from common import cost, load_prices, load_run, price_for

PILOT = 114                      # retail scenarios, one trial
FULL = (114 + 50 + 114) * 2      # retail + airline + telecom, two trials
FREE_TOKENS_M = 100              # Inception's free allowance per new key, millions


def main(paths):
    # runs/*.json also matches the error records (*.errors.json); skip those.
    paths = [p for p in paths if not str(p).endswith(".errors.json")]
    if not paths:
        raise SystemExit("Give it the smoke results, e.g.: uv run python estimate_cost.py runs/smoke_*.json")
    prices = load_prices()
    cash_pilot = cash_full = 0.0
    print(f"{'Model':16} {'convs':>5} {'agent in':>9} {'agent out':>9} {'turns':>5} "
          f"{'agent $':>8} {'sim $':>7}  {'pilot cash':>10} {'full cash':>9}")
    print(f"{'':16} {'':>5} {'per conv':>9} {'per conv':>9} {'':>5} {'per conv':>8} {'/conv':>7}")
    for path in paths:
        meta, rows = load_run(path)
        done = [r for r in rows if not r["infra_error"]]
        bad = len(rows) - len(done)
        if not done:
            print(f"{meta['agent_key']}: no completed conversations in {path}. Check them with `uv run tau2 view`.")
            continue
        ap = price_for(meta["agent_key"], prices)
        up = price_for(meta["user_key"], prices)
        a_in, a_out = mean(r["agent_in"] for r in done), mean(r["agent_out"] for r in done)
        u_in, u_out = mean(r["user_in"] for r in done), mean(r["user_out"] for r in done)
        agent_conv, sim_conv = cost(a_in, a_out, ap), cost(u_in, u_out, up)
        is_mercury = meta["agent_key"] == "mercury-2.5"
        mercury_tokens_m = (a_in + a_out) * FULL / 1e6
        # Mercury's agent tokens are free up to the allowance; the simulator is always paid.
        pilot = sim_conv * PILOT + (0 if is_mercury else agent_conv * PILOT)
        full_agent = agent_conv * FULL
        if is_mercury and mercury_tokens_m > FREE_TOKENS_M:
            full_agent *= (mercury_tokens_m - FREE_TOKENS_M) / mercury_tokens_m
        elif is_mercury:
            full_agent = 0
        full = sim_conv * FULL + full_agent
        cash_pilot += pilot
        cash_full += full
        print(f"{meta['agent_key']:16} {len(done):>5} {a_in:>9,.0f} {a_out:>9,.0f} "
              f"{mean(r['agent_turns'] for r in done):>5.1f} {agent_conv:>8.4f} {sim_conv:>7.4f}  "
              f"{pilot:>10.2f} {full:>9.2f}")
        if bad:
            print(f"  {bad} conversation(s) ended in an infrastructure error and are left out. Look at them first.")
        if is_mercury:
            print(f"  Mercury agent tokens: about {(a_in + a_out) * PILOT / 1e6:.0f}M for the pilot, "
                  f"{mercury_tokens_m:.0f}M for the full run, against {FREE_TOKENS_M}M free.")
    print(f"\nProjected cash spend: pilot ${cash_pilot:,.2f}, full run ${cash_full:,.2f}")
    print("Includes the customer simulator, which is usually the largest line.")
    print("Not included: the GPT-4.1 grader for facts the agent must say, about $1 for the pilot.")


if __name__ == "__main__":
    main(sys.argv[1:])
