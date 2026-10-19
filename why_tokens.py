"""Explains where each model's tokens went, per agent reply.

    uv run python why_tokens.py runs/pilot_*.json

Splits output tokens into visible text, tool-call arguments and reasoning (when the
provider reports it), and shows characters per token so tokenizer differences are
visible. Prints one row per model.
"""

import json
import sys
from statistics import mean, median

from common import load_prices, model_key


def usage_of(m):
    u = dict(m.get("usage") or {})
    raw = ((m.get("raw_data") or {}).get("usage") or {})
    for k, v in raw.items():
        u.setdefault(k, v)
    return u


def main(paths):
    paths = [p for p in paths if not str(p).endswith(".errors.json")]
    prices = load_prices()
    rows = []
    for path in paths:
        d = json.loads(open(path).read())
        key = model_key((d["info"].get("agent_info") or {}).get("llm", ""))
        p = prices.get(key, {})
        chars, tool_chars, tin, tout, reason, cached, secs, n = [], [], [], [], [], [], [], 0
        reason_seen = False
        for s in d["simulations"]:
            for m in s.get("messages") or []:
                if m.get("role") != "assistant" or not m.get("usage"):
                    continue
                u = usage_of(m)
                n += 1
                chars.append(len(m.get("content") or ""))
                tool_chars.append(sum(len(json.dumps(c.get("arguments") or {})) for c in m.get("tool_calls") or []))
                tin.append(u.get("prompt_tokens") or 0)
                tout.append(u.get("completion_tokens") or 0)
                r = ((u.get("completion_tokens_details") or {}).get("reasoning_tokens"))
                if r is not None:
                    reason_seen = True
                reason.append(r or 0)
                cached.append(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
                if m.get("generation_time_seconds") is not None:
                    secs.append(m["generation_time_seconds"])
        visible_tokens = sum(tout) - sum(reason)
        rows.append({
            "model": key,
            "replies": n,
            "in/reply": mean(tin),
            "cached %": 100 * sum(cached) / sum(tin) if sum(tin) else 0,
            "out/reply": mean(tout),
            "reasoning/reply": mean(reason) if reason_seen else None,
            "text chars/reply": mean(chars),
            "tool-arg chars/reply": mean(tool_chars),
            "chars per visible out token": (sum(chars) + sum(tool_chars)) / visible_tokens if visible_tokens else 0,
            "median s/reply": median(secs) if secs else 0,
            "out tokens/s (wall clock)": sum(tout) / sum(secs) if secs else 0,
            "eff. input $/M": (p.get("input", 0) * (1 - sum(cached) / sum(tin)) + p.get("cached_input", p.get("input", 0)) * sum(cached) / sum(tin)) if sum(tin) and p else None,
        })
    cols = list(rows[0].keys())
    width = max(len(c) for c in cols)
    print(f"{'':{width}}  " + "  ".join(f"{r['model']:>14}" for r in rows))
    for c in cols[1:]:
        vals = []
        for r in rows:
            v = r[c]
            vals.append(f"{'n/a':>14}" if v is None else f"{v:>14,.2f}" if isinstance(v, float) else f"{v:>14}")
        print(f"{c:{width}}  " + "  ".join(vals))
    if any(r["reasoning/reply"] is None for r in rows):
        print("\nn/a: that provider did not report reasoning tokens separately; its out/reply may include them.")


if __name__ == "__main__":
    main(sys.argv[1:])
