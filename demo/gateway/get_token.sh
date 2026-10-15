# Fetch a demo token into an environment variable. Prints nothing secret: only who the
# token is for, its roles, scopes and lifetime. Must be SOURCED so the variable lands in
# your shell:
#
#   read -s "DEMO_SALES_USER_PW?demo_sales_user password: " && echo && export DEMO_SALES_USER_PW
#   source demo/gateway/get_token.sh sales            # sets DEMO_SALES_TOKEN
#   source demo/gateway/get_token.sh finance          # sets DEMO_FINANCE_TOKEN (needs DEMO_FINANCE_USER_PW)
#   source demo/gateway/get_token.sh sales openid     # a sales token WITHOUT the demo:sales scope
#
# The second argument is the scope string to ask for (default: "openid demo:sales demo:finance";
# Keycloak drops the scopes the user's role does not allow).
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ] && [ -z "${ZSH_VERSION:-}" ]; then
  echo "source this script, do not run it: source demo/gateway/get_token.sh sales" >&2
  exit 1
fi

_rf_role="${1:-sales}"
_rf_scope="${2:-openid demo:sales demo:finance}"
_rf_kc="${KC_URL:-http://localhost:8080}"
case "$_rf_role" in
  sales)   _rf_user=demo_sales_user;   _rf_pw="${DEMO_SALES_USER_PW:-}";   _rf_var=DEMO_SALES_TOKEN ;;
  finance) _rf_user=demo_finance_user; _rf_pw="${DEMO_FINANCE_USER_PW:-}"; _rf_var=DEMO_FINANCE_TOKEN ;;
  *) echo "role must be sales or finance" >&2; return 1 ;;
esac
if [ -z "$_rf_pw" ]; then
  echo "type the password first: read -s \"DEMO_$(echo "$_rf_role" | tr a-z A-Z)_USER_PW?password: \" && echo && export DEMO_$(echo "$_rf_role" | tr a-z A-Z)_USER_PW" >&2
  return 1
fi

_rf_tok=$(curl -s -X POST "$_rf_kc/realms/rolefence-demo/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=agentgateway \
  --data-urlencode "username=$_rf_user" --data-urlencode "password=$_rf_pw" \
  --data-urlencode "scope=$_rf_scope" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d.get("access_token",""))' 2>/dev/null)
if [ -z "$_rf_tok" ]; then
  echo "no token for $_rf_user (is Keycloak up on $_rf_kc, realm rolefence-demo set up, password right?)" >&2
  unset _rf_role _rf_scope _rf_kc _rf_user _rf_pw _rf_var _rf_tok
  return 1
fi
export "$_rf_var=$_rf_tok"
# Describe the token without printing it: decode the payload (no verification needed here).
printf '%s' "$_rf_tok" | python3 -c '
import base64, json, sys, time
p = sys.stdin.read().split(".")[1]
c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
print("token for", c.get("preferred_username"), "| roles:", ",".join(c.get("realm_access", {}).get("roles", [])),
      "| scope:", c.get("scope"), "| aud:", c.get("aud"), "| expires in", int(c.get("exp", 0) - time.time()), "s")'
echo "$_rf_var is set in this shell"
unset _rf_role _rf_scope _rf_kc _rf_user _rf_pw _rf_var _rf_tok
