#!/bin/bash
# One command for the whole gateway demo: Keycloak + realm, the login-protected Toolbox, the
# open Toolbox, the good and the weak gateway, a sales token, and three scans. Asks for
# passwords in the terminal (never stored), writes reports to reports/, and stops every
# process it started when it finishes or when you press Ctrl-C.
#
#   demo/gateway/run_live.sh
#
# Needs: Docker running, `toolbox` on PATH, the agentgateway binary (on PATH, or set
# AGENTGATEWAY=/path/to/agentgateway), the demo database seeded (demo/seed.py +
# demo/setup_users.js), and a Python env with rolefence-scan installed (.venv/ or PATH).
set -euo pipefail
cd "$(dirname "$0")/../.."

AGENTGATEWAY=${AGENTGATEWAY:-$(command -v agentgateway || true)}
[ -x "${AGENTGATEWAY:-/nonexistent}" ] || { echo "agentgateway binary not found; set AGENTGATEWAY=/path/to/agentgateway" >&2; exit 1; }
command -v toolbox >/dev/null || { echo "toolbox is not on PATH" >&2; exit 1; }
SCAN=${SCAN:-$( [ -x .venv/bin/rolefence-scan ] && echo .venv/bin/rolefence-scan || echo rolefence-scan )}
docker info >/dev/null 2>&1 || { echo "Docker is not running; start Docker Desktop and try again" >&2; exit 1; }
mkdir -p reports

pids=()
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

echo "== Passwords (typed here, never stored)"
if docker ps --format '{{.Names}}' | grep -qx keycloak; then
  read -rsp "Keycloak admin password (existing container): " KC_ADMIN_PW; echo
else
  read -rsp "New Keycloak admin password: " KC_ADMIN_PW; echo
fi
read -rsp "demo_sales_user password (new or existing): " DEMO_SALES_USER_PW; echo
read -rsp "demo_finance_user password (new or existing): " DEMO_FINANCE_USER_PW; echo
read -rsp "demo_sales DATABASE password: " DEMO_SALES_PW; echo
read -rsp "demo_finance DATABASE password: " DEMO_FINANCE_PW; echo
export KC_ADMIN_PW DEMO_SALES_USER_PW DEMO_FINANCE_USER_PW
export DEMO_SALES_URI="mongodb://demo_sales:${DEMO_SALES_PW}@127.0.0.1:27017/rolefence_demo?authSource=rolefence_demo"
export DEMO_FINANCE_URI="mongodb://demo_finance:${DEMO_FINANCE_PW}@127.0.0.1:27017/rolefence_demo?authSource=rolefence_demo"

echo "== Keycloak"
if docker ps -a --format '{{.Names}}' | grep -qx keycloak; then
  docker start keycloak >/dev/null
else
  docker run -d --name keycloak -p 127.0.0.1:8080:8080 -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
    -e KC_BOOTSTRAP_ADMIN_PASSWORD="$KC_ADMIN_PW" quay.io/keycloak/keycloak:26.7.1 start-dev >/dev/null
fi
demo/gateway/setup_keycloak.sh

echo "== Servers"
toolbox --config demo/toolbox/governed_auth.yaml --port 5012 > reports/toolbox-5012.log 2>&1 & pids+=($!)
toolbox --config demo/toolbox/governed.yaml      --port 5011 > reports/toolbox-5011.log 2>&1 & pids+=($!)
"$AGENTGATEWAY" -f demo/gateway/gateway_good.yaml > reports/gateway-3011.log 2>&1 & pids+=($!)
"$AGENTGATEWAY" -f demo/gateway/gateway_weak.yaml > reports/gateway-3012.log 2>&1 & pids+=($!)
for port in 5012 5011 3011 3012; do
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null && break
    sleep 1
  done
done
sleep 2

echo "== Token"
# shellcheck disable=SC1091
source demo/gateway/get_token.sh sales

run() {  # name, args...
  local name="$1"; shift
  echo; echo "==== $name"
  set +e
  "$SCAN" "$@" --json "reports/live-$name.json" | sed -n '1,/^Checks that/p' | grep -v '^   Why\|^   How\|^   Details'
  echo "(exit $?)"
  set -e
}
run good-gateway --mcp http://127.0.0.1:3011/mcp --token-env DEMO_SALES_TOKEN --role sales \
                 --backend http://127.0.0.1:5012/mcp --config demo/toolbox/governed_auth.yaml
run weak-gateway --mcp http://127.0.0.1:3012/mcp --token-env DEMO_SALES_TOKEN --role sales \
                 --backend http://127.0.0.1:5011/mcp
run toolbox-alone --mcp http://127.0.0.1:5012/mcp --token-env DEMO_SALES_TOKEN --role sales

echo
echo "Reports: reports/live-*.json. Expected: good gateway -> only 'listening everywhere';"
echo "weak gateway -> token refused, scopes not advertised, round the gateway; Toolbox alone -> tool list not filtered."
echo "Stopping the servers this script started (Keycloak stays up: docker stop keycloak)."
