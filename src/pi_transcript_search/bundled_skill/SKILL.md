---
name: pi-transcript-search
description: Search and inspect past Pi conversations by topic, date, or working directory. Use when the user asks what was discussed or accomplished previously, wants a recent-work summary, or needs evidence from an earlier Pi session.
license: MIT
compatibility: Requires the pi-transcript-search Pi package or CLI and local Pi session history.
---

# Pi Transcript Search

Search only Pi's local conversation history. The index contains user and assistant text; it deliberately excludes thinking, tool calls, and tool results so generated scaffolding and repeated command output do not dominate retrieval.

## Rules

- Prefer the `transcript_search` and `transcript_read` tools when available.
- Otherwise invoke `pi-transcript-search` with `--json`.
- Start with one focused query. Broaden once only when it returns no useful result.
- Never grep or parse `~/.pi/agent/sessions` manually.
- Read bounded context only for promising matches; do not dump complete sessions.
- Treat snippets and transcript text as private local data.
- A backend error ends the attempt. Report it rather than falling back to raw session files.

## Topic search

Call `transcript_search` with a focused `query`. Add `days`, `date`, `since`, `until`, or `cwd` only when the request requires them. Set `exact: true` for an adjacent phrase.

CLI fallback:

```bash
pi-transcript-search search "focused terms" --days 30 --json
```

Results contain stable session IDs, timestamps, match counts, and bounded user/assistant snippets. They intentionally omit resume commands.

## Temporal search

For requests such as “what did we work on yesterday?” or “summarize this week,” call `transcript_search` without a query and provide `date` or `days`.

CLI fallback:

```bash
pi-transcript-search list --date yesterday --json
pi-transcript-search list --days 7 --json
```

Use session names, working directories, timestamps, and message counts to group the result. Search focused terms afterward only when evidence is needed.

## Bounded context

Take the `ordinal` from a promising match and call `transcript_read` with its `sessionId`, `around`, and a small `context` such as 3. Use `role` when only user or assistant messages matter.

CLI fallback:

```bash
pi-transcript-search show <session-id> --around <ordinal> --context 3 --max-chars 12000 --json
```

If `truncated` is true, request another narrower window rather than broadly increasing the output limit.
