# rolefence-scan

A read-only scanner for MCP servers that sit in front of a database. Point it at the address
your AI assistant uses, and it reports what a model could reach that it should not, in plain
English: what was seen, why it matters, how to close it.

It never writes to anything it checks. Tokens and connection strings are named by environment
variable, never passed as values.

Part of [RoleFence](https://rolefence.com). Apache 2.0.

## What it checks

| Check | Needs | What it looks for |
|-------|-------|-------------------|
| Open tool list | `--mcp` | The server lists and serves tools with no login |
| Default `/mcp` toolset | `--mcp .../mcp/<role>` | The bare `/mcp` address serves every tool |
| Scopes advertised | `--mcp` (login on) | Clients are told which scopes to ask for |
| Listening everywhere | `--mcp` on localhost | The port is also open on the machine's network address |
| Token refused | `--mcp --token-env` | A valid token gets 401 (a gateway in front may be dropping it) |
| Tool list not filtered | `--mcp --token-env` | The caller sees tools it may not call |
| Round the gateway | `--backend` | The question server answers directly, with no token |
| Writes allowed | `--db-env` | The AI's database user holds write rights (read from its privileges; no write is tried) |
| Server-side JavaScript | `--db-env` | `$function` / `$where` run |
| Cross-collection reach | `--db-env` | `$lookup` from a tool's collection pulls rows from others |
| Leaks in replies | `--db-env` | `$collStats` returns the host name; errors echo the full query |
| Write tools | `--config` | Insert, update or delete tools are defined |
| Free-form queries | `--config` | Tools take the whole filter or pipeline from the model |
| Aggregate not read-only | `--config` | `mongodb-aggregate` without `readOnly: true` |
| No row cap | `--config` | Fixed pipelines with no `$limit` |
| Whole documents | `--config` | Find tools with no projection |

MongoDB and [MCP Toolbox for Databases](https://github.com/googleapis/genai-toolbox) first,
with or without [agentgateway](https://agentgateway.dev) in front. Other databases later.

## Install

```bash
uvx rolefence-scan --help        # or: pipx install rolefence-scan
```

## Run

```bash
export SALES_TOKEN='...'                       # a token for one role, from your identity provider
export DB_URI='mongodb://user:pw@host/db?authSource=db'   # the user the question server connects with

rolefence-scan --mcp http://127.0.0.1:3001/mcp --token-env SALES_TOKEN --role sales \
               --backend http://127.0.0.1:5002/mcp \
               --db-env DB_URI --config tools.yaml --json report.json
```

Exit code: `0` nothing found, `1` low or medium findings, `2` any high finding, `3` could not run.
Use it in CI to stop a deploy that opens something up.

## Ask your assistant to run it

`rolefence-scan serve` runs it as an MCP server over stdio. Register it with Claude Code,
Codex or any MCP client, set the environment variables in the shell that starts it, and ask:
"scan my MCP server at http://127.0.0.1:3001/mcp as the sales role".

```json
{"mcpServers": {"rolefence-scan": {"command": "rolefence-scan", "args": ["serve"]}}}
```

## Try it on the demo

`demo/` holds a made-up removals company, Brightline Movers, with two Toolbox setups:
`weak.yaml` (one over-powered user, free-form tools, no login) and `governed.yaml` (fixed
questions, one user per role, projections). Everything in it is invented.

```bash
export DEMO_ADMIN_URI='mongodb://<admin>:<pw>@127.0.0.1:27017/admin'
uv run python demo/seed.py                    # writes database rolefence_demo
mongosh ... demo/setup_users.js               # see the header of that file for the command
toolbox --config demo/toolbox/weak.yaml --port 5010     # with DEMO_RW_URI set
rolefence-scan --mcp http://127.0.0.1:5010/mcp --db-env DEMO_RW_URI --config demo/toolbox/weak.yaml
```

The weak setup should produce several high findings; the governed one far fewer.

### With a login and a gateway

`demo/gateway/` adds the identity side: a Keycloak realm (`rolefence-demo`, roles `sales` and
`finance`, scopes `demo:sales` / `demo:finance`), a third Toolbox config with Toolbox's own
login (`demo/toolbox/governed_auth.yaml`, port 5012), and two [agentgateway](https://agentgateway.dev)
configs in front of it: `gateway_good.yaml` (port 3011, passes the token on) and
`gateway_weak.yaml` (port 3012, drops it, which is agentgateway's default). Passwords are typed
into the terminal, never written to a file; `get_token.sh` fetches a token into a variable and
prints only who it is for.

```bash
# 1. Keycloak (Docker) and the demo realm
docker run -d --name keycloak -p 127.0.0.1:8080:8080 -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD="$KC_ADMIN_PW" quay.io/keycloak/keycloak:26.7.1 start-dev
export KC_ADMIN_PW DEMO_SALES_USER_PW DEMO_FINANCE_USER_PW   # typed with read -s, see the script header
demo/gateway/setup_keycloak.sh

# 2. Toolbox with its own login (needs DEMO_SALES_URI / DEMO_FINANCE_URI as for governed.yaml)
toolbox --config demo/toolbox/governed_auth.yaml --port 5012

# 3. The two gateways (binary from https://github.com/agentgateway/agentgateway/releases)
agentgateway -f demo/gateway/gateway_good.yaml &
agentgateway -f demo/gateway/gateway_weak.yaml &

# 4. A sales token, then scan
source demo/gateway/get_token.sh sales                        # sets DEMO_SALES_TOKEN
rolefence-scan --mcp http://127.0.0.1:3011/mcp --token-env DEMO_SALES_TOKEN --role sales \
               --backend http://127.0.0.1:5012/mcp --config demo/toolbox/governed_auth.yaml
rolefence-scan --mcp http://127.0.0.1:3012/mcp --token-env DEMO_SALES_TOKEN --role sales \
               --backend http://127.0.0.1:5011/mcp
rolefence-scan --mcp http://127.0.0.1:5012/mcp --token-env DEMO_SALES_TOKEN --role sales
```

What each run shows:

| Target | Expected |
|--------|----------|
| Good gateway (3011) | Token accepted, sales sees only `sales_*` tools, scopes advertised, backend 5012 refuses direct calls. Only "listening everywhere" remains (the gateway has no bind setting). |
| Weak gateway (3012) | **Token refused**: the gateway checks the token, then drops it, so Toolbox answers 401. **Scopes not advertised.** With the open governed Toolbox (5011) as backend: **round the gateway**. |
| Toolbox alone (5012) | Login holds, but **the tool list is not filtered**: sales is shown the finance tools and refused when it calls them. No resource metadata. |

## Not in this version

- Derived values (a secret that can be worked out from fields the role may read, such as
  margin from price and cost). Planned as a wizard where you mark which fields are secret.
- Telling you when a config reload failed silently (needs to watch the server, not scan it).
- PostgreSQL and other databases.

## Safety

- Read-only by design: `connectionStatus` for privileges, `$lookup` / `$function` / `$collStats`
  inside read pipelines, `tools/call` with empty arguments. Nothing is inserted, updated or deleted.
- The report never contains data values, only field, collection and tool names and counts.
- `reports/` and `*.report.json` are git-ignored; reports can hold host names.
