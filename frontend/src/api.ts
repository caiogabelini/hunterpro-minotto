const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export interface Lead {
  id: string;
  cnpj: string;
  razao_social: string;
  nome_fantasia?: string | null;
  municipio?: string | null;
  uf?: string | null;
  whatsapp_ativo?: boolean | null;
  email?: string | null;
  email_validado?: boolean | null;
  decisor_nome?: string | null;
  rqe_confirmado?: boolean | null;
  divida_ativa_qtd_inscricoes?: number | null;
  zona_franca?: boolean | null;
  score_total?: number | null;
  prioridade?: string | null;
  created_at: string;
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

export async function fetchLeads(): Promise<Lead[]> {
  const res = await fetch(`${API_URL}/api/leads`);
  if (!res.ok) throw new Error(`Erro ao buscar leads: ${res.status}`);
  return res.json();
}
