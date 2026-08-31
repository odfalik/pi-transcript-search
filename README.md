# Pi Transcript Search

Fast, local, evidence-bearing search over [Pi](https://github.com/badlogic/pi-mono) conversation history.

```bash
pi-transcript-search search "authentication timeout" --days 30 --json
pi-transcript-search list --date yesterday --json
pi-transcript-search show <session-id> --around 42 --context 3 --json
```

## Scope

Pi Transcript Search indexes Pi sessions only. Pi records the conversation independently of the selected model provider, so sessions remain searchable whether Pi used an OpenAI, Anthropic, Google, or local model.

The searchable corpus contains:

- User-visible user text
- Product-visible assistant text
- Session IDs, names, working directories, and timestamps

The index deliberately excludes:

- Thinking blocks
- Tool calls and arguments
- Tool results
- System and developer scaffolding
- Images and binary attachments

This boundary keeps generated context and repeated command output from dominating topic search.

## Installation

Install the Pi package for the `transcript_search` and `transcript_read` tools plus the bundled Agent Skill:

```bash
pi install npm:pi-transcript-search
```

The package requires Node 24+ and Python 3.10+. It bundles the Python search code and uses only the standard library at runtime.

For standalone CLI use:

```bash
uv tool install pi-transcript-search
pi-transcript-search install-skill
```

Restart Pi or run `/reload` after installing resources.

## Pi tools

Ask Pi naturally to find earlier work. The extension exposes:

- `transcript_search`: topic search when `query` is provided, or temporal listing when omitted
- `transcript_read`: bounded user/assistant context around a matched message ordinal

The extension has no startup hook, timer, hidden context injection, or background process. Index refresh happens only when a tool or CLI command requests it.

## Commands

### Search by topic

```bash
pi-transcript-search search "index corruption" --json
pi-transcript-search search "index corruption" --days 14 --json
pi-transcript-search search "exact phrase" --exact --json
pi-transcript-search search "release" --cwd project-substring --json
```

Default topic search requires every query word in one message and supports word prefixes. `--exact` requires an adjacent phrase. Results are ranked with SQLite FTS5 BM25 and contain bounded snippets from user or assistant messages.

### List by time

```bash
pi-transcript-search list --date today --json
pi-transcript-search list --date yesterday --json
pi-transcript-search list --days 7 --json
pi-transcript-search list --since 2026-08-01 --until 2026-08-15 --json
```

`--date`, `--since`, and `--until` use local calendar-day boundaries. `--days` is a rolling 24-hour window.

### Read bounded evidence

```bash
pi-transcript-search show <session-id> --around 42 --context 3 --json
pi-transcript-search show <session-id> --around 42 --role assistant --max-chars 12000 --json
```

Transcript output is capped at 20,000 characters by default. The JSON response reports `truncated: true` when more content exists.

### Index health

```bash
pi-transcript-search status --json
pi-transcript-search index --json
pi-transcript-search index --rebuild --json
```

Search and list refresh the index incrementally before querying. `--no-refresh` queries the last committed state.

## Storage and privacy

Native Pi sessions remain the source of truth and are never modified:

```text
~/.pi/agent/sessions/**/*.jsonl
```

The disposable derived index defaults to:

```text
~/.local/share/pi-transcript-search/index.sqlite
```

Its directory is set to mode `0700` and the database to `0600`. Startup fails if either mode cannot be applied and verified. Override either location for testing or custom setups:

```bash
PI_TRANSCRIPT_SESSIONS_DIR=/path/to/sessions \
PI_TRANSCRIPT_SEARCH_DB=/path/to/index.sqlite \
pi-transcript-search index
```

The npm extension invokes `python3`. Set `PI_TRANSCRIPT_SEARCH_PYTHON` to a different Python 3.10+ executable when needed.

The program has no network code, telemetry, account, daemon, model dependency, or background process. SQLite may keep transcript pages in `index.sqlite-wal` and `index.sqlite-shm`. To remove the derived copy, stop active search commands and delete the database plus both sidecars. The next search rebuilds the index. Deleting a native session removes its indexed records on the next refresh.

The index contains conversation text and must be treated as sensitive local data.

## Index behavior

- Initial indexing parses complete Pi JSONL files.
- Later refreshes inspect file metadata and process only appended complete lines.
- Unterminated active JSONL lines remain unindexed until the writer adds a newline.
- Shrunk or rewritten files are rebuilt atomically within the SQLite transaction.
- Removed source files are pruned automatically.
- Every indexed message retains its Pi entry ID and ordinal for bounded retrieval.

## Limitations

- Search indexes text only. It does not index images.
- Search indexes stored branch entries in a Pi session file, not only its current leaf.
- Session start time drives date filtering. A long-running session is grouped by when it began.
- Exact exhaustive analysis is limited to indexed user and assistant text. Excluded tool results are intentionally outside the search corpus.
- The npm Pi package requires Node 24+ and Python 3.10+.
- Python must include SQLite FTS5, which standard CPython distributions provide.

## Development

```bash
uv sync
uv run pytest -v
uv build
npm install
npm run check
npm run pack:check
```

Tests use neutral synthetic sessions. They do not inspect local Pi history.

## License

MIT
