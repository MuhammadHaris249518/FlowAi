import { AppError } from "../../errors/app-error";
import type { AuthContext } from "../../middleware/auth";
import { aiServiceClient } from "../../lib/ai-service-client";
import { groqChatClient } from "../../lib/groq-chat-client";
import { talkseMcp } from "../../lib/talkse-mcp-client";
import { ASSISTANT_TOOLS, executeAssistantTool } from "./assistant.tools";
import type {
  AssistantActionDTO,
  AssistantChatMessageDTO,
  AssistantChatResultDTO,
  AssistantLlmMessage,
  AssistantLlmToolCall,
  AssistantToolDefinition,
} from "./assistant.types";

const MAX_TOOL_ROUNDS = 6;

const SYSTEM_PROMPT = `You are the AI assistant inside the Cool Breeze CRM for a home-services business (AC, plumbing, electrical).
You have two kinds of tools:
- CRM tools (leads, contacts, deals, tasks, reports, messages): the business's sales data.
- Talkse tools (names start with "talkse__"), reached over MCP: the AI voice receptionist that answers customer
  calls. Use them to check technician availability, book visits, review recent calls and transcripts, see and
  answer requests waiting for the owner's approval, and manage the FAQs the receptionist answers by itself.
Always use tools for facts; never invent names, prices or times. Before booking a visit or answering an owner
request, make sure you have the details (customer name, service, area, time) and confirm what you did.
Keep answers short and practical.`;

/** One LLM turn: Groq when GROQ_API_KEY is set, otherwise the original Python ai-service. */
async function llmTurn(messages: AssistantLlmMessage[], tools: AssistantToolDefinition[]) {
  if (groqChatClient.enabled()) {
    return groqChatClient.chat(SYSTEM_PROMPT, messages, tools);
  }
  return aiServiceClient.assistantChat({ messages, tools });
}

async function runTool(
  auth: AuthContext,
  call: AssistantLlmToolCall,
  actions: AssistantActionDTO[]
): Promise<unknown> {
  if (talkseMcp.isTalkseTool(call.name)) {
    return talkseMcp.callTool(call.name, call.arguments ?? {});
  }
  return executeAssistantTool(auth, call.name, call.arguments ?? {}, actions);
}

export const assistantService = {
  async chat(auth: AuthContext, history: AssistantChatMessageDTO[]): Promise<AssistantChatResultDTO> {
    const messages: AssistantLlmMessage[] = history.map((m) => ({
      role: m.role,
      content: m.content,
    }));
    const actions: AssistantActionDTO[] = [];
    // CRM tools + the Talkse voice agent's tools over MCP (empty if Talkse is unreachable)
    const tools = [...ASSISTANT_TOOLS, ...(await talkseMcp.toolDefinitions())];

    for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
      let turn;
      try {
        turn = await llmTurn(messages, tools);
      } catch (err) {
        console.error("Assistant LLM error:", err instanceof Error ? err.message : err);
        throw new AppError(
          "AI assistant is temporarily unavailable. Try again in a moment.",
          503,
          "AI_SERVICE_UNAVAILABLE"
        );
      }

      if (turn.tool_calls && turn.tool_calls.length > 0) {
        messages.push({
          role: "assistant",
          content: turn.content,
          tool_calls: turn.tool_calls,
        });

        for (const call of turn.tool_calls) {
          let result: unknown;
          try {
            result = await runTool(auth, call, actions);
          } catch (err) {
            result = { error: err instanceof Error ? err.message : "Tool failed" };
          }
          messages.push({
            role: "tool",
            tool_call_id: call.id,
            name: call.name,
            content: JSON.stringify(result),
          });
        }
        continue;
      }

      const reply = (turn.content ?? "").trim();
      if (!reply) {
        throw new AppError(
          "The assistant returned an empty reply. Try rephrasing your question.",
          502,
          "AI_EMPTY_REPLY"
        );
      }
      return { reply, actions };
    }

    return {
      reply:
        "I needed too many lookups to finish that. Try a more specific question — for example a lead name, or 'top 5 leads by score'.",
      actions,
    };
  },
};
