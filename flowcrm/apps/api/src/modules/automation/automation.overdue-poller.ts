import { prisma } from "../../lib/prisma";

// Emits one TASK_OVERDUE outbox event per open task once its due date has passed, so
// "A task becomes overdue" workflows run. The outbox poller does the rest.
const BATCH_SIZE = 100;
const LOOKBACK_DAYS = 30; // ignore tasks that went overdue long ago (no flood on first start)

export async function pollOverdueTasksOnce(): Promise<number> {
  const now = new Date();
  const tasks = await prisma.task.findMany({
    where: {
      completedAt: null,
      dueAt: { lt: now, gt: new Date(now.getTime() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000) },
    },
    orderBy: { dueAt: "asc" },
    take: BATCH_SIZE,
  });

  let emitted = 0;
  for (const task of tasks) {
    const already = await prisma.outboxEvent.findFirst({
      where: { type: "TASK_OVERDUE", payload: { path: ["entityId"], equals: task.id } },
      select: { id: true },
    });
    if (already) continue;
    await prisma.outboxEvent.create({
      data: {
        organizationId: task.organizationId,
        type: "TASK_OVERDUE",
        payload: {
          entityType: "Task",
          entityId: task.id,
          assigneeId: task.assigneeId,
          entity: { title: task.title, priority: task.priority, dueAt: task.dueAt?.toISOString() ?? null },
        },
      },
    });
    emitted++;
  }
  return emitted;
}

export function startOverduePoller(intervalMs = 60_000): NodeJS.Timeout {
  let running = false;
  return setInterval(() => {
    if (running) return;
    running = true;
    pollOverdueTasksOnce()
      .catch((err) => {
        console.error("[overdue-poller] error:", err);
      })
      .finally(() => {
        running = false;
      });
  }, intervalMs);
}
