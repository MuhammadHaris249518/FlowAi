"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Bot, Inbox as InboxIcon, PhoneCall, RefreshCw, Send, Sparkles, Zap } from "lucide-react";
import { Topbar } from "@/components/layout/Topbar";
import { useApiContext } from "@/features/auth/hooks/use-api-context";
import { communicationApi } from "@/features/communication/api/communication-api";
import type { Message } from "@/features/communication/types";

// Where an inbox item came from. Notifications are received messages written by the
// Talkse AI receptionist (calls that need the owner) or by automation workflows (NOTIFY_OWNER);
// AI drafts are emails an ACTION_AI workflow step wrote for review.
type Source = "receptionist" | "automation" | "ai_draft" | "email";
type Tab = "all" | "notifications" | "drafts" | "sent";

function sourceOf(m: Message): Source {
  if (m.status === "DRAFT") return "ai_draft";
  if (m.fromAddress === "Talkse AI receptionist") return "receptionist";
  if (m.fromAddress === "Cool Breeze Automation" || m.fromAddress === "FlowCRM Automation") return "automation";
  return "email";
}

const SOURCE_META: Record<Source, { label: string; icon: typeof Bell; className: string }> = {
  receptionist: { label: "AI receptionist", icon: PhoneCall, className: "bg-teal-50 text-teal-700" },
  automation: { label: "Automation", icon: Zap, className: "bg-blue-50 text-blue-700" },
  ai_draft: { label: "AI draft", icon: Sparkles, className: "bg-fuchsia-50 text-fuchsia-700" },
  email: { label: "Email", icon: InboxIcon, className: "bg-surface-muted text-ink-700" },
};

