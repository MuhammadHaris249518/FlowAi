"use client";

import type { NodeProps, NodeTypes } from "reactflow";
import { BaseNode } from "./BaseNode";
import { NODE_TYPE_DEFS } from "../../lib/node-defs";
import type { BuilderNodeData } from "../../lib/graph-transform";

function subtitleFor(data: BuilderNodeData): string {
  const c = data.config as Record<string, unknown>;
  switch (data.nodeType) {
    case "TRIGGER":
      return typeof c.trigger === "string" ? c.trigger.replaceAll("_", " ") : "Not configured";
    case "CONDITION":
      return typeof c.field === "string" && typeof c.op === "string"
        ? `${c.field} ${c.op} ${String(c.value ?? "")}`
        : "Not configured";
    case "DELAY":
      return typeof c.hours === "number" ? `${c.hours}h delay` : "Not configured";
    case "ACTION_STATIC":
      return c.action === "CREATE_TASK"
        ? `Create task: ${c.title ?? "untitled"}`
        : c.action === "UPDATE_LEAD_STATUS"
          ? `Set lead status: ${c.status ?? "?"}`
          : c.action === "NOTIFY_OWNER"
            ? `Notify owner: ${c.subject ?? "untitled"}`
            : "Not configured";
    case "ACTION_AI":
      return typeof c.instructions === "string" && c.instructions
        ? `AI email: ${c.instructions.slice(0, 40)}${c.instructions.length > 40 ? "…" : ""}`
        : "Not configured";
  }
}

function WorkflowNode({ data, selected }: NodeProps<BuilderNodeData>) {
  return (
    <BaseNode
      nodeType={data.nodeType}
      title={NODE_TYPE_DEFS[data.nodeType].label}
      subtitle={subtitleFor(data)}
      selected={selected}
    />
  );
}

// Every backend node type maps to the same visual shell — differentiation
// is purely data-driven (icon/color/subtitle), so one component covers all
// five reactflow "types" instead of duplicating five near-identical files.
export const nodeTypes: NodeTypes = {
  TRIGGER: WorkflowNode,
  CONDITION: WorkflowNode,
  DELAY: WorkflowNode,
  ACTION_STATIC: WorkflowNode,
  ACTION_AI: WorkflowNode,
};
