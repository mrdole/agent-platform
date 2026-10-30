---
name: bench-instrument-check
description: Take read-only measurements from lab instruments (spectrum analyzers, signal generators, power supplies, scopes) through the bench MCP server and compare them to requirements. Use for verifying RF behavior, sanity-checking a bench, or collecting evidence for an MR.
---
# Instrument checks

## Steps
1. `list_devices(bench)`, then `identify(device)` for each instrument you'll use. Record
   the IDN strings. Stop if an IDN doesn't match the inventory model.
2. Find the exact SCPI commands in the instrument's knowledge doc
   (`search_docs("<model> SCPI")`). Don't guess syntax, because vendors differ.
3. Read with `query(device, "<command>?")`. Queries only.
4. `write` is for configuration and stays off unless the operator enabled it. Even then,
   never change RF output state, level, frequency or attenuation unless the task says so
   and a human approved it (`bench/rf-safety.md`).
5. If a value is outside limits, re-measure once with the same settings. If it still
   fails, report it. Never adjust limits or settings to get a pass.

## Report
| Req | Parameter | Limit | Measured | Units | Instrument (IDN) | Time (UTC) | Pass |
|-----|-----------|-------|----------|-------|------------------|------------|------|

Add the instrument settings that affect the reading (RBW, span, reference level,
averaging) under the table.
