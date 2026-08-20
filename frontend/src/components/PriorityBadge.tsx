const CORES: Record<string, string> = {
  A: "priority-a",
  B: "priority-b",
  C: "priority-c",
};

export function PriorityBadge({ prioridade, size = "sm" }: { prioridade?: string | null; size?: "sm" | "lg" }) {
  if (!prioridade) return null;
  const classe = CORES[prioridade.toUpperCase()] ?? "priority-c";
  return <span className={`priority-badge ${classe} ${size === "lg" ? "priority-badge-lg" : ""}`}>{prioridade.toUpperCase()}</span>;
}
