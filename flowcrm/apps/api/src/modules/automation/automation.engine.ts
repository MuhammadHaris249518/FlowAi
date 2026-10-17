import { prisma } from "../../lib/prisma";
import type { Workflow, WorkflowNode, WorkflowEdge, WorkflowRun } from "@prisma/client";
import { aiServiceClient, type EmailDraftContext } from "../../lib/ai-service-client";
import { groqChatClient } from "../../lib/groq-chat-client";

export const AI_POLL_INTERVAL_MS = 5000;
export const AI_MAX_POLL_ATTEMPTS = 40; // ~3.3 min ceiling — job_store is in-memory and non-durable

type NodeWithEdges = WorkflowNode;
type WorkflowWithGraph = Workflow & { nodes: NodeWithEdges[]; edges: WorkflowEdge[] };

// Builds the personalization context ai-service needs to draft a real email.
// Unlike lead scoring, this intentionally sends real names/notes, not boolean
// signals — the org boundary is enforced by scoping this query to context.organizationId.
async function buildEmailDraftContext(context: Record<string, unknown>): Promise<EmailDraftContext> {
  const organizationId = context.organizationId as string;
  const entityType = context.entityType as string;
  const entityId = context.entityId as string;

  if (entityType === "Lead") {
    const lead = await prisma.lead.findFirst({
      where: { id: entityId, organizationId },
      include: { contact: { include: { company: true } } },
    });
    return {
      contactName: lead?.contact?.fullName ?? null,
      companyName: lead?.contact?.company?.name ?? null,
      leadStatus: lead?.status ?? null,
      leadSource: lead?.source ?? null,
      notes: lead?.notes ?? null,
    };
  }

  if (entityType === "Deal") {
    const deal = await prisma.deal.findFirst({
      where: { id: entityId, organizationId },
      include: { contact: true, company: true },
    });
    return {
      contactName: deal?.contact?.fullName ?? null,
      companyName: deal?.company?.name ?? null,
      dealStage: deal?.stage ?? null,
      dealTitle: deal?.title ?? null,
    };
  }

  return {};
}

function findNextNodeId(edges: WorkflowEdge[], currentNodeId: string, branch?: "true" | "false"): string | null {
  const candidates = edges.filter((e) => e.sourceNodeId === currentNodeId);
  if (branch) {
    const match = candidates.find((e) => e.label === branch);
    return match?.targetNodeId ?? null;
  }
  return candidates[0]?.targetNodeId ?? null;
}

async function logStep(runId: string, nodeId: string, status: "SUCCESS" | "FAILED" | "SKIPPED", output?: unknown, error?: string) {
  await prisma.workflowRunLog.create({
    data: { runId, nodeId, status, output: output as object | undefined, error },
  });
}

function evaluateCondition(config: Record<string, unknown>, context: Record<string, unknown>): boolean {
  // v1 supports a single flat comparison: { field, op, value }.
  // field is a dotted path into context, e.g. "entity.score".
  const { field, op, value } = config as { field: string; op: string; value: unknown };
  const actual = field.split(".").reduce<unknown>((acc, key) => {
    if (acc && typeof acc === "object" && key in acc) return (acc as Record<string, unknown>)[key];
    return undefined;
  }, context);

  // The builder stores every typed value as a string ("1000", "MEETING"): compare numbers as numbers
  const num = (v: unknown) => (typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN);
  const same = String(actual ?? "").toLowerCase() === String(value ?? "").toLowerCase();

  switch (op) {
    case "eq": return same;
    case "neq": return !same;
    case "gte": return num(actual) >= num(value);
    case "lte": return num(actual) <= num(value);
    case "contains": return String(actual ?? "").toLowerCase().includes(String(value ?? "").toLowerCase());
    default: return false;
  }
}

/**
 * Names and values a node's text can reference as {{placeholder}}:
 * contactName, contactPhone, dealTitle, dealValue, dealStage, leadStatus, leadScore, leadSource.
 */
