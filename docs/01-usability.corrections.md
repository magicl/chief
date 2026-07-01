# Chief — Usability corrections

This file relates to [`01-usability.md`](01-usability.md) and [`01-usability.impl.md`](01-usability.impl.md).

FIX:

- [x] Instead of the conversation view, show the full event log (all events). Remove the expandable duplicate event log. Call it **Events** view.
- [x] In each event log entry, show a stats field on the top row (right-aligned, same line as `#n` and tag): response time and cost when present.
- [x] Clicking **Chief** in the header returns to the main dashboard.
- [x] On the dashboard **Recent sessions** table: Session identifier is the first column; Agent identifier is clickable.
- [x] Session top bar shows model name and **Total** session cost; total updates live as SSE events arrive (sum of per-event `cost_usd`).
- [x] Tool call cost: LLM token cost is recorded on **OUTPUT** events (each provider round-trip), not on **TOOL_CALL**. Builtin tools have no token cost; **TOOL_RESULT** now records execution latency in stats so tool rows still show timing.

BUGS:

- [x] Events not showing on the session page — broken `x-data` attribute (double-quoted `tojson` inside double-quoted attribute broke Alpine init).
- [x] View stuck spinning on back navigation — session SSE connections were not closed on leave, exhausting browser per-host connection slots; now closed on `pagehide` and reconnected after bfcache restore.
no
