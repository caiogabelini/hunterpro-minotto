/**
 * Base das chamadas à API.
 *
 * Em PRODUÇÃO o padrão é string vazia, ou seja, URLs RELATIVAS
 * (`/api/leads`). O nginx que serve o frontend faz proxy de `/api/` (e
 * de `/health`) pro App Service do backend dentro da rede interna do
 * EasyPanel -- ver frontend/nginx.conf.template. Como o navegador vê
 * front e API na mesma origem, não há requisição cross-origin e o CORS
 * deixa de ser um problema em produção.
 *
 * Em DESENVOLVIMENTO o padrão continua sendo http://localhost:8000,
 * porque o dev server do Vite não tem proxy configurado e o uvicorn
 * roda numa porta separada.
 *
 * O `import.meta.env.PROD` no meio existe pra que o comportamento certo
 * seja o PADRÃO nos dois ambientes, sem depender de ninguém lembrar de
 * definir a variável. Antes o fallback era localhost em qualquer caso:
 * bastava `VITE_API_URL` não estar definida no build de produção pra
 * app inteira tentar falar com a máquina de quem abriu o navegador.
 *
 * `VITE_API_URL` continua sobrescrevendo os dois casos -- necessário só
 * se o backend for servido em outro domínio. Atenção: variável do Vite
 * é assada no bundle em tempo de BUILD, não lida em runtime; defini-la
 * no painel do EasyPanel depois do build não tem efeito nenhum.
 */
const API_URL = import.meta.env.VITE_API_URL ?? (import.meta.env.PROD ? "" : "http://localhost:8000");

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

/** Mesmo shape do dict devolvido por
 * app/services/ai_enrichment.py::gerar_insights_estrategicos e
 * persistido em Lead.insights_ia. `potencial_oportunidade` chega já
 * normalizado (lowercase/trim) pelo backend, mas NÃO é forçado a um dos
 * 3 valores esperados -- um valor fora de "alto"/"médio"/"baixo" só cai
 * num badge neutro no frontend (ver POTENCIAL_LABELS em
 * LeadDossierPage.tsx), nunca quebra a tela. */
export interface InsightsIA {
  resumo_estrategico: string;
  potencial_oportunidade: string;
  recomendacao_abordagem: string[];
  estrategia_comunicacao: string;
  cta_sugerido: string;
}

/** Mesmo shape de LeadMessageRead em app/schemas/lead_message.py --
 * uma mensagem de abordagem gerada, já persistida (uma linha de
 * histórico por canal em LeadMessage). `canal` tipado como união
 * literal porque é o mesmo conjunto fechado de CanalMensagem no
 * backend (app/models/lead_message.py) -- Instagram de propósito fora. */
export type CanalAbordagem = "email" | "whatsapp";

export interface LeadMessage {
  id: string;
  lead_id: string;
  canal: CanalAbordagem;
  conteudo: string;
  assunto?: string | null;
  gerado_em: string;
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
  // Demais e-mails que o fallback `domain-search` do Hunter trouxe na
  // mesma resposta (ver CLAUDE.md). Só existe quando
  // HUNTER_DOMAIN_SEARCH_FALLBACK estava ligada na busca -- desligada
  // por padrão, então na maioria dos leads vem null. Lido via
  // `getEmailsSecundarios` (defensivo), nunca direto.
  emails_secundarios?: unknown;
  decisor_nome?: string | null;
  site?: string | null;
  // O Firecrawl conseguiu ler o site? null = etapa nunca rodou (sem
  // site), true = leu, false = tentou e não leu. A distinção entre os
  // dois últimos é o que permite ao dossiê dizer "não conseguimos ler"
  // em vez de mostrar campos vazios sem explicação (ver CLAUDE.md).
  site_scrape_sucesso?: boolean | null;
  instagram?: string | null;
  linkedin_empresa?: string | null;
  linkedin_decisor?: string | null;
  google_rating?: number | null;
  google_avaliacoes?: number | null;
  servicos?: string | null;
  rqe_confirmado?: boolean | null;
  rqe_fonte?: string | null;
  cnes_codigo?: string | null;
  divida_ativa_qtd_inscricoes?: number | null;
  divida_ativa_valor_total?: number | null;
  divida_ativa_ajuizado?: boolean | null;
  zona_franca?: boolean | null;
  score_total?: number | null;
  score_detalhes?: ScoreDetalhes | null;
  prioridade?: string | null;
  kanban_status: string;
  motivo_perda?: string | null;
  servicos_vendidos?: string[] | null;
  tipo_contrato?: string | null;
  valor_fechamento?: number | null;
  mensagem_abordagem?: string | null;
  insights_ia?: InsightsIA | null;
  insights_gerado_em?: string | null;
  created_at: string;
  updated_at: string;
}