async function loadPlaceholders(context: Record<string, unknown>): Promise<Record<string, string>> {
  const organizationId = context.organizationId as string;
  const entityId = context.entityId as string;
  const out: Record<string, string> = {};

  if (context.entityType === "Lead") {
    const lead = await prisma.lead.findFirst({ where: { id: entityId, organizationId }, include: { contact: true } });
    Object.assign(out, {
      contactName: lead?.contact?.fullName ?? "the customer",
      contactPhone: lead?.contact?.phone ?? "",
      leadStatus: lead?.status ?? "",
      leadScore: String(lead?.score ?? ""),
      leadSource: lead?.source ?? "",
      contactId: lead?.contactId ?? "",
    });
  } else if (context.entityType === "Task") {
    const task = await prisma.task.findFirst({ where: { id: entityId, organizationId }, include: { contact: true } });
    Object.assign(out, {
      taskTitle: task?.title ?? "",
      taskPriority: task?.priority ?? "",
      taskDue: task?.dueAt ? task.dueAt.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "",
      contactName: task?.contact?.fullName ?? "the customer",
      contactPhone: task?.contact?.phone ?? "",
      contactId: task?.contactId ?? "",
    });
  } else if (context.entityType === "Deal") {
    const deal = await prisma.deal.findFirst({ where: { id: entityId, organizationId }, include: { contact: true } });
    Object.assign(out, {
      contactName: deal?.contact?.fullName ?? "the customer",
      contactPhone: deal?.contact?.phone ?? "",
      dealTitle: deal?.title ?? "",
      dealValue: deal ? `$${Number(deal.value).toLocaleString("en-US")}` : "",
      dealStage: deal?.stage ?? "",
      contactId: deal?.contactId ?? "",
    });
  }
  return out;
}

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key: string) => values[key] ?? "");
}

/** Who automation tasks go to when the event didn't name an assignee: the org's owner. */
async function defaultAssignee(organizationId: string): Promise<string | null> {
  const owner = await prisma.membership.findFirst({
    where: { organizationId, role: "ORG_OWNER" },
    select: { userId: true },
  });
  return owner?.userId ?? null;
}

/**
 * ACTION_AI without the Python ai-service: Groq drafts the email right away.
 * Same outcome as the ai-service path: a DRAFT message plus a review task, never auto-sent.
 */
async function draftEmailWithGroq(instructions: string, context: Record<string, unknown>): Promise<unknown> {
  const organizationId = context.organizationId as string;
  const leadId = context.entityType === "Lead" ? (context.entityId as string) : undefined;
  const dealId = context.entityType === "Deal" ? (context.entityId as string) : undefined;
  const values = await loadPlaceholders(context);
  const draftContext = await buildEmailDraftContext(context);

  const text = await groqChatClient.complete(
    "You write short, friendly customer emails for a small business. " +
      'Reply with JSON only: {"subject": "...", "body": "..."}. Plain text body, no markdown, under 120 words.',
    `Instructions: ${fill(instructions, values)}\n\nCustomer context: ${JSON.stringify(draftContext)}`
  );
  let subject = "Follow-up";
  let body = text.trim();
  const json = text.match(/\{[\s\S]*\}/);
  if (json) {
    try {
      const parsed = JSON.parse(json[0]) as { subject?: string; body?: string };
      subject = parsed.subject?.trim() || subject;
      body = parsed.body?.trim() || body;
    } catch {
      // not JSON: keep the raw text as the body
    }
  }

  const contact = values.contactId ? await prisma.contact.findUnique({ where: { id: values.contactId } }) : null;
  const draft = await prisma.message.create({
    data: {
      organizationId,
      contactId: values.contactId || undefined,
      leadId,
      dealId,
      channel: "EMAIL",
      direction: "OUTBOUND",
      status: "DRAFT",
      subject,
      body,
      toAddress: contact?.email ?? null,
      fromAddress: "AI Assistant",
    },
  });
  await prisma.task.create({
    data: {
      organizationId,
      title: `Review AI-drafted email: ${subject}`,
      description: `${body}\n\n---\nDrafted by AI from a workflow. Review it in the Inbox before sending.`,
      leadId,
      dealId,
      contactId: values.contactId || undefined,
      assigneeId: await defaultAssignee(organizationId),
      priority: "MEDIUM",
      messageId: draft.id,
    },
  });
  return { messageId: draft.id, subject };
}

