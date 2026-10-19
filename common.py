"""Reads tau-bench results files and turns each conversation into one row.

Cost is recomputed here from token counts and prices.json. The harness's own
cost field is not used, because it records $0.00 for any model LiteLLM can't
price, which includes Mercury reached through the litellm_proxy route.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIXES = ("litellm_proxy/", "openai/", "responses/", "openrouter/", "gemini/", "anthropic/", "inception/")


def load_prices():
    return json.loads((HERE / "prices.json").read_text())["models"]


def model_key(llm_name):
    """'litellm_proxy/mercury-2.5' -> 'mercury-2.5'; 'openai/responses/gpt-6-luna' -> 'gpt-6-luna'."""
    name = llm_name or ""
    changed = True
    while changed:
        changed = False
        for p in PREFIXES:
            if name.startswith(p):
                name = name[len(p):]
                changed = True
    return name


def price_for(key, prices):
    if key not in prices:
        raise SystemExit(
            f"No price for '{key}' in prices.json. Add it (dollars per million tokens) and rerun."
        )
    return prices[key]


def _cached(messages, role):
    """Input tokens the provider reported as served from its prompt cache, if it reported any."""
    n, seen = 0, False
    for m in messages:
        if m.get("role") != role:
            continue
        for u in ((m.get("usage") or {}), ((m.get("raw_data") or {}).get("usage") or {})):
            c = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
            if c is not None:
                n += c
                seen = True
                break
    return n if seen else None


def _tokens(messages, role):
    tin = tout = 0
    for m in messages:
        if m.get("role") != role:
            continue
        u = m.get("usage") or {}
        tin += u.get("prompt_tokens") or 0
        tout += u.get("completion_tokens") or 0
    return tin, tout


NETWORK_ERRORS = ("RateLimitError", "Timeout", "APIConnectionError", "ServiceUnavailableError",
                  "InternalServerError", "APIError", "ConnectionError", "BadGatewayError")


def classify_error(sim):
    """Sorts an 'infrastructure_error' conversation by who caused it.

    The harness gives this label to any exception, including a model returning
    broken tool-call JSON. Those are the model's failures, so they must count as
    unresolved, not be excluded or retried. Returns one of:
      agent_output  the agent model's reply couldn't be parsed (broken tool-call JSON)
      agent_rejected  the agent's API refused the request (usually a settings problem)
      network       rate limit, timeout, outage: rerun it
      grader        the GPT-4.1 grader failed: rerun it
      simulator     the scripted customer failed: rerun it
      auth          a key is wrong or missing: fix .env
      other         anything else: read the error
    """
    info = sim.get("info") or {}
    tb = info.get("error_traceback") or ""
    etype = info.get("error_type") or ""
    text = tb + " " + str(info.get("error") or "")
    last = tb.strip().splitlines()[-1] if tb.strip() else etype
    # An empty reply is caught by the orchestrator, so its traceback has no
    # /agent/ or /user/ frame. The message says whose reply it was.
    if "AssistantMessage must have either content or tool_calls" in text:
        return "agent_output"
    if "UserMessage must have either content or tool_calls" in text:
        return "simulator"
    if "AuthenticationError" in etype or "AuthenticationError" in last:
        return "auth"
    if any(n in etype or n in last for n in NETWORK_ERRORS):
        return "network"
    if "/evaluator/" in tb:
        return "grader"
    if "/agent/" in tb:
        if "BadRequestError" in etype or "BadRequestError" in last:
            return "agent_rejected"
        return "agent_output"
    if "/user/" in tb:
        return "simulator"
    return "other"


def is_rate_limit(text):
    t = str(text or "")
    return "RateLimitError" in t or "Rate limit" in t or "rate limit" in t or " 429" in t


def agent_side(sim):
    """True when the error was raised while calling the agent model's API."""
    info = sim.get("info") or {}
    text = (info.get("error_traceback") or "") + " " + str(info.get("error") or "")
    return "/agent/" in text or "AssistantMessage" in text


MODEL_CAUSED = {"agent_output", "agent_rejected"}
RERUNNABLE = {"network", "grader", "simulator"}


def ledger_path(results_path):
    p = Path(results_path)
    return p.with_name(p.stem + ".errors.json")


def load_ledger(results_path):
    p = ledger_path(results_path)
    return json.loads(p.read_text()) if p.exists() else {}


