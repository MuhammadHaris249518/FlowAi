import { apiClient, type RequestContext } from "@/lib/api-client";
import type { Message } from "../types";

export const communicationApi = {
  getThread: (
    ctx: RequestContext,
    params: { leadId?: string; contactId?: string; limit?: number }
  ) => {
    const query: Record<string, string> = {};
    if (params.leadId) query.leadId = params.leadId;
    if (params.contactId) query.contactId = params.contactId;
    if (params.limit) query.limit = String(params.limit);
    return apiClient.get<Message[]>("/communication/messages", ctx, query);
  },

  /** Org-wide inbox: AI receptionist + automation notifications, AI email drafts, emails. */
  getInbox: (ctx: RequestContext, limit = 100) =>
    apiClient.get<Message[]>("/communication/inbox", ctx, { limit: String(limit) }),

  sendDraft: (ctx: RequestContext, messageId: string) =>
    apiClient.post<Message>(`/communication/messages/${messageId}/send`, ctx),
};
