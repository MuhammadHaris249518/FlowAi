import { prisma } from "../../lib/prisma";
import { automationRepository } from "./automation.repository";
import { startRun } from "./automation.engine";

const BATCH_SIZE = 50;

export async function pollOutboxOnce(): Promise<void> {
  const events = await prisma.outboxEvent.findMany({
    where: { processedAt: null },
    orderBy: { createdAt: "asc" },
    take: BATCH_SIZE,
  });

  for (const event of events) {
    // Claim the event first so a second poller (or an overlapping tick) never runs it twice
    const claimed = await prisma.outboxEvent.updateMany({
      where: { id: event.id, processedAt: null },
      data: { processedAt: new Date() },
    });
    if (claimed.count === 0) continue;

    const workflows = await automationRepository.findActiveWorkflowsForTrigger(
      event.organizationId,
      event.type
    );

    const payload = event.payload as { entityType: string; entityId: string; [key: string]: unknown };

    for (const workflow of workflows) {
      try {
        await startRun(workflow, payload.entityType, payload.entityId, {
          ...payload,
          organizationId: event.organizationId,
          entityType: payload.entityType,
          entityId: payload.entityId,
        });
      } catch (err) {
        // One broken workflow must not stop the others for this event
        console.error(`[outbox-poller] workflow ${workflow.id} failed for event ${event.id}:`, err);
      }
    }
  }
}

export function startOutboxPoller(intervalMs = 5000): NodeJS.Timeout {
  // A run can take seconds (AI drafts): skip a tick instead of overlapping the previous one
  let running = false;
  return setInterval(() => {
    if (running) return;
    running = true;
    pollOutboxOnce()
      .catch((err) => {
        console.error("[outbox-poller] error:", err);
      })
      .finally(() => {
        running = false;
      });
  }, intervalMs);
}
