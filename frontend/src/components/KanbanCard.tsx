import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import type { Lead } from "../api";
import { PriorityBadge } from "./PriorityBadge";

export function KanbanCard({ lead }: { lead: Lead }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: lead.id,
  });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.4 : 1,
  };

  return (
    <div ref={setNodeRef} style={style} {...listeners} {...attributes} className="kanban-card">
      <div className="kanban-card-top">
        <PriorityBadge prioridade={lead.prioridade} />
        <span className="kanban-card-score">{lead.score_total ?? "—"}</span>
      </div>
      <div className="kanban-card-nome">{lead.razao_social}</div>
    </div>
  );
}
