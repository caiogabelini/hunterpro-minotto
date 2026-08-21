// Espelha app/models/lead.py::KanbanStatus no backend -- ordem de funil
// fixa, não alfabética. Se um valor novo for adicionado lá, adicionar
// aqui também (a validação de valor aceito é feita só no backend).
export const KANBAN_COLUMNS: { status: string; label: string }[] = [
  { status: "novo_lead", label: "Novo Lead" },
  { status: "qualificacao", label: "Qualificação" },
  { status: "contatado", label: "Contatado" },
  { status: "respondeu", label: "Respondeu" },
  { status: "reuniao", label: "Reunião" },
  { status: "proposta_enviada", label: "Proposta Enviada" },
  { status: "negociacao", label: "Negociação" },
  { status: "ganho", label: "Ganho" },
  { status: "perdido", label: "Perdido" },
];

export const STATUS_PERDIDO = "perdido";
export const STATUS_GANHO = "ganho";