async function executeStaticAction(config: Record<string, unknown>, context: Record<string, unknown>): Promise<unknown> {
  const action = config.action as string;
  const entityId = context.entityId as string;
  const organizationId = context.organizationId as string;
  const values = await loadPlaceholders(context);

  if (action === "CREATE_TASK") {
    return prisma.task.create({
      data: {
        organizationId,
        title: fill((config.title as string) ?? "Follow up", values),
        assigneeId: (context.assigneeId as string) ?? (await defaultAssignee(organizationId)),
        priority: (config.priority as "LOW" | "MEDIUM" | "HIGH") ?? "MEDIUM",
        dueAt: new Date(Date.now() + 24 * 60 * 60 * 1000),
        leadId: context.entityType === "Lead" ? entityId : undefined,
        dealId: context.entityType === "Deal" ? entityId : undefined,
        contactId: values.contactId || undefined,
      },
    });
  }

  if (action === "NOTIFY_OWNER") {
    // Notifications live in the Inbox as received messages from "FlowCRM Automation"
    return prisma.message.create({
      data: {
        organizationId,
        contactId: values.contactId || undefined,
        leadId: context.entityType === "Lead" ? entityId : undefined,
        dealId: context.entityType === "Deal" ? entityId : undefined,
        channel: "EMAIL",
        direction: "INBOUND",
        status: "RECEIVED",
        subject: fill((config.subject as string) ?? "Workflow notification", values),
        body: fill((config.message as string) ?? "", values),
        fromAddress: "FlowCRM Automation",
        receivedAt: new Date(),
      },
    });
  }

  if (action === "UPDATE_LEAD_STATUS") {
    return prisma.lead.update({
      where: { id: entityId },
      data: { status: config.status as "NEW" | "CONTACTED" | "QUALIFIED" | "DISQUALIFIED" | "CONVERTED" },
    });
  }

  throw new Error(`Unknown ACTION_STATIC action: ${action}`);
}

/**
 * Runs synchronous nodes (TRIGGER, CONDITION, ACTION_STATIC) forward until it
 * hits a DELAY node (persists WAITING + resumeAt), an ACTION_AI node
 * (deferred — sprint 2), or the graph ends (COMPLETED). Never holds a run
 * open across a delay — always returns after one node or one straight-line
 * synchronous run.
 */
