#!/usr/bin/env bash
#
# One command to drive the whole demo.
#
# Standing in front of judges is the worst time to type six commands in the
# right order into three terminals. This starts the mock prod stack and the
# approval bus, waits until both actually answer, resets the scenario so the run
# is deterministic, and starts the agent — then cleans up when you Ctrl-C.
#
#   scripts/demo.sh                        # sim provider, no API key needed
#   scripts/demo.sh --provider truefoundry # the live gateway run
#   scripts/demo.sh --resume last          # the Layer 4 session-survival beat
#
# Anything you pass is forwarded to run_agent.py, so --model, --github and
# --max-steps all work.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

MOCK_PORT="${MOCK_PORT:-8000}"
BUS_PORT="${BUS_PORT:-8500}"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-20}"

mock_pid=""
bus_pid=""
agent_pid=""

# Quoting "${@:-a b c}" yields ONE argument, which argparse then rejects. Set
# the positional parameters instead, so each option stays its own argv element.
[ "$#" -eq 0 ] && set -- --provider sim --subagents

resuming="no"
for arg in "$@"; do
  [ "$arg" = "--resume" ] && resuming="yes"
done

log()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

cleanup() {
  local code=$?
  # Only ever kill what this script started — never a stray uvicorn the
  # operator is running in another window.
  for pid in "$agent_pid" "$bus_pid" "$mock_pid"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  exit "$code"
}
trap cleanup EXIT INT TERM

port_free() {
  # Probe with the interpreter we already validated, not nc: `nc` is not a
  # declared prerequisite, and `! missing_cmd` reports command-not-found as
  # success — which would silently mark every port free and let the launcher
  # run against somebody else's server.
  "$PYTHON" - "$1" <<'PYEOF'
import socket, sys
with socket.socket() as s:
    s.settimeout(1)
    sys.exit(1 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 0)
PYEOF
}

wait_for() {
  local url="$1" name="$2" deadline=$((SECONDS + STARTUP_TIMEOUT))
  while [ "$SECONDS" -lt "$deadline" ]; do
    curl -sf "$url" >/dev/null 2>&1 && return 0
    sleep 0.3
  done
  die "$name did not come up within ${STARTUP_TIMEOUT}s — check /tmp/ir-$name.log"
}

[ -x "$PYTHON" ] || die "no interpreter at $PYTHON. Run: uv venv --python 3.12 .venv && uv pip install -r requirements.txt"

# This launcher tells the operator to approve in the dashboard, so it must not
# inherit AUTO_APPROVE=1 from the shell or .env — that would approve every
# destructive action with no human, while the UI still claims to be gating.
if [ "${AUTO_APPROVE:-}" = "1" ]; then
  warn "AUTO_APPROVE=1 is set in the environment; ignoring it so the approval gate is real."
  warn "For an unattended recording, run: AUTO_APPROVE=1 $PYTHON run_agent.py ..."
fi
unset AUTO_APPROVE

for port_and_name in "$MOCK_PORT:mock_env" "$BUS_PORT:approval bus"; do
  port="${port_and_name%%:*}"; name="${port_and_name#*:}"
  port_free "$port" || die "port $port is already in use — something else is running as the $name"
done

log "starting mock prod stack on :$MOCK_PORT"
PYTHONPATH="$REPO" "$PYTHON" -m uvicorn mock_env.main:app --port "$MOCK_PORT" \
  > /tmp/ir-mock_env.log 2>&1 &
mock_pid=$!

log "starting approval bus + dashboard on :$BUS_PORT"
"$PYTHON" -m uvicorn approval_server:app --port "$BUS_PORT" \
  > "/tmp/ir-approval bus.log" 2>&1 &
bus_pid=$!

wait_for "http://localhost:$MOCK_PORT/alerts" "mock_env"
wait_for "http://localhost:$BUS_PORT/events" "approval bus"

# Deterministic scenario every time — this is what makes the demo repeatable.
# But NOT when resuming: a reset would clear the bus and destroy the continuous
# timeline that the session-survival demo exists to show, and re-break the
# service underneath a run that is mid-remediation.
if [ "$resuming" = "yes" ]; then
  log "resuming — leaving the scenario and the event timeline intact"
else
  curl -sf -X POST "http://localhost:$MOCK_PORT/reset" >/dev/null
  curl -sf -X POST "http://localhost:$BUS_PORT/reset" >/dev/null
  log "scenario reset — checkout-service is degraded on v1.4.2"
fi

printf '\n\033[1m  Dashboard: http://localhost:%s/\033[0m\n' "$BUS_PORT"
printf '  Approve in the dashboard when the run pauses. Ctrl-C stops everything.\n\n'

# Run the agent in the background and `wait` on it, rather than in the
# foreground. Bash defers traps until the running foreground command finishes,
# and the agent can sit at the approval gate for five minutes — so a foreground
# agent means Ctrl-C during the pause does nothing and the servers leak. `wait`
# is interruptible, so the trap fires immediately.
set +e
MOCK_ENV_URL="http://localhost:$MOCK_PORT" \
AGENT_BUS_URL="http://localhost:$BUS_PORT" \
  "$PYTHON" -u run_agent.py "$@" &
agent_pid=$!
wait "$agent_pid"
agent_status=$?
agent_pid=""
set -e

[ "$agent_status" -ne 0 ] && warn "agent exited with status $agent_status"

# Leave the servers up so the dashboard still shows the finished run.
printf '\n'
log "run complete — dashboard still live at http://localhost:$BUS_PORT/"
log "press Ctrl-C to shut down"
wait "$bus_pid" 2>/dev/null || true
