#!/usr/bin/env bash
# Thin wrapper around docker compose for the disposable hub sandbox.
#
# The mode (which Home Assistant, and whether the mTLS proxy runs) is remembered
# in .sandbox-mode, so `down`, `logs`, `ps`, `reset`, `shell` and `test` always
# act on the SAME services that `up` started. Not doing that is how you end up
# with orphaned containers from a previous mode still running alongside the new
# ones.
set -euo pipefail
cd "$(dirname "$0")"

MODE_FILE=".sandbox-mode"
HUB_URL="http://localhost:8080"

read_mode()  { [ -f "$MODE_FILE" ] && cat "$MODE_FILE" || echo "demo"; }
write_mode() { printf '%s\n' "$1" > "$MODE_FILE"; }

# Populate COMPOSE=(docker compose ...) for a mode string like "real-ha+tls".
build_compose() {
  local mode="$1"
  COMPOSE=(docker compose -f docker-compose.yml)
  case "$mode" in *real-ha*) COMPOSE+=(-f docker-compose.realha.yml) ;; esac
  case "$mode" in *tls*)     COMPOSE+=(--profile tls) ;; esac
}

# Parse flags after a command: --real-ha / --tls (also accept "real-ha", "tls").
mode_from_flags() {
  local mode="demo" tls=""
  for a in "$@"; do
    case "$a" in
      --real-ha|real-ha) mode="real-ha" ;;
      --tls|tls)         tls="+tls" ;;
      --demo|demo)       mode="demo" ;;
    esac
  done
  printf '%s%s\n' "$mode" "$tls"
}

# Remove containers belonging to services that are NOT part of the current mode.
# `--remove-orphans` does not do this: a service that exists in the file but is
# profile-disabled (the simulated HA in real-ha mode, the proxy without --tls) is
# "known but inactive", so compose leaves it running. Without this, switching
# modes leaves the old Home Assistant up alongside the new stack.
prune_inactive() {
  local active; active=" $("${COMPOSE[@]}" config --services 2>/dev/null | tr '\n' ' ') "
  local svc name
  for svc in hub homeassistant ha-init ntfy proxy; do
    case "$active" in *" $svc "*) continue ;; esac
    case "$svc" in
      hub) name=vahub ;;
      *)   name="vahub-$svc" ;;
    esac
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$name"; then
      echo "removing container from another mode: $name"
      docker rm -f "$name" >/dev/null 2>&1 || true
    fi
  done
}

wait_healthy() {
  printf 'waiting for the hub '
  for _ in $(seq 1 90); do
    if curl -sf "$HUB_URL/health" >/dev/null 2>&1; then echo "-> up"; return 0; fi
    printf '.'; sleep 2
  done
  echo "-> TIMEOUT"
  echo "the hub did not become healthy. recent logs:" >&2
  "${COMPOSE[@]}" logs --tail 40 hub >&2 || true
  return 1
}

# Modules handshake a moment after /health starts answering, so give them a
# little time to leave "starting" before judging them.
wait_modules() {
  for _ in $(seq 1 20); do
    local j; j="$(curl -s "$HUB_URL/api/modules" 2>/dev/null || true)"
    [ -n "$j" ] || { sleep 1; continue; }
    if ! printf '%s' "$j" | grep -q '"state": *"starting"'; then return 0; fi
    sleep 1
  done
}

# Print each module's state; non-zero exit if any module is not ready, so a
# broken module (a missing venv, an unreachable backend) is visible at startup
# instead of silently sitting there.
show_modules() {
  local json; json="$(curl -s "$HUB_URL/api/modules" 2>/dev/null || true)"
  [ -n "$json" ] || { echo "  (could not read /api/modules)"; return 1; }
  python3 -c '
import json, sys
try:
    mods = json.loads(sys.argv[1])
except Exception:
    print("  (bad JSON from /api/modules)"); sys.exit(1)
bad = []
for m in sorted(mods, key=lambda x: x["name"]):
    ok = m["state"] == "ready"
    if not ok: bad.append(m["name"])
    print("  %s %-14s %-11s %s" % ("OK " if ok else "!! ", m["name"], m["state"], m.get("last_error") or ""))
if bad:
    print("\n  NOT READY: %s" % ", ".join(bad))
    print("  inspect with: ./sandbox.sh logs   (or ./sandbox.sh modlog <name>)")
    sys.exit(1)
' "$json"
}

