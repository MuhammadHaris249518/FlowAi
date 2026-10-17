// MCP client for the Talkse voice agent's MCP server (Streamable HTTP transport, JSON-RPC 2.0).
// The FlowCRM AI Assistant offers these tools to the LLM next to its own CRM tools, so a
// chat in FlowCRM can check technician availability, book visits, read call transcripts and
// answer the owner's pending approval requests.
//
// TALKSE_MCP_URL: e.g. https://<talkse-host>/mcp/  (unset = Talkse tools disabled)

import type { AssistantToolDefinition } from "../modules/assistant/assistant.types";

const MCP_URL = process.env.TALKSE_MCP_URL ?? "";
const PROTOCOL_VERSION = "2025-03-26";
const TOOL_PREFIX = "talkse__";
const TOOLS_TTL_MS = 60_000;

interface McpTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

let rpcId = 0;
let sessionId: string | null = null;
let initialized = false;
let toolCache: { at: number; tools: McpTool[] } | null = null;

async function rpc(method: string, params?: Record<string, unknown>, notification = false): Promise<any> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
  };
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;
  const body = notification
    ? { jsonrpc: "2.0", method, params }
    : { jsonrpc: "2.0", id: ++rpcId, method, params };

  const res = await fetch(MCP_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(20_000),
  });
  const sid = res.headers.get("mcp-session-id");
  if (sid) sessionId = sid;
  if (notification) return null;
  if (!res.ok) throw new Error(`Talkse MCP ${method} failed: HTTP ${res.status}`);

  // The server answers with JSON; Streamable HTTP also allows an SSE stream carrying the response
  const text = await res.text();
  const payload = (res.headers.get("content-type") ?? "").includes("text/event-stream")
    ? JSON.parse(
        text
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .pop() ?? "{}"
      )
    : JSON.parse(text);
  if (payload.error) throw new Error(`Talkse MCP ${method}: ${payload.error.message}`);
  return payload.result;
}

async function ensureInitialized(): Promise<void> {
  if (initialized) return;
  await rpc("initialize", {
    protocolVersion: PROTOCOL_VERSION,
    capabilities: {},
    clientInfo: { name: "flowcrm-assistant", version: "1.0.0" },
  });
  await rpc("notifications/initialized", undefined, true);
  initialized = true;
}

export const talkseMcp = {
  enabled: () => Boolean(MCP_URL),

  isTalkseTool: (name: string) => name.startsWith(TOOL_PREFIX),

  /** Talkse's MCP tools as LLM function definitions (prefixed talkse__). Empty if Talkse is unreachable. */
  async toolDefinitions(): Promise<AssistantToolDefinition[]> {
    if (!MCP_URL) return [];
    try {
      if (!toolCache || Date.now() - toolCache.at > TOOLS_TTL_MS) {
        await ensureInitialized();
        const result = await rpc("tools/list");
        toolCache = { at: Date.now(), tools: result.tools ?? [] };
      }
      return toolCache.tools.map((t) => ({
        type: "function",
        function: {
          name: `${TOOL_PREFIX}${t.name}`,
          description: `[Talkse voice agent] ${t.description ?? t.name}`,
          parameters: t.inputSchema ?? { type: "object", properties: {} },
        },
      }));
    } catch (err) {
      console.warn("Talkse MCP unavailable:", err instanceof Error ? err.message : err);
      initialized = false;
      sessionId = null;
      return [];
    }
  },

  /** Calls a talkse__ tool over MCP and returns its result (parsed JSON when possible). */
  async callTool(prefixedName: string, args: Record<string, unknown>): Promise<unknown> {
    await ensureInitialized();
    const result = await rpc("tools/call", { name: prefixedName.slice(TOOL_PREFIX.length), arguments: args });
    if (result?.structuredContent) return result.structuredContent;
    const text = (result?.content ?? [])
      .filter((c: { type: string }) => c.type === "text")
      .map((c: { text: string }) => c.text)
      .join("\n");
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  },
};
