const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Um critério já pontuado pra um lead -- mesmo shape de
 * CriterionScoreDetail em app/scoring/compute_lead_score.py, serializado
 * dentro de Lead.score_detalhes.breakdown pela task compute_lead_score. */
export interface ScoreBreakdownItem {
  key: string;
  label: string;
  weight: number;
  layer: "estruturado" | "inferencia" | "validacao";
  points: number;
}

export interface ScoreDetalhes {
  breakdown: ScoreBreakdownItem[];
}

export interface Lead {
  id: string;
  cnpj: string;
  razao_social: string;
  nome_fantasia?: string | null;
  cnae_principal?: string | null;
  municipio?: string | null;
  uf?: string | null;
  anos_mercado?: number | null;
  simples_nacional?: boolean | null;
  situacao_cadastral?: string | null;
  telefone?: string | null;
  whatsapp_ativo?: boolean | null;
  email?: string | null;
  email_validado?: boolean | null;
  decisor_nome?: string | null;
  site?: string | null;
  instagram?: string | null;
  linkedin_empresa?: string | null;
  linkedin_decisor?: string | null;
  google_rating?: number | null;
  google_avaliacoes?: number | null;
  servicos?: string | null;
  rqe_confirmado?: boolean | null;
  divida_ativa_qtd_inscricoes?: number | null;
  zona_franca?: boolean | null;
  score_total?: number | null;
  score_detalhes?: ScoreDetalhes | null;
  prioridade?: string | null;
  kanban_status: string;
  motivo_perda?: string | null;
  created_at: string;
}

/** Lançada quando o backend devolve 401 -- o token guardado em memória
 * (ver AuthContext) expirou ou é inválido. Quem chama decide o que fazer
 * (tipicamente: deslogar e redirecionar pro /login). */
export class UnauthorizedError extends Error {
  constructor() {
    super("Sessão expirada ou token inválido");
    this.name = "UnauthorizedError";
  }
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const corpo = await res.json();
    if (typeof corpo.detail === "string") return corpo.detail;
    return JSON.stringify(corpo.detail);
  } catch {
    return `Erro ${res.status}`;
  }
}

export async function checkHealth(): Promise<{ status: string } | null> {
  try {
    const res = await fetch(`${API_URL}/health`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function login(email: string, senha: string): Promise<string> {
  const res = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, senha }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  const corpo = await res.json();
  return corpo.access_token as string;
}

export async function fetchLeads(token: string): Promise<Lead[]> {
  const res = await fetch(`${API_URL}/api/leads`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(`Erro ao buscar leads: ${res.status}`);
  return res.json();
}

export async function fetchLead(token: string, leadId: string): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function updateLeadStatus(
  token: string,
  leadId: string,
  kanban_status: string,
  motivo_perda?: string,
): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ kanban_status, motivo_perda }),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}