report() {
  local mode="$1"
  echo
  echo "mode: $mode"
  echo "hub:  $HUB_URL"
  case "$mode" in *tls*) echo "tls:  https://localhost:8443 (needs proxy/certs/client.p12 in your browser)" ;; esac
  echo "ntfy: http://localhost:2586  (topic: ${NTFY_TOPIC:-vahub-alerts})"
  echo
  echo "modules:"
  wait_modules
  show_modules || true
}

cmd="${1:-help}"; shift || true

case "$cmd" in
  up)
    mode="$(mode_from_flags "$@")"; build_compose "$mode"; write_mode "$mode"
    prune_inactive
    "${COMPOSE[@]}" up --build -d --remove-orphans
    wait_healthy && report "$mode"
    ;;

  # Convenience aliases for the common combinations.
  real-ha)     exec "$0" up --real-ha "$@" ;;
  real-ha-tls) ./proxy/gen-certs.sh; exec "$0" up --real-ha --tls "$@" ;;
  tls)         ./proxy/gen-certs.sh; exec "$0" up --tls "$@" ;;

  down)
    build_compose "$(read_mode)"
    "${COMPOSE[@]}" down --remove-orphans
    ;;

  # Full clean slate: also wipes the volumes (hub state + DB, HA onboarding,
  # ntfy messages), then rebuilds in the same mode.
  reset)
    mode="$(read_mode)"; build_compose "$mode"
    "${COMPOSE[@]}" down -v --remove-orphans
    prune_inactive
    "${COMPOSE[@]}" up --build -d --remove-orphans
    wait_healthy && report "$mode"
    ;;

  status)  build_compose "$(read_mode)"; "${COMPOSE[@]}" ps; report "$(read_mode)" ;;
  logs)    build_compose "$(read_mode)"; "${COMPOSE[@]}" logs -f "$@" ;;
  ps)      build_compose "$(read_mode)"; "${COMPOSE[@]}" ps ;;
  shell)   build_compose "$(read_mode)"; "${COMPOSE[@]}" exec hub /bin/bash 2>/dev/null \
             || "${COMPOSE[@]}" exec hub /bin/sh ;;
  build)   build_compose "$(read_mode)"; "${COMPOSE[@]}" build ;;

  # stderr of one module (the supervisor keeps a ring buffer per module)
  modlog)
    [ $# -ge 1 ] || { echo "usage: $0 modlog <module>"; exit 1; }
    curl -s "$HUB_URL/api/modules/$1/logs" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for line in d.get("lines", []): print(" ", line)' ;;

  # Send a test push through the notify module (verifies the whole chain:
  # hub -> policy gate -> notify module -> ntfy container).
  push)
    msg="${*:-test push from sandbox.sh}"
    payload="$(python3 -c '
import json,sys
print(json.dumps({"module":"notify","tool":"send_push",
                  "args":{"title":"vahub","message":sys.argv[1]}}))' "$msg")"
    curl -s -X POST "$HUB_URL/api/dev/call" -H 'content-type: application/json' -d "$payload"
    echo ;;

  test)  # -p no:cacheprovider because /app/project is a read-only mount
    build_compose "$(read_mode)"
    "${COMPOSE[@]}" run --rm --no-deps --entrypoint /opt/vh/hub/bin/python hub \
      -m pytest -q -p no:cacheprovider /app/project ;;

  help|*)
    cat <<'EOF'
usage: ./sandbox.sh <command> [flags]

  up [--real-ha] [--tls]   build + start, wait for health, report module states
  real-ha                  alias for: up --real-ha      (your real Home Assistant)
  tls                      alias for: up --tls          (mTLS proxy, demo HA)
  real-ha-tls              alias for: up --real-ha --tls

  status                   containers + module states
  logs [service]           follow logs
  modlog <module>          stderr of one hub module (e.g. notify)
  push [message]           send a test push through the notify module
  ps | shell | build | test
  down                     stop (same mode that was started)
  reset                    wipe volumes and restart in the same mode

The active mode is remembered in .sandbox-mode so every command acts on the
same set of services.
EOF
    ;;
esac