const TABS: { id: Tab; label: string; match: (m: Message) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "notifications", label: "Notifications", match: (m) => m.direction === "INBOUND" },
  { id: "drafts", label: "AI drafts", match: (m) => m.status === "DRAFT" },
  { id: "sent", label: "Sent", match: (m) => m.direction === "OUTBOUND" && m.status !== "DRAFT" },
];

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export default function InboxPage() {
  const ctx = useApiContext();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sendState, setSendState] = useState<{ id: string; status: "sending" | "error"; error?: string } | null>(null);

  const { data: messages = [], isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["inbox", ctx.organizationId],
    queryFn: () => communicationApi.getInbox(ctx),
    enabled: Boolean(ctx.organizationId),
    refetchInterval: 15_000,
  });

  const visible = useMemo(() => messages.filter(TABS.find((t) => t.id === tab)!.match), [messages, tab]);
  const selected = visible.find((m) => m.id === selectedId) ?? visible[0] ?? null;
  const counts = Object.fromEntries(TABS.map((t) => [t.id, messages.filter(t.match).length])) as Record<Tab, number>;

  async function sendDraft(m: Message) {
    setSendState({ id: m.id, status: "sending" });
    try {
      await communicationApi.sendDraft(ctx, m.id);
      setSendState(null);
      await queryClient.invalidateQueries({ queryKey: ["inbox"] });
    } catch (err) {
      setSendState({ id: m.id, status: "error", error: err instanceof Error ? err.message : "Send failed" });
    }
  }

  return (
    <>
      <Topbar title="Inbox" />
      <main className="space-y-6 p-6 sm:p-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => { setTab(t.id); setSelectedId(null); }}
                className={`rounded-full px-4 py-1.5 text-sm font-medium transition ${
                  tab === t.id ? "bg-brand-500 text-white" : "bg-white text-ink-700 shadow-card hover:text-ink-900"
                }`}
              >
                {t.label} <span className={tab === t.id ? "text-white/80" : "text-ink-300"}>{counts[t.id]}</span>
              </button>
            ))}
          </div>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-ink-700 shadow-card hover:text-ink-900"
          >
            <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} aria-hidden /> Refresh
          </button>
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          {/* List */}
          <section className="overflow-hidden rounded-2xl bg-white shadow-card">
            {isLoading && <p className="p-6 text-sm text-ink-500">Loading inbox…</p>}
            {isError && <p className="p-6 text-sm text-red-600">Couldn&apos;t load the inbox. Try Refresh.</p>}
            {!isLoading && !isError && visible.length === 0 && (
              <div className="p-10 text-center">
                <Bell className="mx-auto h-8 w-8 text-ink-300" aria-hidden />
                <p className="mt-3 text-sm font-medium text-ink-700">Nothing here yet</p>
                <p className="mt-1 text-xs text-ink-500">
                  Calls that need you and automation alerts appear here as they happen.
                </p>
              </div>
            )}
            <ul className="max-h-[640px] divide-y divide-surface-border overflow-y-auto">
              {visible.map((m) => {
                const meta = SOURCE_META[sourceOf(m)];
                const Icon = meta.icon;
                const active = selected?.id === m.id;
                return (
                  <li key={m.id}>
                    <button
                      onClick={() => setSelectedId(m.id)}
                      className={`flex w-full gap-3 px-5 py-4 text-left transition ${active ? "bg-brand-50" : "hover:bg-surface-muted"}`}
                    >
                      <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${meta.className}`}>
                        <Icon className="h-4 w-4" aria-hidden />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-baseline justify-between gap-2">
                          <span className="truncate text-sm font-semibold text-ink-900">{m.subject || "(no subject)"}</span>
                          <span className="shrink-0 text-[11px] text-ink-300">{timeAgo(m.receivedAt ?? m.createdAt)}</span>
                        </span>
                        <span className="mt-0.5 block truncate text-xs text-ink-500">{m.body}</span>
                        <span className={`mt-1.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${meta.className}`}>
                          {meta.label}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          {/* Detail */}
          <section className="rounded-2xl bg-white p-6 shadow-card">
            {!selected ? (
              <p className="text-sm text-ink-500">Select a message to read it.</p>
            ) : (
              <article>
                <div className="flex flex-wrap items-center gap-2">
                  {(() => {
                    const meta = SOURCE_META[sourceOf(selected)];
                    const Icon = meta.icon;
                    return (
                      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${meta.className}`}>
                        <Icon className="h-3.5 w-3.5" aria-hidden /> {meta.label}
                      </span>
                    );
                  })()}
                  <span className="text-xs text-ink-300">
                    {new Date(selected.receivedAt ?? selected.createdAt).toLocaleString("en-US", {
                      weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                    })}
                  </span>
                </div>
                <h2 className="mt-3 text-lg font-semibold text-ink-900">{selected.subject || "(no subject)"}</h2>
                <dl className="mt-2 space-y-0.5 text-xs text-ink-500">
                  {selected.contactName && <div><dt className="inline font-medium">Customer: </dt><dd className="inline">{selected.contactName}</dd></div>}
                  {selected.fromAddress && <div><dt className="inline font-medium">From: </dt><dd className="inline">{selected.fromAddress}</dd></div>}
                  {selected.toAddress && <div><dt className="inline font-medium">To: </dt><dd className="inline">{selected.toAddress}</dd></div>}
                </dl>
                <div className="mt-5 whitespace-pre-wrap rounded-xl bg-surface-muted p-4 text-sm leading-relaxed text-ink-900">
                  {selected.body}
                </div>

                {selected.status === "DRAFT" && (
                  <div className="mt-5 rounded-xl border border-fuchsia-200 bg-fuchsia-50/60 p-4">
                    <p className="flex items-center gap-1.5 text-sm font-semibold text-fuchsia-800">
                      <Bot className="h-4 w-4" aria-hidden /> Drafted by AI from a workflow
                    </p>
                    <p className="mt-1 text-xs text-fuchsia-900/80">
                      Nothing is sent automatically. Review it, then send it to the customer.
                    </p>
                    <button
                      onClick={() => sendDraft(selected)}
                      disabled={!selected.toAddress || sendState?.status === "sending"}
                      className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-3 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Send className="h-3.5 w-3.5" aria-hidden />
                      {sendState?.id === selected.id && sendState.status === "sending" ? "Sending…" : "Send email"}
                    </button>
                    {!selected.toAddress && (
                      <p className="mt-2 text-xs text-ink-500">This customer has no email address on file.</p>
                    )}
                    {sendState?.id === selected.id && sendState.status === "error" && (
                      <p className="mt-2 text-xs text-red-600">{sendState.error}</p>
                    )}
                  </div>
                )}
              </article>
            )}
          </section>
        </div>
      </main>
    </>
  );
}
