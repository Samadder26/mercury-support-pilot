# Pilot results

| Model | Scenarios | Resolved (pass^1) | 95% range | Agent cost, list | $ per resolved, list | 95% range | $ per resolved, promo | $ per resolved, list with caching | $ per resolved, promo with caching | Median s / turn | p95 s / turn | Tool calls returning an error |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-6-luna (low) | 114 | 80.7% | 72.8% to 87.7% | $0.8407 | $0.0091 | $0.0082 to $0.0103 | n/a | $0.0018 | n/a | 1.87 | 3.67 | 2.7% |
| mercury-2.5 (none) | 114 | 79.8% | 71.9% to 86.8% | $2.37 | $0.0261 | $0.0234 to $0.0293 | $0.0052 | $0.0106 | $0.0021 | 0.80 | 1.49 | 3.6% |
| mercury-2.5 (low) | 114 | 76.3% | 68.4% to 84.2% | $2.49 | $0.0286 | $0.0251 to $0.0329 | $0.0057 | $0.0123 | $0.0025 | 0.95 | 1.97 | 4.1% |

Paired difference in pass^1, mercury-2.5 (none) minus gpt-6-luna (low), on 114 shared scenarios: -0.9% (95% range -9.6% to +7.9%), within noise.

Cost per resolved conversation at list price, mercury-2.5 (none) as a multiple of gpt-6-luna (low): 2.85x (95% range 2.57x to 3.16x), a real difference.

Same, with cached input billed at the cached price: 5.84x (95% range 5.28x to 6.47x), a real difference.

Paired difference in pass^1, mercury-2.5 (low) minus gpt-6-luna (low), on 114 shared scenarios: -4.4% (95% range -14.0% to +4.4%), within noise.

Cost per resolved conversation at list price, mercury-2.5 (low) as a multiple of gpt-6-luna (low): 3.13x (95% range 2.81x to 3.51x), a real difference.

Same, with cached input billed at the cached price: 6.78x (95% range 6.07x to 7.62x), a real difference.

Paired difference in pass^1, mercury-2.5 (none) minus mercury-2.5 (low), on 114 shared scenarios: +3.5% (95% range -6.1% to +13.2%), within noise.

Cost per resolved conversation at list price, mercury-2.5 (none) as a multiple of mercury-2.5 (low): 0.91x (95% range 0.81x to 1.02x), within noise.

Same, with cached input billed at the cached price: 0.86x (95% range 0.76x to 0.97x), a real difference.

Notes
- gpt-6-luna (low): 114 conversations, 13.0 agent turns each, median 25.6 s of model time per conversation. Endings: {'user_stop': 114}. Customer simulator (gpt-4.1-2025-04-14) cost $1.30, reported separately because a buyer would not pay it.
- mercury-2.5 (none): 114 conversations, 13.6 agent turns each, median 11.2 s of model time per conversation. Endings: {'user_stop': 113, 'model_error:agent_rejected': 1}. Customer simulator (gpt-4.1-2025-04-14) cost $1.84, reported separately because a buyer would not pay it.
- mercury-2.5 (low): 114 conversations, 13.4 agent turns each, median 13.3 s of model time per conversation. Endings: {'user_stop': 110, 'too_many_errors': 1, 'model_error:agent_output': 3}. Customer simulator (gpt-4.1-2025-04-14) cost $1.58, reported separately because a buyer would not pay it.
- Cost is recomputed from token counts at the prices in prices.json. The list and promo columns charge every input token in full; the "with caching" columns charge the tokens each provider reported as cached at its cached-input price, which is closer to an actual bill.
- gpt-6-luna (low): the provider reported 95% of input tokens as cached.
- mercury-2.5 (none): the provider reported 70% of input tokens as cached.
- mercury-2.5 (low): the provider reported 70% of input tokens as cached.
- Seconds per turn are measured from this computer and include network time, the provider's queue and any retries after a rate limit. The two models ran one after the other, not side by side.
- "Tool calls returning an error" includes legitimate answers like "order not found", not only malformed calls.
- Warning: mercury-2.5 (none): 1 conversation(s) failed because of the model ({'agent_rejected': 1}) and count as unresolved. The harness's own summary box leaves these out, so its pass^1 will be higher than this table's.
- Warning: mercury-2.5 (none): 1 failed conversation(s) kept no record of their tokens. Each is charged the average spend of this model's other failed conversations.
- Warning: mercury-2.5 (none): the harness recorded $0.00 cost (LiteLLM couldn't price it). Expected for Mercury; this script uses token counts instead.
- Warning: mercury-2.5 (low): 3 conversation(s) failed because of the model ({'agent_output': 3}) and count as unresolved. The harness's own summary box leaves these out, so its pass^1 will be higher than this table's.
- Warning: mercury-2.5 (low): 2 failed conversation(s) kept no record of their tokens. Each is charged the average spend of this model's other failed conversations.
- Warning: mercury-2.5 (low): 2 conversation(s) hit a server or connection error (not a rate limit) on this model's API at least once and were rerun. Counting them as failures instead, pass^1 would be 76.3%. If one model has many more of these, the errors may be the model's output being rejected rather than an outage: read them in runs/*.errors.json.
- Warning: mercury-2.5 (low): the harness recorded $0.00 cost (LiteLLM couldn't price it). Expected for Mercury; this script uses token counts instead.
