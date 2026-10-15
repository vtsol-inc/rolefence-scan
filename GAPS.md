# What we found when we gave an AI access to a database

This is the gap log behind `rolefence-scan`. In October 2026 we built a governed setup with
open-source parts (MongoDB → MCP Toolbox for Databases → agentgateway → Keycloak → an AI
assistant) and tried to break it, one layer at a time. Every row below is something that did
not hold, could not be answered, or had to be done by hand. The examples use the demo company
that ships with the scanner, **Brightline Movers**, a made-up removals firm with customers,
enquiries, orders and supplier payments. The sales role must not see supplier cost; finance
may see everything.

Categories: **hand-edit** (done by hand, no tool), **two-places** (one decision written in
more than one place), **couldn't-answer** (no way to find out), **silent-failure** (it looked
fine and was not), **leak** (something reached the model that should not have).

Where the scanner catches a row, the check name is given.

## The database layer

| # | Category | What we found | Scanner check |
|---|----------|---------------|---------------|
| 1 | silent-failure | A tool's collection is not a boundary. A tool "about enquiries" ran `$lookup` into `customers` and returned names, emails and phones; another joined `supplier_payments` to `orders` and returned bank references. The database user, not the tool, decides what can be read. | cross-collection |
| 2 | silent-failure | Server-side JavaScript runs. `$function` in an aggregate returned its result and `$where` ran in a find. Neither the tool layer nor a read-only role blocks it; it is a database setting (`security.javascriptEnabled`). | server-side-js |
| 3 | silent-failure | Leaving a field out of the copy did not hide what it means. `orders.supplier_cost` was removed from the sales view, but a join of `price` to the base `orders` collection gave the margin per order wherever the role could still reach the base collection. Field lists do not stop derived values. | (planned: field wizard) |
| 4 | leak | `$collStats` returns the server's host name and collection sizes. Unauthorized errors echo the full command and session id back to the model. | leaks-in-errors |
| 5 | couldn't-answer | There is no way to ask MongoDB or Toolbox "what can this role read right now?" We had to add temporary probe tools to the config, run them, and remove them again. | (the scanner's database checks are that probe) |
| 6 | silent-failure | The database accepted the MCP server's connection with no credentials, and the server reported "ready". The missing login only showed on the first tool call. With authentication off, views and roles enforce nothing at all. | db-no-auth |

## The question server (MCP Toolbox)

| # | Category | What we found | Scanner check |
|---|----------|---------------|---------------|
| 7 | hand-edit | There is no prebuilt MongoDB tool set, so every tool is written by hand. A baseline for 7 collections is 13 near-identical blocks; each new collection means two more. | — |
| 8 | silent-failure | Aggregate tools have no row cap. Find tools stop at 20 rows; a pipeline without `$limit` returns the whole collection (2,000 customers in one reply). | no-row-cap |
| 9 | leak | Find tools return whole documents unless a projection is written. "Find a customer" returned every field on 20 customers. | whole-documents |
| 10 | silent-failure | Aggregate tools without `readOnly: true` accept `$out` and `$merge`; with a database user that can write, a pipeline creates a new collection. | aggregate-not-readonly, writes-allowed |
| 11 | silent-failure | Tools that take the whole filter or pipeline from the model are not "questions" at all; the model writes any query it likes, including the joins in row 1. | free-form-queries |
| 12 | couldn't-answer | Status fields are bare integers (`enquiries.status` has five codes) and dates are timestamps. Nothing in the tools tells the model what `3` means, so it guesses. | — |
| 13 | silent-failure | Toolbox serves a `default` toolset at `/mcp` holding every tool from every role, without being asked. `/mcp/sales` is a menu, not a boundary; drop the name and you get the finance tools. | default-toolset |
| 14 | silent-failure | A bad edit to the config is rejected on reload, but the server keeps serving the old config and says so only in its own log. From the client side the new tools simply "do not exist". | — (needs a watcher, not a scan) |
| 15 | two-places | One decision ("sales must not see supplier cost") lives in three files: the field list in the view script, the projection in the tool config, and the role notes. Change one and forget the others. | — |
| 16 | two-places | A tool is tied to one database user, so giving the same question to two roles means copying the whole tool block. | — |
| 17 | couldn't-answer | A fixed "lost enquiries by month" tool returned a number that looked right and meant nothing, because an automatic job had set the `lost` flag on every old enquiry. No tool can know what a field means; the person who owns the data can. | — |

## The gateway and the login

| # | Category | What we found | Scanner check |
|---|----------|---------------|---------------|
| 18 | silent-failure | The gateway protects only traffic that goes through it. The question server behind it still answers with no token and lists all tools. | round-the-gateway |
| 19 | silent-failure | Both locks on (gateway login and the server's own login): by default the gateway removes the token it has checked before forwarding, so the server behind it refuses everything and the person sees a bare 401. One setting (`backendAuth: passthrough`) fixes it; nothing tells you. | token-refused |
| 20 | silent-failure | With the server's own login and per-tool scopes, a sales token is refused a finance tool (403), but the tool list still shows the finance tools. The model sees what it cannot use. | tool-list-not-filtered |
| 21 | silent-failure | A token without the right scope is refused even on the role's own tools, and the published metadata does not say which scopes to ask for. A client that asks for a plain token fails with no reason given. | scopes-not-advertised |
| 22 | two-places | The gateway decides by role and the server by scope. When they disagree (sales role, token without `demo:sales`), the gateway shows the tools and the server refuses them. Two systems, one decision. | — |
| 23 | two-places | The gateway's role rule is a naming convention: sales may call tools whose names start with `sales_`. Rename a tool in the config and the rule silently changes. | — |
| 24 | silent-failure | The gateway listens on every network interface. Anyone on the same network can reach it; a login still stands in the way, but the door should not be visible. There is no bind setting for the MCP listener. | listening-everywhere |
| 25 | couldn't-answer | Identity stops at the gateway. The token names the person and role, but the server connects to the database as one fixed login per role, so the database cannot tell people apart and its log cannot either. | — |
| 26 | couldn't-answer | The gateway's request log names the caller by the identity provider's internal id (`jwt.sub=f305f66e-…`), not by username. "Who called this tool?" needs a lookup somewhere else. | — |
| 27 | hand-edit | An AI app cannot sign itself up: dynamic client registration is refused by Keycloak's default policy, so each AI app needs a hand-registered client with a fixed callback port. | — |
| 28 | hand-edit | Setting up the identity provider is a script of admin API calls: realm, roles, client, audience mappers, scopes with role mappings, users. In dev mode its data lives inside the container and is lost with it. | — |
| 29 | silent-failure | One AI session held the governed link (through the gateway) and an old direct link to the server side by side. Signed in as sales, it was still offered the finance tools through the direct link. Removing a link from a config file is the only control. | round-the-gateway (if you know to point it there) |

## The AI app

| # | Category | What we found | Scanner check |
|---|----------|---------------|---------------|
| 30 | leak | The commonest way to connect an AI app to a database skips all of the above: the app starts the MCP server as a program on the person's computer, and the database login, password included, is written in plain text in the app's settings file. One file read gives the login, often for a database on another computer, with no gateway, login or role in between. | plaintext-db-password, local-tools-open |

## What this adds up to

Thirty rows, and the three that cost the most time are the ones no tool can see from
outside: one decision written in three places (15), what a field means (17), and who the
person really is once the request reaches the database (25). The scanner finds the rest.

The scanner is free. The part that writes one decision into every layer, keeps the history,
and lets an administrator do it without a developer, is what [RoleFence](https://rolefence.com)
is for.
