#!/bin/bash
# Keycloak for the rolefence-scan demo. Creates realm `rolefence-demo`, roles `sales` and
# `finance`, public client `agentgateway` (password grant on, audience mappers for
# `agentgateway` and `toolbox`), optional client scopes `demo:sales` and `demo:finance` tied
# to the roles, and one user per role: demo_sales_user / demo_finance_user.
# Safe to re-run: existing objects are left alone, passwords and roles are re-applied.
#
# Needs a Keycloak container named `keycloak` on http://localhost:8080, e.g.
#   docker run -d --name keycloak -p 127.0.0.1:8080:8080 \
#     -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD="$KC_ADMIN_PW" \
#     quay.io/keycloak/keycloak:26.7.1 start-dev
# and three variables typed into the same terminal tab (never put them in a file):
#   read -s "KC_ADMIN_PW?Keycloak admin password: " && echo && \
#   read -s "DEMO_SALES_USER_PW?new demo_sales_user password: " && echo && \
#   read -s "DEMO_FINANCE_USER_PW?new demo_finance_user password: " && echo && \
#   export KC_ADMIN_PW DEMO_SALES_USER_PW DEMO_FINANCE_USER_PW && demo/gateway/setup_keycloak.sh
set -euo pipefail

: "${KC_ADMIN_PW:?type the Keycloak admin password first}"
: "${DEMO_SALES_USER_PW:?type the demo_sales_user password first}"
: "${DEMO_FINANCE_USER_PW:?type the demo_finance_user password first}"

REALM=rolefence-demo
CLIENT=agentgateway
KC_URL=${KC_URL:-http://localhost:8080}
kc() { docker exec -i keycloak /opt/keycloak/bin/kcadm.sh "$@"; }

echo "Waiting for Keycloak at $KC_URL ..."
for _ in $(seq 1 60); do
  curl -sf -o /dev/null "$KC_URL/realms/master" && break
  sleep 2
done

kc config credentials --server http://localhost:8080 --realm master \
  --user admin --password "$KC_ADMIN_PW" >/dev/null

kc get "realms/$REALM" >/dev/null 2>&1 || kc create realms -s realm="$REALM" -s enabled=true

for role in sales finance; do
  kc get "roles/$role" -r "$REALM" >/dev/null 2>&1 || kc create roles -r "$REALM" -s name="$role"
done

# Public client with the password grant on, so the demo can fetch tokens from a terminal.
# Two audience mappers: `agentgateway` (checked by the gateway) and `toolbox` (checked by
# Toolbox's own auth in demo/toolbox/governed_auth.yaml).
if [ "$(kc get clients -r "$REALM" -q clientId="$CLIENT" --fields id --format csv --noquotes)" = "" ]; then
  kc create clients -r "$REALM" -f - <<JSON
{
  "clientId": "$CLIENT",
  "enabled": true,
  "publicClient": true,
  "directAccessGrantsEnabled": true,
  "standardFlowEnabled": true,
  "redirectUris": ["http://localhost:*", "http://127.0.0.1:*"],
  "protocolMappers": [
    { "name": "agentgateway-audience", "protocol": "openid-connect",
      "protocolMapper": "oidc-audience-mapper",
      "config": { "included.custom.audience": "$CLIENT", "access.token.claim": "true", "id.token.claim": "false" } },
    { "name": "toolbox-audience", "protocol": "openid-connect",
      "protocolMapper": "oidc-audience-mapper",
      "config": { "included.custom.audience": "toolbox", "access.token.claim": "true", "id.token.claim": "false" } }
  ]
}
JSON
fi
CID=$(kc get clients -r "$REALM" -q clientId="$CLIENT" --fields id --format csv --noquotes)

# Optional client scopes tied to realm roles. With a role scope mapping Keycloak leaves a
# scope out of the token when the user lacks the role, even if the client asks for it.
make_scope() {  # scope-name role...
  local name="$1"; shift
  local sid
  sid=$(kc get client-scopes -r "$REALM" --fields id,name --format csv --noquotes | awk -F, -v n="$name" '$2==n{print $1}')
  if [ -z "$sid" ]; then
    kc create client-scopes -r "$REALM" -f - <<JSON
{ "name": "$name", "protocol": "openid-connect",
  "attributes": { "include.in.token.scope": "true", "display.on.consent.screen": "false" } }
JSON
    sid=$(kc get client-scopes -r "$REALM" --fields id,name --format csv --noquotes | awk -F, -v n="$name" '$2==n{print $1}')
  fi
  for role in "$@"; do
    kc get "roles/$role" -r "$REALM" --fields id,name | \
      python3 -c 'import json,sys; print(json.dumps([json.load(sys.stdin)]))' | \
      kc create "client-scopes/$sid/scope-mappings/realm" -r "$REALM" -f - >/dev/null 2>&1 || true
  done
  kc update "clients/$CID/optional-client-scopes/$sid" -r "$REALM" >/dev/null
}
make_scope demo:sales sales finance
make_scope demo:finance finance

make_user() {  # username role password
  kc get users -r "$REALM" -q username="$1" -q exact=true --fields username --format csv --noquotes | grep -qx "$1" ||
    kc create users -r "$REALM" -s username="$1" -s enabled=true -s emailVerified=true \
      -s email="$1@brightline.example" -s firstName="$2" -s lastName=Demo
  kc set-password -r "$REALM" --username "$1" --new-password "$3"
  kc add-roles -r "$REALM" --uusername "$1" --rolename "$2"
}
make_user demo_sales_user sales "$DEMO_SALES_USER_PW"
make_user demo_finance_user finance "$DEMO_FINANCE_USER_PW"

echo "Keycloak realm '$REALM' ready: roles sales, finance; client $CLIENT (aud agentgateway + toolbox);"
echo "scopes demo:sales (sales, finance), demo:finance (finance); users demo_sales_user, demo_finance_user."
