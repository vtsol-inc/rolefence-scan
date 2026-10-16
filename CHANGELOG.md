# Changelog

## Unreleased

- NIST AI RMF section in every report (text, HTML download, JSON and the wizard's result
  cards): each finding has an "AI RMF:" line with the subcategories it gives evidence for, and
  a summary at the end has one row per subcategory (GOVERN 1.6, GOVERN 6.1, MEASURE 2.7,
  MEASURE 2.8, MEASURE 2.10, MANAGE 1.3, MANAGE 3.1, MANAGE 4.1) with what the scan showed and
  a status: Evidence, Gaps to fix, or Not checked in this scan. A fixed row says accuracy,
  bias and model testing are not covered by this tool. A fixed line says the report gives
  evidence and does not certify compliance. Numbers and wording checked against NIST AI 100-1
  and NIST AI 600-1.
- The mapping is one data file, `src/rolefence_scan/data/ai_rmf.json`. JSON reports have a new
  `ai_rmf` list on each finding and an `ai_rmf` block (source, disclaimer, summary).

## 0.1.2 (15 Oct 2026)

- Local-program ("stdio") MCP servers: the ones an AI app starts from its settings file
  (`command` + `args` + `env`) are now found in Claude Code, Claude Desktop, Cursor and Codex
  settings and shown in the wizard as "runs as a local program".
- New check from the settings file only (nothing started): a database password (high) or a
  token or API key (medium) written there in plain text. Names the setting, never its value,
  and says whether the database is on this computer or another one.
- New run mode that starts a local program once, asks for its tool list, and stops it (never
  `tools/call`): checks for tools that change data and reports that every tool is open to
  whoever uses the computer. CLI `--stdio-from FILE [--server NAME]`, `--stdio "COMMAND"`,
  `--stdio-timeout`; wizard (asks before starting anything); `serve` tool `scan_local_program`.
- Reports list checks that "do not apply here" (the network checks, for a local program)
  separately from checks that could not run; JSON has a new `not_applicable` list.
- Wizard: "Found on this computer" is sorted from the settings alone into "Talks to a
  database" (first), "Unknown: may reach your data, check it", and well-known tools that are
  not databases (folded away, still scannable). The list of known servers is one data file,
  `src/rolefence_scan/data/known_servers.json`.
- Wizard: a server listed in several places shows once, with the apps that list it; scanning
  it checks every copy's settings. An address that is also a running server shows once.
- `GAPS.md` row 30: the database login in plain text in an AI app's settings file.
- Fix: a database user that can read every collection was reported as "Cross-collection
  reach is contained" (low) when the only refusal was `system.views`, which is refused even
  for such users. It is now reported as "A tool's collection is not a boundary" (medium), and
  the "contained" message no longer prints an empty list.

## 0.1.1 (15 Oct 2026)

- `rolefence-scan ui`: a step-by-step page in your browser (local only). Address with a
  connection test, optional token, database login in plain boxes with a "Test login" button,
  config file by drop or paste, review, live progress, findings as cards, HTML and JSON download.
- Auto-detect: finds running Toolbox and agentgateway processes (address, config file, login
  variable) and MCP addresses in Claude Code, Claude Desktop, Cursor and Codex settings.
- "Where do I find it?" help on each step.
- Scanner: a backend that does not answer is reported as "could not check", not as closed;
  a 500 from a gateway says the server behind it is probably down.
- Demo: `demo/gateway/run_live.sh` runs the whole gateway demo in one command; Toolbox with its
  own login is started with `--toolbox-url`, which it needs.
- `GAPS.md` (29 gaps, by layer, with the check that catches each) and
  `docs/what-a-model-can-reach.md`.

## 0.1.0 (15 Oct 2026)

- First release: 13 read-only checks (from outside, as the database user, from the config
  file), plain-English report with JSON output and exit codes, `serve` mode for MCP clients,
  the Brightline Movers demo with weak and governed setups, gateway demo configs, tests.