/** Mesmo shape de DashboardSummary em app/schemas/dashboard.py. */
export interface DashboardSummary {
  leads_no_mes: number;
  leads_no_mes_limite: number;
  score_medio: number;
  leads_em_negociacao: number;
  taxa_conversao: number;
  receita_fechada_pontual: number;
  receita_fechada_recorrente_mensal: number;
  receita_fechada_total: number;
}

/** Mesmo shape de AcaoRecomendada em app/schemas/dashboard.py.
 * `kanban_status_filtro`/`filtro_chave` ainda não são usados pra filtrar
 * de verdade o Kanban no frontend (ver DashboardPage.tsx) -- só
 * persistidos aqui pra quando essa ligação for implementada. */
export interface AcaoRecomendada {
  titulo: string;
  quantidade: number;
  kanban_status_filtro?: string | null;
  filtro_chave: string;
}

/** Mesmo shape de DashboardPremissas em app/schemas/dashboard.py --
 * premissas do Simulador de Receita, por usuário. */
export interface DashboardPremissas {
  leads_qualificados: number;
  taxa_fechamento: number;
  ticket_medio: number;
}

/** Mesmo shape de FunilEtapa em app/schemas/dashboard.py.
 * ⚠️ `percentual` é uma FOTOGRAFIA da distribuição atual dos leads por
 * etapa, não uma taxa de conversão real de coorte -- este projeto não
 * tem histórico de transição de status, só o `kanban_status` atual de
 * cada lead. Ver a mesma ressalva em DashboardPage.tsx (nota exibida
 * abaixo do funil) e no docstring de `get_funil` no backend. */
export interface FunilEtapa {
  status: string;
  label: string;
  quantidade: number;
  percentual: number;
}

/** Mesmo shape de MotivoPerda em app/schemas/dashboard.py -- agrupado
 * por texto exato de `motivo_perda`, sem normalização semântica. */
export interface MotivoPerda {
  motivo: string;
  quantidade: number;
}

/** Mesmo shape de BuscaLeadsRead em app/schemas/busca_leads.py -- um
 * registro de execução do painel admin de "busca mensal" (ver
 * pages/BuscaLeadsPage.tsx). `status` é `"executando" | "concluido" |
 * "erro"` (mesmos valores de StatusBusca no backend), tipado como
 * `string` aqui (não união literal) pelo mesmo motivo de `Lead.kanban_status`
 * já ser `string` solto neste arquivo -- simplicidade, sem necessidade
 * de um union type estrito pra um campo só comparado por igualdade. */
