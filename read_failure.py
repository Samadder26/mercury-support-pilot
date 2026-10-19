"""Prints one conversation so you can see why it failed.

    uv run python read_failure.py runs/pilot_mercury_retail.json 34

Shows what the customer was told to want, Sierra's correct changes to the
store, the changes the agent made (with the store's replies), then the whole
conversation. Then decide who caused the failure:
  agent      the agent misread the request, broke a policy rule, picked the wrong item, stopped early
  simulator  the scripted customer went off its instructions (asked for something else, gave up, agreed to the wrong thing)
  debatable  a reasonable agent could have done either; the answer key is strict or ambiguous
"""

import json
import sys

READ_ONLY = ("get_", "find_", "list_", "calculate", "think", "transfer_to_human")


def main(path, task_id):
    data = json.loads(open(path).read())
    task = next((t for t in data["tasks"] if str(t["id"]) == str(task_id)), None)
    sim = next((s for s in data["simulations"] if str(s["task_id"]) == str(task_id)), None)
    if task is None or sim is None:
        raise SystemExit(f"No task {task_id} in {path}.")

    print("=" * 70)
    print(f"TASK {task_id}   ended: {sim.get('termination_reason')}   "
          f"reward: {(sim.get('reward_info') or {}).get('reward')}")
    print("=" * 70)

    print("\nWHAT THE CUSTOMER WAS TOLD")
    instr = (task.get("user_scenario") or {}).get("instructions") or {}
    for k in ("reason_for_call", "known_info", "unknown_info", "task_instructions"):
        if instr.get(k):
            print(f"  {k}: {instr[k]}")

    crit = task.get("evaluation_criteria") or {}
    print("\nCORRECT CHANGES TO THE STORE (Sierra's answer)")
    writes = [a for a in crit.get("actions") or [] if not a["name"].startswith(READ_ONLY)]
    for a in writes:
        print(f"  {a['name']} {json.dumps(a['arguments'])}")
    if not writes:
        print("  none: the right outcome was to change nothing")
    for k in ("communicate_info", "nl_assertions"):
        if crit.get(k):
            print(f"  must also tell the customer: {crit[k]}")

    msgs = sim.get("messages") or []
    print("\nCHANGES THE AGENT MADE")
    made = False
    for i, m in enumerate(msgs):
        for c in m.get("tool_calls") or []:
            if not c["name"].startswith(READ_ONLY):
                made = True
                reply = next((x for x in msgs[i + 1:] if x.get("role") == "tool"), None)
                print(f"  {c['name']} {json.dumps(c['arguments'])}")
                print(f"    store replied: {str((reply or {}).get('content'))[:200]}")
    if not made:
        print("  none")

    info = sim.get("info") or {}
    if info.get("error"):
        print(f"\nERROR: {str(info['error'])[:400]}")

    print("\nCONVERSATION")
    for m in msgs:
        if m.get("role") == "user" and m.get("content"):
            print(f"\nCUSTOMER: {m['content'].strip()}")
        elif m.get("role") == "assistant":
            if m.get("content"):
                print(f"\nAGENT: {m['content'].strip()}")
            for c in m.get("tool_calls") or []:
                print(f"   [agent calls {c['name']} {json.dumps(c['arguments'])[:150]}]")
        elif m.get("role") == "tool" and str(m.get("content") or "").lower().startswith("error"):
            print(f"   [store error: {str(m['content'])[:150]}]")
    print()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: uv run python read_failure.py runs/pilot_mercury_retail.json 34")
    main(sys.argv[1], sys.argv[2])
