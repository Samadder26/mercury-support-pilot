"""Records every errored conversation in a ledger before anything gets retried,
and says whether a rerun is needed. run_pilot.sh calls this after each run.

    python record_errors.py runs/pilot_mercury_retail.json

Exit codes: 0 nothing to rerun, 10 rerunnable errors remain, 2 stop and fix setup.
"""

import json
import sys
from collections import Counter

from common import MODEL_CAUSED, RERUNNABLE, agent_side, classify_error, is_rate_limit, ledger_path, load_ledger

HELP = {
    "agent_output": "the agent's reply couldn't be parsed (usually broken tool-call JSON). Counted as a failed conversation.",
    "agent_rejected": "the agent's API refused the request. If this hits most conversations, a setting is wrong (model name, reasoning_effort, a parameter the API doesn't accept). Counted as failed.",
    "network": "rate limit, timeout or outage. Will be rerun.",
    "grader": "the GPT-4.1 grader failed. Will be rerun.",
    "simulator": "the scripted customer failed. Will be rerun.",
    "auth": "a key is wrong or missing. Fix ~/tau2-bench/.env and run again.",
    "other": "unrecognised error. Read it in `uv run tau2 view` or paste it to Claude.",
}


def main(path):
    data = json.loads(open(path).read())
    ledger = load_ledger(path)
    now = Counter()
    for s in data.get("simulations", []):
        if s.get("termination_reason") != "infrastructure_error":
            continue
        cause = classify_error(s)
        now[cause] += 1
        key = f"{s.get('task_id')}|{s.get('trial')}"
        entry = ledger.get(key)
        # Keep the first model-caused failure; never let a later rerun erase it.
        if entry is None or (entry["cause"] not in MODEL_CAUSED and cause in MODEL_CAUSED):
            info = s.get("info") or {}
            old = entry or {}
            entry = {"cause": cause, "error": str(info.get("error"))[:300]}
            if old.get("network_on_agent"):
                entry["network_on_agent"] = True
            ledger[key] = entry
        # Remember any server or connection error on the agent's own API, so the
        # analysis can check whether one model had many more of them.
        info_s = s.get("info") or {}
        if cause == "network" and agent_side(s) and not is_rate_limit(str(info_s.get("error")) + (info_s.get("error_type") or "")):
            entry["network_on_agent"] = True
    ledger_path(path).write_text(json.dumps(ledger, indent=2, sort_keys=True))

    total = len(data.get("simulations", []))
    if not now:
        print(f"  Errors: none in {total} conversations.")
    else:
        print(f"  Errors in {total} conversations:")
        for cause, n in now.most_common():
            print(f"    {n} x {cause}: {HELP[cause]}")
    model_total = sum(1 for e in ledger.values() if e["cause"] in MODEL_CAUSED)
    if model_total:
        print(f"  {model_total} conversation(s) failed because of the agent model; they count as unresolved.")
        if now["agent_rejected"] > total / 2:
            print("  STOP: most requests were refused. This is a setup problem, not the model. Fix it before the pilot.")
            return 2
    if now["auth"]:
        return 2
    if any(now[c] for c in RERUNNABLE):
        return 10
    if now["other"]:
        print("  Some errors weren't recognised. Look at them before trusting the numbers.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
