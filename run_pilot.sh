#!/usr/bin/env bash
# Runs the tau-bench retail scenarios for both models and copies the results here.
#
#   bash run_pilot.sh smoke    10 scenarios per model, to check everything works (cents)
#   bash run_pilot.sh pilot    all 114 retail scenarios per model, one trial each
#
# Re-running the same command resumes where it stopped.

set -euo pipefail

MODE="${1:-}"
TAU2_DIR="${TAU2_DIR:-$HOME/tau2-bench}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# The two models being compared. Mercury is reached through LiteLLM's generic
# OpenAI-compatible route, which reads the address and key from .env, so the key
# is never written into the results file.
MERCURY="${MERCURY:-litellm_proxy/mercury-2.5}"
# GPT-6 Luna refuses reasoning_effort together with tools on /chat/completions,
# so it goes through OpenAI's Responses API. LiteLLM's "responses/" route does
# that and carries reasoning_effort across as reasoning.effort.
LUNA="${LUNA:-openai/responses/gpt-6-luna}"

# Customer simulator: the harness's own default, a neutral third model.
# To spend less, run with USER_SIM=gpt-5.6-luna bash run_pilot.sh pilot
USER_SIM="${USER_SIM:-gpt-4.1-2025-04-14}"

# Conversations run at the same time. Drop to 2 if you see 429 rate-limit errors.
CONCURRENCY="${CONCURRENCY:-4}"

# Reasoning level, the same for both arms. Sent through extra_body because the
# LiteLLM version the harness pins silently drops reasoning_effort for gpt-6-luna.
# Runs at a level other than "low" get their own save name, so they never mix
# with the main runs:   REASONING=none ARMS=mercury bash run_pilot.sh pilot
REASONING="${REASONING:-low}"
AGENT_ARGS="{\"extra_body\": {\"reasoning_effort\": \"$REASONING\"}}"
SUFFIX=""; [ "$REASONING" = "low" ] || SUFFIX="_$REASONING"

# Which models to run: "mercury luna" (default), or just one of them.
ARMS="${ARMS:-mercury luna}"

case "$MODE" in
  smoke) EXTRA=(--num-tasks 10); PREFIX="smoke" ;;
  pilot) EXTRA=(); PREFIX="pilot" ;;
  *) echo "Usage: bash run_pilot.sh smoke   or   bash run_pilot.sh pilot"; exit 1 ;;
esac

if [ ! -d "$TAU2_DIR" ]; then
  echo "Can't find the harness at $TAU2_DIR. Did Step 2 finish?"; exit 1
fi

mkdir -p "$HERE/runs"
cd "$TAU2_DIR"

# Copies the harness's results here and records every error in the ledger
# (runs/<name>.errors.json) BEFORE any rerun can replace it. Returns
# record_errors.py's exit code: 0 done, 10 rerun needed, 2 stop.
snapshot () {
  local save="$1"
  local src="data/simulations/$save/results.json"
  [ -f "$src" ] || return 0
  cp "$src" "$HERE/runs/${save}.json"
  local rc=0
  uv run python "$HERE/record_errors.py" "$HERE/runs/${save}.json" || rc=$?
  return $rc
}

run_arm () {
  local name="$1" model="$2"
  local save="${PREFIX}_${name}_retail${SUFFIX}"
  echo ""
  echo "=== Running $name ($model) -> data/simulations/$save ==="
  # When resuming, the harness keeps the settings of the first run and only
  # logs a warning if they changed. Refuse instead, so settings never get mixed.
  local settings="agent=$model user=$USER_SIM args=$AGENT_ARGS"
  local sf="$HERE/runs/${save}.settings"
  if [ -f "$sf" ] && [ "$(cat "$sf")" != "$settings" ]; then
    echo "This run was started with different settings:"
    echo "  before: $(cat "$sf")"
    echo "  now:    $settings"
    echo "Put the settings back, or start fresh by renaming data/simulations/$save and runs/${save}.*"
    exit 2
  fi
  echo "$settings" > "$sf"
  # A previous run may have been interrupted: record its errors first.
  snapshot "$save" >/dev/null || true
  local attempt rc
  for attempt in 1 2 3 4; do
    # --max-retries 0: the harness's own retries would give a model that
    # returns broken tool calls extra attempts and hide those failures.
    uv run tau2 run \
      --domain retail \
      --agent-llm "$model" \
      --agent-llm-args "$AGENT_ARGS" \
      --user-llm "$USER_SIM" \
      --num-trials 1 \
      --max-concurrency "$CONCURRENCY" \
      --max-retries 0 \
      --auto-resume \
      --save-to "$save" \
      ${EXTRA[@]+"${EXTRA[@]}"}
    rc=0
    snapshot "$save" || rc=$?
    if [ "$rc" -eq 2 ]; then
      echo "Stopped: fix the problem above, then run the same command again."; exit 2
    elif [ "$rc" -eq 10 ] && [ "$attempt" -lt 4 ]; then
      echo "Rerunning conversations that failed for reasons other than the model (pass $((attempt + 1)) of 4)..."
      sleep 30
    else
      break
    fi
  done
  echo "Results in runs/${save}.json, error history in runs/${save}.errors.json"
}

for arm in $ARMS; do
  case "$arm" in
    mercury) run_arm mercury "$MERCURY" ;;
    luna) run_arm luna "$LUNA" ;;
    *) echo "Unknown arm '$arm' (use mercury or luna)"; exit 1 ;;
  esac
done

echo ""
echo "Done. Next:"
if [ "$MODE" = "smoke" ]; then
  echo "  uv run python estimate_cost.py runs/smoke_*.json"
else
  echo "  uv run --with matplotlib python analyze.py runs/pilot_*.json"
fi