export async function advanceRun(run: WorkflowRun, workflow: WorkflowWithGraph): Promise<void> {
  let currentNodeId = run.currentNodeId;
  let context = run.context as Record<string, unknown>;

  while (currentNodeId) {
    const node = workflow.nodes.find((n) => n.id === currentNodeId);
    if (!node) {
      await prisma.workflowRun.update({
        where: { id: run.id },
        data: { status: "FAILED" },
      });
      await logStep(run.id, currentNodeId, "FAILED", undefined, "Node not found in graph");
      return;
    }

    const config = node.config as Record<string, unknown>;

    if (node.type === "TRIGGER") {
      await logStep(run.id, node.id, "SUCCESS");
      currentNodeId = findNextNodeId(workflow.edges, node.id);
      continue;
    }

    if (node.type === "CONDITION") {
      const result = evaluateCondition(config, context);
      await logStep(run.id, node.id, "SUCCESS", { result });
      currentNodeId = findNextNodeId(workflow.edges, node.id, result ? "true" : "false");
      continue;
    }

    if (node.type === "DELAY") {
      const hours = (config.hours as number) ?? 24;
      const resumeAt = new Date(Date.now() + hours * 60 * 60 * 1000);
      await prisma.workflowRun.update({
        where: { id: run.id },
        data: { status: "WAITING", currentNodeId: node.id, resumeAt, context: context as object },
      });
      await logStep(run.id, node.id, "SUCCESS", { resumeAt });
      // Resume continues from the node AFTER this delay, not this node again.
      const next = findNextNodeId(workflow.edges, node.id);
      await prisma.workflowRun.update({ where: { id: run.id }, data: { currentNodeId: next } });
      return;
    }

    if (node.type === "ACTION_STATIC") {
      try {
        const output = await executeStaticAction(config, context);
        await logStep(run.id, node.id, "SUCCESS", output);
      } catch (err) {
        await prisma.workflowRun.update({ where: { id: run.id }, data: { status: "FAILED" } });
        await logStep(run.id, node.id, "FAILED", undefined, (err as Error).message);
        return;
      }
      currentNodeId = findNextNodeId(workflow.edges, node.id);
      continue;
    }

    if (node.type === "ACTION_AI") {
      const instructions = (config.instructions as string | undefined)?.trim();
      if (!instructions) {
        await prisma.workflowRun.update({ where: { id: run.id }, data: { status: "FAILED" } });
        await logStep(run.id, node.id, "FAILED", undefined, "ACTION_AI node has no instructions configured");
        return;
      }

      // No ai-service deployed: draft with Groq inline and continue to the next node
      if (!process.env.AI_SERVICE_URL && groqChatClient.enabled()) {
        try {
          const output = await draftEmailWithGroq(instructions, context);
          await logStep(run.id, node.id, "SUCCESS", output);
        } catch (err) {
          await prisma.workflowRun.update({ where: { id: run.id }, data: { status: "FAILED" } });
          await logStep(run.id, node.id, "FAILED", undefined, `AI draft failed: ${(err as Error).message}`);
          return;
        }
        currentNodeId = findNextNodeId(workflow.edges, node.id);
        continue;
      }

      try {
        const draftContext = await buildEmailDraftContext(context);
        const job = await aiServiceClient.createEmailDraft({ instructions, context: draftContext });

        await prisma.workflowRun.update({
          where: { id: run.id },
          data: {
            status: "WAITING",
            currentNodeId: node.id, // stay on this node — resolved by the AI poller, not the resume poller
            aiJobId: job.jobId,
            aiPollAttempts: 0,
            resumeAt: new Date(Date.now() + AI_POLL_INTERVAL_MS),
            context: context as object,
          },
        });
        await logStep(run.id, node.id, "SUCCESS", { jobId: job.jobId, status: "dispatched" });
      } catch (err) {
        await prisma.workflowRun.update({ where: { id: run.id }, data: { status: "FAILED" } });
        await logStep(run.id, node.id, "FAILED", undefined, `Failed to dispatch to ai-service: ${(err as Error).message}`);
      }
      return;
    }
  }

  await prisma.workflowRun.update({ where: { id: run.id }, data: { status: "COMPLETED" } });
}