def load_run(path):
    """Returns (meta, rows). One row per conversation.

    Conversations whose first attempt failed because of the agent model (see
    classify_error) count as unresolved, even if a later rerun succeeded. The
    history comes from the .errors.json ledger that run_pilot.sh writes after
    every run, before anything is retried.
    """
    data = json.loads(Path(path).read_text())
    ledger = load_ledger(path)
    info = data.get("info", {})
    agent_llm = (info.get("agent_info") or {}).get("llm", "")
    user_llm = (info.get("user_info") or {}).get("llm", "")
    meta = {
        "file": str(path),
        "agent_llm": agent_llm,
        "agent_key": model_key(agent_llm),
        "agent_llm_args": (info.get("agent_info") or {}).get("llm_args"),
        "user_llm": user_llm,
        "user_key": model_key(user_llm),
        "num_trials": info.get("num_trials"),
        "git_commit": info.get("git_commit"),
        "domain": (info.get("environment_info") or {}).get("domain_name"),
    }
    rows = []
    for s in data.get("simulations", []):
        msgs = s.get("messages") or []
        reward = (s.get("reward_info") or {}).get("reward")
        a_in, a_out = _tokens(msgs, "assistant")
        u_in, u_out = _tokens(msgs, "user")
        gen_times = [
            m["generation_time_seconds"]
            for m in msgs
            if m.get("role") == "assistant" and m.get("generation_time_seconds") is not None
        ]
        tool_calls = sum(len(m.get("tool_calls") or []) for m in msgs if m.get("role") == "assistant")
        tool_errors = sum(
            1 for m in msgs
            if m.get("role") == "tool" and m.get("requestor", "assistant") == "assistant"
            and (m["error"] if "error" in m else str(m.get("content") or "").lstrip().lower().startswith("error"))
        )
        key = f"{s.get('task_id')}|{s.get('trial')}"
        is_infra = s.get("termination_reason") == "infrastructure_error"
        cause = classify_error(s) if is_infra else None
        entry = ledger.get(key) or {}
        past = entry.get("cause")
        # A conversation that hit a server or connection error on the agent's API,
        # even once. Usually an outage, but some providers return 5xx for output
        # the model got wrong, so analyze.py reports these per model.
        info_s = s.get("info") or {}
        agent_server_error = (
            (is_infra and cause == "network" and agent_side(s)
             and not is_rate_limit(str(info_s.get("error")) + (info_s.get("error_type") or "")))
            or (bool(entry.get("network_on_agent")) and not is_rate_limit(entry.get("error")))
        )
        model_failed = cause in MODEL_CAUSED or past in MODEL_CAUSED
        resolved = (reward is not None and reward >= 0.999) and not model_failed
        rows.append({
            "task_id": str(s.get("task_id")),
            "trial": s.get("trial"),
            "termination": ("model_error:" + (cause or past)) if model_failed else s.get("termination_reason"),
            # Excluded from results only when not the model's fault.
            "infra_error": is_infra and not model_failed,
            "error_cause": cause or past,
            "model_failed": model_failed,
            "agent_server_error": agent_server_error,
            "reward": reward,
            "resolved": resolved,
            "agent_in": a_in,
            "agent_out": a_out,
            "agent_cached_in": _cached(msgs, "assistant"),
            "user_in": u_in,
            "user_out": u_out,
            "agent_turns": len(gen_times),
            "gen_times": gen_times,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "harness_agent_cost": s.get("agent_cost"),
        })
    return meta, rows


def cost_with_cache(rows, p, promo=False):
    """Agent spend when cached input is billed at the cached price. Rows whose
    provider reported no cache data are charged in full. None if no cached price."""
    key = "promo_cached_input" if promo else "cached_input"
    if key not in p or (promo and "promo_input" not in p):
        return None
    pin = p["promo_input"] if promo else p["input"]
    pout = p["promo_output"] if promo else p["output"]
    total = 0.0
    for r in rows:
        c = min(r.get("agent_cached_in") or 0, r["agent_in"])
        total += (r["agent_in"] - c) * pin + c * p[key] + r["agent_out"] * pout
    return total / 1e6


def cost(tin, tout, p, promo=False):
    if promo and "promo_input" in p:
        return (tin * p["promo_input"] + tout * p["promo_output"]) / 1e6
    return (tin * p["input"] + tout * p["output"]) / 1e6
