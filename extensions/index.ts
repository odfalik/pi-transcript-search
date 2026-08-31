import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runner = resolve(packageRoot, "runner.py");
const python = process.env.PI_TRANSCRIPT_SEARCH_PYTHON || "python3";

function addFlag(args: string[], name: string, value: string | number | boolean | undefined) {
  if (value === undefined || value === false) return;
  args.push(`--${name}`);
  if (value !== true) args.push(String(value));
}

async function runCli(
  pi: ExtensionAPI,
  args: string[],
  signal: AbortSignal | undefined,
): Promise<{ text: string; details: unknown }> {
  const result = await pi.exec(python, [runner, ...args], { signal, timeout: 120_000 });
  if (result.code !== 0) {
    const reason = result.stderr.trim() || result.stdout.trim() || `exit ${result.code}`;
    throw new Error(`pi-transcript-search failed: ${reason}`);
  }
  const text = result.stdout.trim();
  try {
    return { text, details: JSON.parse(text) };
  } catch {
    throw new Error("pi-transcript-search returned invalid JSON");
  }
}

export default function transcriptSearchExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "transcript_search",
    label: "Transcript Search",
    description:
      "Search or list past Pi conversations. Searches only user and assistant text; excludes thinking, tool calls, tool results, and injected scaffolding. Returns bounded snippets with stable session IDs and message ordinals.",
    parameters: Type.Object({
      query: Type.Optional(Type.String({ minLength: 1, description: "Topic words or exact phrase" })),
      exact: Type.Optional(Type.Boolean({ description: "Require query words as an adjacent phrase" })),
      days: Type.Optional(Type.Integer({ minimum: 1, maximum: 36_500 })),
      date: Type.Optional(Type.String({ description: "YYYY-MM-DD, today, or yesterday" })),
      since: Type.Optional(Type.String({ description: "Include sessions starting on/after this local day" })),
      until: Type.Optional(Type.String({ description: "Include sessions through this local day" })),
      cwd: Type.Optional(Type.String({ description: "Literal case-insensitive substring of session cwd" })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
      noRefresh: Type.Optional(Type.Boolean({ description: "Query the existing index without refreshing" })),
    }),
    async execute(_toolCallId, params, signal) {
      const args: string[] = [];
      if (params.query) {
        args.push("search", params.query);
        addFlag(args, "exact", params.exact);
      } else {
        args.push("list");
      }
      addFlag(args, "days", params.days);
      addFlag(args, "date", params.date);
      addFlag(args, "since", params.since);
      addFlag(args, "until", params.until);
      addFlag(args, "cwd", params.cwd);
      addFlag(args, "limit", params.limit ?? (params.query ? 20 : 50));
      addFlag(args, "no-refresh", params.noRefresh);
      args.push("--json");
      const result = await runCli(pi, args, signal);
      return { content: [{ type: "text", text: result.text }], details: result.details };
    },
  });

  pi.registerTool({
    name: "transcript_read",
    label: "Transcript Read",
    description:
      "Read a bounded user/assistant message window from a Pi conversation found by transcript_search.",
    parameters: Type.Object({
      sessionId: Type.String({ minLength: 1 }),
      around: Type.Optional(Type.Integer({ minimum: 0, maximum: 2_147_483_647 })),
      context: Type.Optional(Type.Integer({ minimum: 0, maximum: 20 })),
      role: Type.Optional(StringEnum(["user", "assistant"] as const)),
      maxChars: Type.Optional(Type.Integer({ minimum: 1, maximum: 20_000 })),
      noRefresh: Type.Optional(Type.Boolean()),
    }),
    async execute(_toolCallId, params, signal) {
      const args = ["show", params.sessionId];
      addFlag(args, "around", params.around);
      addFlag(args, "context", params.context ?? 3);
      addFlag(args, "role", params.role);
      addFlag(args, "max-chars", params.maxChars ?? 12_000);
      addFlag(args, "no-refresh", params.noRefresh);
      args.push("--json");
      const result = await runCli(pi, args, signal);
      return { content: [{ type: "text", text: result.text }], details: result.details };
    },
  });
}
