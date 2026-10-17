// Tool-calling chat through Groq's OpenAI-compatible API. Lets the AI Assistant run without
// the separate Python ai-service: set GROQ_API_KEY (and optionally GROQ_ASSISTANT_MODEL).

import type {
  AssistantLlmMessage,
  AssistantLlmToolCall,
  AssistantToolDefinition,
} from "../modules/assistant/assistant.types";

const GROQ_URL = "https://api.groq.com/openai/v1/chat/completions";

export interface ChatTurn {
  content: string | null;
  tool_calls: AssistantLlmToolCall[];
}

function toOpenAiMessages(messages: AssistantLlmMessage[]) {
  return messages.map((m) => {
    if (m.role === "assistant" && m.tool_calls?.length) {
      return {
        role: "assistant",
        content: m.content ?? null,
        tool_calls: m.tool_calls.map((c) => ({
          id: c.id,
          type: "function",
          function: { name: c.name, arguments: JSON.stringify(c.arguments ?? {}) },
        })),
      };
    }
    if (m.role === "tool") {
      return { role: "tool", tool_call_id: m.tool_call_id, content: m.content ?? "" };
    }
    return { role: m.role, content: m.content ?? "" };
  });
}

export const groqChatClient = {
  enabled: () => Boolean(process.env.GROQ_API_KEY),

  async chat(
    system: string,
    messages: AssistantLlmMessage[],
    tools: AssistantToolDefinition[]
  ): Promise<ChatTurn> {
    const model = process.env.GROQ_ASSISTANT_MODEL ?? "openai/gpt-oss-20b";
    const body = JSON.stringify({
      model,
      messages: [{ role: "system", content: system }, ...toOpenAiMessages(messages)],
      tools,
      tool_choice: "auto",
      temperature: 0.2,
      ...(model.includes("gpt-oss") ? { reasoning_effort: "low" } : {}),
    });

    // Free tier has a tokens-per-minute limit: on 429 wait (Retry-After, capped) and retry up to twice
    let res: Response | null = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      res = await fetch(GROQ_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${process.env.GROQ_API_KEY}`,
        },
        body,
        signal: AbortSignal.timeout(45_000),
      });
      if (res.status !== 429 || attempt === 2) break;
      const waitS = Math.min(Number(res.headers.get("retry-after")) || 5, 15);
      await new Promise((r) => setTimeout(r, waitS * 1000));
    }
    if (!res) throw new Error("Groq chat failed: no response");
    if (!res.ok) {
      throw new Error(`Groq chat failed: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
    }
    const data = await res.json();
    const msg = data.choices?.[0]?.message ?? {};
    return {
      content: msg.content ?? null,
      tool_calls: (msg.tool_calls ?? []).map((c: any) => {
        let args: Record<string, unknown> = {};
        try {
          args = c.function?.arguments ? JSON.parse(c.function.arguments) : {};
        } catch {
          args = {};
        }
        return { id: c.id, name: c.function?.name, arguments: args };
      }),
    };
  },
};
