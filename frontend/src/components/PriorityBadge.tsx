const CORES: Record<string, string> = {
  A: "priority-a",
  B: "priority-b",
  C: "priority-c",
};

export function PriorityBadge({ prioridade }: { prioridade?: string | null }) {
  if (!prioridade) return null;
  const classe = CORES[prioridade.toUpperCase()] ?? "priority-c";
  return <span className={`priority-badge ${classe}`}>{prioridade.toUpperCase()}</span>;
}