export interface BuscaLeadsRegistro {
  id: string;
  iniciado_por_id: string;
  iniciado_em: string;
  concluido_em?: string | null;
  status: string;
  total_cnpjs_encontrados?: number | null;
  /** Quantos CNPJs passaram na pré-seleção e entraram no enriquecimento
   * pago (top N por score preliminar). Fica entre `encontrados` (o
   * universo) e `processados`. `null` em buscas anteriores a 22/08/2026,
   * quando a pré-seleção não existia. */
  total_cnpjs_selecionados?: number | null;
  total_leads_processados?: number | null;
  erros?: string[] | null;
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

/** Lançada quando o backend devolve 409 -- hoje só `dispararBusca`
 * (POST /api/admin/buscas), quando já existe uma BuscaLeads com
 * status "executando". `message` já vem pronta do `detail` do backend
 * ("Já existe uma busca em andamento, iniciada em ...") -- quem chama
 * mostra direto, não precisa montar uma mensagem própria. */
export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
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

/** Mesmo shape de LeadListaResponse em app/schemas/lead.py -- resposta
 * paginada de GET /api/leads/lista (tela "Lista de Leads"). `items` é a
 * mesma forma de `Lead` já usada em todo o resto do app -- o backend
 * devolve o `LeadRead` completo por item, não um subconjunto próprio
 * pra essa tela. */
export interface LeadListaResposta {
  items: Lead[];
  total: number;
  pagina: number;
  por_pagina: number;
}

export interface LeadListaParametros {
  busca?: string;
  prioridade?: string;
  kanban_status?: string;
  ordenar_por?: "score_total" | "created_at";
  ordem?: "asc" | "desc";
  pagina?: number;
  por_pagina?: number;
}

export async function fetchLeadsLista(token: string, parametros: LeadListaParametros): Promise<LeadListaResposta> {
  const query = new URLSearchParams();
  if (parametros.busca) query.set("busca", parametros.busca);
  if (parametros.prioridade) query.set("prioridade", parametros.prioridade);
  if (parametros.kanban_status) query.set("kanban_status", parametros.kanban_status);
  if (parametros.ordenar_por) query.set("ordenar_por", parametros.ordenar_por);
  if (parametros.ordem) query.set("ordem", parametros.ordem);
  if (parametros.pagina) query.set("pagina", String(parametros.pagina));
  if (parametros.por_pagina) query.set("por_pagina", String(parametros.por_pagina));

  const res = await fetch(`${API_URL}/api/leads/lista?${query.toString()}`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchLead(token: string, leadId: string): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchMensagens(token: string, leadId: string): Promise<LeadMessage[]> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/mensagens`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function gerarAbordagemCanal(token: string, leadId: string, canal: CanalAbordagem): Promise<LeadMessage> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/gerar-abordagem/${canal}`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function gerarInsights(token: string, leadId: string): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/gerar-insights`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchDashboardSummary(token: string): Promise<DashboardSummary> {
  const res = await fetch(`${API_URL}/api/dashboard/summary`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchAcoesRecomendadas(token: string): Promise<AcaoRecomendada[]> {
  const res = await fetch(`${API_URL}/api/dashboard/acoes-recomendadas`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchPremissas(token: string): Promise<DashboardPremissas> {
  const res = await fetch(`${API_URL}/api/dashboard/premissas`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function salvarPremissas(token: string, premissas: DashboardPremissas): Promise<DashboardPremissas> {
  const res = await fetch(`${API_URL}/api/dashboard/premissas`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(premissas),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchFunil(token: string): Promise<FunilEtapa[]> {
  const res = await fetch(`${API_URL}/api/dashboard/funil`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchMotivosPerda(token: string): Promise<MotivoPerda[]> {
  const res = await fetch(`${API_URL}/api/dashboard/motivos-perda`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function dispararBusca(token: string): Promise<BuscaLeadsRegistro> {
  const res = await fetch(`${API_URL}/api/admin/buscas`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (res.status === 409) throw new ConflictError(await parseErrorDetail(res));
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchBuscas(token: string): Promise<BuscaLeadsRegistro[]> {
  const res = await fetch(`${API_URL}/api/admin/buscas`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function fetchBusca(token: string, buscaId: string): Promise<BuscaLeadsRegistro> {
  const res = await fetch(`${API_URL}/api/admin/buscas/${buscaId}`, { headers: authHeaders(token) });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

/** Mesmos 3 campos que `PATCH /{lead_id}/status` exige quando
 * `kanban_status == "ganho"` (ver app/schemas/lead.py::LeadStatusUpdate
 * e o docstring da rota) -- espelha `motivo_perda` do lado positivo. */
export interface DadosFechamento {
  servicos_vendidos: string[];
  tipo_contrato: "pontual" | "recorrente";
  valor_fechamento: number;
}

export async function updateLeadStatus(
  token: string,
  leadId: string,
  kanban_status: string,
  motivo_perda?: string,
  dadosFechamento?: DadosFechamento,
): Promise<Lead> {
  const res = await fetch(`${API_URL}/api/leads/${leadId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ kanban_status, motivo_perda, ...dadosFechamento }),
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}
