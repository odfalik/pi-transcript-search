import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const root = mkdtempSync(join(tmpdir(), "pi-transcript-search-"));
const sessions = join(root, "sessions", "--workspace-example--");
mkdirSync(sessions, { recursive: true });
const sessionId = "01900000-0000-7000-8000-000000000001";
const records = [
  {
    type: "session",
    version: 3,
    id: sessionId,
    timestamp: "2026-08-30T10:00:00.000Z",
    cwd: "/workspace/example",
  },
  {
    type: "message",
    id: "user0001",
    parentId: null,
    timestamp: "2026-08-30T10:01:00.000Z",
    message: {
      role: "user",
      content: [{ type: "text", text: "native extension needle" }],
    },
  },
];
writeFileSync(
  join(sessions, `2026-08-30T10-00-00.000Z_${sessionId}.jsonl`),
  `${records.map((record) => JSON.stringify(record)).join("\n")}\n`,
);

process.env.PI_TRANSCRIPT_SESSIONS_DIR = join(root, "sessions");
process.env.PI_TRANSCRIPT_SEARCH_DB = join(root, "index.sqlite");

try {
  const { default: extension } = await import("../extensions/index.ts");
  const tools = new Map();
  const pi = {
    registerTool(definition) {
      tools.set(definition.name, definition);
    },
    async exec(command, args, options = {}) {
      try {
        const result = await execFileAsync(command, args, {
          signal: options.signal,
          timeout: options.timeout,
          maxBuffer: 1024 * 1024,
        });
        return { code: 0, stdout: result.stdout, stderr: result.stderr };
      } catch (error) {
        return {
          code: typeof error.code === "number" ? error.code : 1,
          stdout: error.stdout ?? "",
          stderr: error.stderr ?? String(error),
        };
      }
    },
  };

  extension(pi);
  assert.deepEqual([...tools.keys()], ["transcript_search", "transcript_read"]);

  const search = await tools.get("transcript_search").execute(
    "call-search",
    { query: "native extension needle", limit: 5 },
    undefined,
  );
  assert.equal(search.details.meta.returned, 1);
  assert.equal(search.details.results[0].session_id, sessionId);
  assert.equal(search.details.results[0].matches[0].role, "user");

  const read = await tools.get("transcript_read").execute(
    "call-read",
    { sessionId, around: 0, context: 0, maxChars: 1000, noRefresh: true },
    undefined,
  );
  assert.equal(read.details.messages.length, 1);
  assert.equal(read.details.messages[0].text, "native extension needle");
} finally {
  rmSync(root, { recursive: true, force: true });
}
