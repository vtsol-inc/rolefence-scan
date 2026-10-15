# What an AI model can reach through your MCP server, and how to check

*A write-up from building a governed AI-to-database setup with open-source parts, October
2026. The examples use Brightline Movers, a made-up company. The tool it led to is
[rolefence-scan](https://github.com/vtsol-inc/rolefence-scan).*

## The setup everyone is building right now

A company wants its people to ask an AI assistant questions about company data. The common
way to do it today:

1. The data sits in a database (here, MongoDB).
2. An **MCP server** sits in front of it and holds a list of questions the AI may ask. We used
   Google's MCP Toolbox for Databases.
3. A **gateway** sits in front of that and checks who is asking. We used agentgateway with
   Keycloak for the login.
4. The AI assistant (Claude, Codex, Cursor, …) talks to the gateway.

Each layer is supposed to limit what the one above it can do. We built it, then spent three
days trying to get past each layer. This is what we learned.

## Finding 1: the tool is not the boundary, the database user is

The first thing people build is a handful of "find" and "aggregate" tools, one per collection.
It feels safe: the enquiries tool is about enquiries.

It is not. A tool bound to `enquiries` can run `$lookup` into `customers` and return every
name, email and phone number, because the database user the server connects with can read
`customers`. The tool's collection is a default, not a limit. The same user could run
JavaScript inside the database (`$function`, `$where`), and if it had write rights, a
pipeline ending in `$out` would create a new collection.

What holds: one database user per role, with a custom role that grants `find` on exactly the
collections or views that role needs, and nothing else. When we did that for the sales user,
`$lookup` into `orders` was refused by the database itself, whatever the tool said.

What does not hold: removing a field. We took `supplier_cost` out of the sales view, but
wherever sales could still reach the base `orders` collection, `price` minus `supplier_cost`
gave the margin per order. Hiding a field does not hide what can be worked out from others.
Only the person who knows the business can say which fields are secret together.

## Finding 2: the menu is not a boundary either

Toolbox lets you group tools into toolsets and serve each at its own address: `/mcp/sales`,
`/mcp/finance`. We gave the sales assistant the sales address and felt good.

Then we called `/mcp` with no toolset name. It served every tool from every role. Toolsets
are a menu for the client to pick from; nothing stops a client picking a different one.
The finance "margin" tool answered a caller who had only ever been given the sales address.

What holds: a login that is checked per request, and a rule that hides tools by role. That is
what the gateway is for.

## Finding 3: the gateway protects only what goes through it

With Keycloak and agentgateway in place, the gateway did its job: no token, no answer; a
sales token sees only the three sales tools; calling a finance tool as sales gets "unknown
tool", which is better than "forbidden" because the model never learns it exists.

Then we called the question server directly, on its own port, with no token. It answered and
listed everything. The gateway is a front door; the back door was still open.

What holds: either bind the question server so only the gateway can reach it, or turn on the
question server's own login as a second lock. We did the second. It worked, with two catches:

- **The gateway throws the token away.** By default agentgateway checks the token and then
  removes it before forwarding. The server behind it, now expecting a token, refused every
  request. The person sees "401", nothing more. One setting, `backendAuth: passthrough`,
  forwards the token; nothing in the error tells you that.
- **The server's own login does not hide tools.** With per-tool scopes, a sales token is
  refused on a finance tool (403), but the tool list still shows the finance tools. The
  model sees what it cannot use, tries it, fails, and may tell the user what exists.

So the gateway hides, the server enforces, and you need both. Two locks, two configs, two
places to keep in step.

## Finding 4: the small things that leak

- `$collStats` returns the server's host name and collection sizes to the model.
- Error messages echo the full query and session id back.
- The gateway listens on every network interface; anyone on the office network can reach it.
- Aggregate tools have no row cap: a pipeline without `$limit` returns the whole collection.
- Find tools return whole documents unless you write a projection for each one.

None of these is a break-in on its own. Together they tell an outsider what your servers are
called, how big your data is, and what shape your queries take.

## Finding 5: the things no scanner can see

Three of the gaps that cost us the most time cannot be found from outside:

1. **One decision lives in three places.** "Sales must not see supplier cost" was written in
   the view script, in the tool's projection, and in the gateway's rule. Change one and forget
   the others, and nothing warns you.
2. **What a field means.** A fixed "lost enquiries by month" tool returned a number that looked
   right and meant nothing, because an automatic job had set the `lost` flag on every old
   enquiry. Only the owner of the data knows that.
3. **Who the person is, once the request reaches the database.** The token names the person;
   the server connects to the database as one fixed login per role. The database's own log
   cannot tell people apart.

## How to check your own setup

We turned the checks we ran by hand into a tool, `rolefence-scan`. It is read-only, runs on
your own machine, and reports in plain English: what it saw, why it matters, how to close it.

```bash
uvx rolefence-scan ui          # a step-by-step page in your browser
```

or, with an assistant that speaks MCP, register it and ask: *"scan my MCP server as the sales
role"*.

Point it at the address your assistant uses. Give it, if you have them, a token for one role,
the server's own address behind the gateway, the database login the server uses, and the
server's config file. Each one unlocks more checks. On the demo company that ships with it,
the first-attempt setup gets 11 findings; the governed one gets 4; the governed one behind a
properly configured gateway gets 1 (the network interface, which agentgateway cannot yet
change).

The full gap log, 29 rows, is in [GAPS.md](../GAPS.md).

## What we are building next

The scanner finds. The hard part, writing one decision into every layer and keeping it there
without a developer, is what [RoleFence](https://rolefence.com) is for. If you run a setup like
this and found a gap we missed, open an issue; every check in the scanner started as someone's
"it shouldn't have been able to do that".