export async function resolveAiNode(run: WorkflowRun, workflow: WorkflowWithGraph): Promise<void> {
  if (!run.aiJobId) return;

  let status;
  try {
    status = await aiServiceClient.getEmailDraftStatus(run.aiJobId);
  } catch (err) {
    // Transient network error talking to ai-service — don't fail the run yet,
    // just let the next poll cycle retry (still bounded by aiPollAttempts below).
    await bumpAiPollAttempt(run, `Poll error: ${(err as Error).message}`);
    return;
  }

  if (status.status === "completed" && status.result) {
    const context = run.context as Record<string, unknown>;
    const organizationId = context.organizationId as string;
    const leadId = context.entityType === "Lead" ? (context.entityId as string) : undefined;
    const dealId = context.entityType === "Deal" ? (context.entityId as string) : undefined;

    // Resolve a recipient address so the eventual "Send" action has
    // somewhere to send to — best-effort, Phase 1 doesn't hard-fail if
    // there isn't one, the Task description still shows the draft either way.
    let toAddress: string | null = null;
    if (leadId) {
      const lead = await prisma.lead.findUnique({ where: { id: leadId }, include: { contact: true } });
      toAddress = lead?.contact?.email ?? null;
    } else if (dealId) {
      const deal = await prisma.deal.findUnique({ where: { id: dealId }, include: { contact: true } });
      toAddress = deal?.contact?.email ?? null;
    }

    const draftMessage = await prisma.message.create({
      data: {
        organizationId,
        leadId,
        dealId,
        channel: "EMAIL",
        direction: "OUTBOUND",
        status: "DRAFT",
        subject: status.result.subject,
        body: status.result.body,
        toAddress,
      },
    });

    // If this ACTION_AI node is configured with a document to attach (e.g.
    // "always attach the pricing sheet to this follow-up"), link it now.
    // The document isn't chosen by the AI — it's a fixed choice a human set
    // when configuring the workflow node, kept deliberately simple for v1.
    const aiNode = workflow.nodes.find((n) => n.id === run.currentNodeId);
    const attachDocumentId = (aiNode?.config as Record<string, unknown> | undefined)?.attachDocumentId as string | undefined;
    if (attachDocumentId) {
      const document = await prisma.document.findFirst({
        where: { id: attachDocumentId, organizationId },
      });
      if (document) {
        await prisma.messageAttachment.create({
          data: { messageId: draftMessage.id, documentId: document.id },
        });
      }
      // If the configured document doesn't exist (deleted since the
      // workflow was set up) or belongs to a different org, this silently
      // skips attaching rather than failing the whole email draft — a
      // missing attachment shouldn't block a otherwise-good draft.
    }

    await prisma.task.create({
      data: {
        organizationId,
        title: `Review AI-drafted email: ${status.result.subject}`,
        description: `To: ${context.entityType} ${context.entityId}\n\nSubject: ${status.result.subject}\n\n${status.result.body}\n\n---\nDrafted by AI (${status.result.revisionCount} revision(s)). Review and send via the linked draft — not auto-sent.`,
        leadId,
        dealId,
        priority: "MEDIUM",
        messageId: draftMessage.id,
      },
    });

    const node = workflow.nodes.find((n) => n.id === run.currentNodeId)!;
    const next = findNextNodeId(workflow.edges, node.id);

    await logStep(run.id, node.id, "SUCCESS", { jobId: run.aiJobId, result: status.result });
    await prisma.workflowRun.update({
      where: { id: run.id },
      data: { status: "RUNNING", currentNodeId: next, aiJobId: null, aiPollAttempts: 0, resumeAt: null },
    });

    const updated = await prisma.workflowRun.findUniqueOrThrow({ where: { id: run.id } });
    await advanceRun(updated, workflow);
    return;
  }

  if (status.status === "failed") {
    await logStep(run.id, run.currentNodeId!, "FAILED", undefined, status.error ?? "ai-service job failed");
    await prisma.workflowRun.update({
      where: { id: run.id },
      data: { status: "FAILED", aiJobId: null, resumeAt: null },
    });
    return;
  }

  // still pending/running
  await bumpAiPollAttempt(run);
}

async function bumpAiPollAttempt(run: WorkflowRun, timeoutError?: string): Promise<void> {
  const attempts = run.aiPollAttempts + 1;
  if (attempts >= AI_MAX_POLL_ATTEMPTS) {
    await logStep(run.id, run.currentNodeId!, "FAILED", undefined, timeoutError ?? "Timed out waiting on ai-service email draft job");
    await prisma.workflowRun.update({
      where: { id: run.id },
      data: { status: "FAILED", aiJobId: null, resumeAt: null },
    });
    return;
  }
  await prisma.workflowRun.update({
    where: { id: run.id },
    data: { aiPollAttempts: attempts, resumeAt: new Date(Date.now() + AI_POLL_INTERVAL_MS) },
  });
}

export async function startRun(workflow: WorkflowWithGraph, entityType: string, entityId: string, seedContext: Record<string, unknown>): Promise<void> {
  const triggerNode = workflow.nodes.find((n) => n.type === "TRIGGER");
  if (!triggerNode) return;

  const run = await prisma.workflowRun.create({
    data: {
      workflowId: workflow.id,
      organizationId: workflow.organizationId,
      entityType,
      entityId,
      currentNodeId: triggerNode.id,
      context: seedContext as object,
    },
  });

  await advanceRun(run, workflow);
}
