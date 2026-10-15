# rolefence-scan

**Find out what your AI assistant can really see in your database, before someone else does.**

Part of [RoleFence](https://rolefence.com). Free and open source (Apache 2.0). Made by VTSOL INC.

## What is it?

Many companies now let an AI assistant (Claude, ChatGPT, Codex and others) answer questions
from the company database. The AI talks to the database through an "MCP server": a small
program that holds a list of questions the AI may ask.

The problem: these setups are new, done by hand, and easy to get slightly wrong. A tool meant
to answer "how many enquiries did we get in June?" can often also read every customer's phone
number, see the supplier costs that sales staff should never see, or even change data.

`rolefence-scan` is a checker. You point it at the address your AI uses, and it tells you, in
plain English:

- **what it saw** (for example: "the server lists 6 tools to anyone, with no login")
- **why it matters** (for example: "anyone on this address can ask those questions, and nobody
  is recorded as having asked")
- **how to close it** (for example: "put a login in front; here is which setting")

It only **reads**. It never changes anything in the database or the server it checks.

## What is the goal?

Before you give an AI access to company data, you want three things to be true:

1. The AI can **only read**, never write.
2. Each role (sales, finance, support, …) can reach **only its own data**, and the hidden
   fields really stay hidden.
3. There is a **login**, so you know who asked what.

`rolefence-scan` checks whether that is true today, so you can fix the gaps. Run it again
after every change, or in your deployment pipeline, so a mistake never reaches production
without a warning.

## How do I use it?

### 1. Install

You need Python 3.11 or newer.

```bash
uvx rolefence-scan --help
```

(or `pipx install rolefence-scan`)

### 2. The easy way: the wizard in your browser

```bash
rolefence-scan ui
```

This opens a page on your own computer (`http://127.0.0.1:8765`, reachable only from this
machine) that walks you through five short steps: the server address, an optional login token,
an optional database connection, an optional config file, then a review and the run. Each check
appears as it finishes; the findings are shown as cards you can download as an HTML or JSON
report. Tokens and connection strings are kept in memory for that one run and never saved.

### 2b. Or the command line

The simplest run: just the address your AI assistant is configured with.

```bash
rolefence-scan --mcp http://127.0.0.1:5000/mcp
```

That already tells you whether the server answers without a login, whether a "sales" address
leaks the "finance" tools, and whether the server is open to the whole office network.

### 3. Give it more, get more

Each extra piece of information unlocks more checks. Passwords and tokens are **never typed
on the command line**: you put them in an environment variable and give the scanner the
variable's *name*.

| Add this | And the scanner can also check |
|----------|-------------------------------|
| `--token-env SALES_TOKEN --role sales` (a login token for one role) | Is the token accepted? Does the sales role see tools it is not allowed to call? |
| `--backend http://…` (the server's own address behind the gateway) | Can someone skip the gateway and call the server directly? |
| `--db-env DB_URI` (the database connection the server uses) | Can the AI's database user write? Can it run code inside the database? Can one tool read other collections? |
| `--config tools.yaml` (the server's config file) | Are there write tools? Do tools accept any query? Do they return whole records, or unlimited rows? |

A full run looks like this:

```bash
export SALES_TOKEN='...'                                   # a login token for the sales role
export DB_URI='mongodb://user:pw@host/db?authSource=db'   # the connection the MCP server uses

rolefence-scan --mcp http://127.0.0.1:3001/mcp --token-env SALES_TOKEN --role sales \
               --backend http://127.0.0.1:5002/mcp \
               --db-env DB_URI --config tools.yaml --json report.json
```

The exit code tells a script how it went: `0` nothing found, `1` low or medium findings,
`2` at least one high finding, `3` it could not run. Use it in CI to stop a deploy that
opens something up.

### 4. Or just ask your AI assistant to run it

The scanner can itself be an MCP server. Register it with Claude Code, Codex or any MCP
client, and ask in plain words: *"scan my MCP server at http://127.0.0.1:3001/mcp as the
sales role"*. The assistant runs the checks and explains the report.

```json
{"mcpServers": {"rolefence-scan": {"command": "rolefence-scan", "args": ["serve"]}}}
```

Set the environment variables in the shell that starts the assistant. The assistant only ever
sees variable names, never the secrets.

## What does a report look like?

This is a real run against the "weak" demo setup that ships with the scanner (made-up data):

```
rolefence-scan report for http://127.0.0.1:5010/mcp/sales

11 finding(s): 6 high, 3 medium, 2 low

1. [HIGH] Tools are listed without a login
   What we saw:    The server answered with 3 tool(s) when no token was sent.
   Why it matters: Anyone who can reach this address can see every question the AI can ask
                   and call them. There is no record of who asked.
   How to close:   Put a login in front: a gateway that checks tokens, or the question
                   server's own auth setting.

2. [HIGH] The bare /mcp address serves every tool
   What we saw:    /mcp/sales lists 3 tool(s); /mcp lists 6, including 3 not meant for this caller.
   Why it matters: Per-role toolsets are a menu, not a boundary. Anyone who drops the toolset
                   name from the address gets everything.

3. [HIGH] The AI's database user can write
   What we saw:    The user the question server connects with holds write rights.
   Why it matters: A tool mistake, a prompt injection, or a pipeline with $out/$merge can
                   change or delete data.
   How to close:   Create a user with a custom read-only role and point the server at it.

4. [HIGH] Server-side JavaScript runs for the AI's database user
   ...
```

The same company, set up properly (one read-only user per role, fixed questions, a login in
front), gets 4 findings instead of 11, and the scanner says which ones remain.

## Examples

**"Our sales team's AI must never see supplier cost."**
Run with `--db-env` set to the sales user's connection. If the scanner reports *"cross-collection
reach"* into `orders` or *"the AI's database user can write"*, the database user is too
powerful, whatever the tools say. If it reports *"Cross-collection reach is contained by the
database role"*, the data layer is doing its job.

**"We put a gateway with login in front. Are we done?"**
Run with `--mcp` on the gateway and `--backend` on the server behind it. *"The question server
can be reached without the gateway"* means anyone who knows the inside address skips the login.

**"Users say they get 401 even with a valid login."**
Run with `--token-env`. *"A valid token is refused"* with the hint about pass-through means the
gateway checked the token and then threw it away before forwarding, so the server behind it
saw none. One setting fixes it.

**"A developer set this up months ago. Is it still safe?"**
Run it with everything you have, save `--json report.json`, and run it again after each change.
Compare the two.

## The full list of checks

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

Works today with MongoDB and [MCP Toolbox for Databases](https://github.com/googleapis/genai-toolbox),
with or without [agentgateway](https://agentgateway.dev) in front. Other databases are planned.

## Try it on the demo

`demo/` holds a made-up removals company, **Brightline Movers**: 2,000 customers, 3,200
enquiries, 1,110 orders, all invented. It comes with two setups:

- `demo/toolbox/weak.yaml`: how a first attempt usually looks. One over-powered database user,
  tools that accept any query, no login. **Expected: 11 findings.**
- `demo/toolbox/governed.yaml`: fixed questions, one read-only user per role, hidden fields
  removed by a view. **Expected: 4 findings**, all about the missing login (that is the
  gateway's job, below) and a database setting.

```bash
export DEMO_ADMIN_URI='mongodb://<admin>:<pw>@127.0.0.1:27017/admin'
uv run python demo/seed.py                    # writes database rolefence_demo
mongosh ... demo/setup_users.js               # see the header of that file for the command
toolbox --config demo/toolbox/weak.yaml --port 5010     # with DEMO_RW_URI set
rolefence-scan --mcp http://127.0.0.1:5010/mcp/sales --db-env DEMO_RW_URI --config demo/toolbox/weak.yaml
```

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

## What it does not do yet

- **Secrets that can be worked out from other fields.** If sales can see price and the AI can
  find cost somewhere, it can work out the margin. The scanner cannot know which fields are
  secret in *your* business. Planned: a short wizard where you tick the secret fields and the
  scanner checks each role against them.
- **Watching the server over time**, for example telling you that a config change was rejected
  and the old config is still running.
- **Fixing things.** It finds and explains; you (or the RoleFence console) make the change.
- **PostgreSQL and other databases.** MongoDB first.

## Is it safe to run on a live system?

Yes, by design:

- It only reads: it asks the database what the user is allowed to do, runs tiny read queries,
  and calls tools with empty arguments. Nothing is inserted, updated or deleted.
- The report never contains data values, only names of fields, collections and tools, and counts.
- Secrets are never on the command line, so they do not end up in shell history or logs.
- `reports/` and `*.report.json` are git-ignored, because a report can contain host names.

## Questions or ideas

Open an issue. If you found a gap the scanner missed, that is the most useful thing you can
tell us: every new check started as someone's "it shouldn't have been able to do that".
