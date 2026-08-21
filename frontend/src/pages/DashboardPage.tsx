import {
  ArrowRight,
  Calculator,
  FileSearch,
  Funnel,
  Gauge,
  Handshake,
  Landmark,
  ListChecks,
  Loader2,
  Package,
  PartyPopper,
  Percent,
  Target,
  ThumbsDown,
  UserCheck,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchAcoesRecomendadas,
  fetchDashboardSummary,
  fetchFunil,
  fetchMotivosPerda,
  fetchPremissas,
  salvarPremissas,
  UnauthorizedError,
  type AcaoRecomendada,
  type DashboardPremissas,
  type DashboardSummary,
  type FunilEtapa,
  type MotivoPerda,
} from "../api";
import { useAuth } from "../context/AuthContext";
import { formatCurrencyBRL } from "../format";
import "./DashboardPage.css";

/** Ícone por `filtro_chave` (ver app/schemas/dashboard.py::AcaoRecomendada)
 * -- uma entrada por categoria hoje implementada no backend, mais um
 * fallback genérico pra uma categoria nova que apareça sem entrada aqui
 * (nunca quebra por falta de ícone mapeado). `divida_pgfn_nao_abordada`
 * reaproveita o mesmo `Landmark` já usado na seção de dívida ativa PGFN
 * do dossiê -- mesma linguagem de ícone pro mesmo assunto. */
const ACAO_ICONES: Record<string, LucideIcon> = {
  decisor_identificado: UserCheck,
  prioridade_a: Target,
  revisao_manual: FileSearch,
  divida_pgfn_nao_abordada: Landmark,
};
const ACAO_ICONE_PADRAO = ListChecks;

/** Premissas em edição no formulário -- inputs numéricos HTML sempre
 * mandam string em `onChange`, então o form guarda os 3 campos já como
 * `number` (convertidos na hora, `|| 0` cobre o caso de campo vazio/
 * inválido) pra poder calcular a receita estimada a cada tecla sem
 * parsear de novo em outro lugar. */
type PremissasForm = DashboardPremissas;

const PREMISSAS_PADRAO: PremissasForm = { leads_qualificados: 0, taxa_fechamento: 20, ticket_medio: 1500 };

export function DashboardPage() {
  const { token, logout } = useAuth();

  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [acoes, setAcoes] = useState<AcaoRecomendada[]>([]);
  const [funil, setFunil] = useState<FunilEtapa[]>([]);
  const [motivosPerda, setMotivosPerda] = useState<MotivoPerda[]>([]);
  const [form, setForm] = useState<PremissasForm>(PREMISSAS_PADRAO);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  const [salvando, setSalvando] = useState(false);
  const [erroSalvar, setErroSalvar] = useState<string | null>(null);
  const [salvoAgora, setSalvoAgora] = useState(false);

  useEffect(() => {
    if (!token) return;
    Promise.all([
      fetchDashboardSummary(token),
      fetchAcoesRecomendadas(token),
      fetchPremissas(token),
      fetchFunil(token),
      fetchMotivosPerda(token),
    ])
      .then(([summaryData, acoesData, premissasData, funilData, motivosPerdaData]) => {
        setSummary(summaryData);
        setAcoes(acoesData);
        setForm(premissasData);
        setFunil(funilData);
        setMotivosPerda(motivosPerdaData);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErro(String(e));
      })
      .finally(() => setCarregando(false));
  }, [token, logout]);

  async function salvar() {
    if (!token) return;
    setSalvando(true);
    setErroSalvar(null);
    setSalvoAgora(false);
    try {
      const salvo = await salvarPremissas(token, form);
      setForm(salvo);
      setSalvoAgora(true);
      setTimeout(() => setSalvoAgora(false), 2000);
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErroSalvar("Não foi possível salvar as premissas agora. Tente novamente.");
    } finally {
      setSalvando(false);
    }
  }

  const receitaEstimada = form.leads_qualificados * (form.taxa_fechamento / 100) * form.ticket_medio;

  return (
    <div className="dashboard-page">
      <header className="dashboard-header">
        <div className="dashboard-eyebrow">HunterPro</div>
        <h1 className="dashboard-title">Dashboard</h1>
      </header>

      {carregando && <p className="dashboard-status">Carregando...</p>}
      {erro && <p className="dashboard-status dashboard-erro">{erro}</p>}

      {summary && (
        <>
          <SecaoResumoExecutivo summary={summary} />
          <SecaoConsumoPlano summary={summary} />
          <SecaoFunil funil={funil} />
          <SecaoMotivosPerda motivos={motivosPerda} />
          <SecaoAcoesRecomendadas acoes={acoes} />
          <SecaoSimulador
            form={form}
            onChange={setForm}
            receitaEstimada={receitaEstimada}
            salvando={salvando}
            salvoAgora={salvoAgora}
            erroSalvar={erroSalvar}
            onSalvar={salvar}
          />
        </>
      )}
    </div>
  );
}

// --- Resumo Executivo ----------------------------------------------------

function SecaoResumoExecutivo({ summary }: { summary: DashboardSummary }) {
  return (
    <section className="dashboard-section">
      <SectionHeading icon={Gauge}>Resumo executivo</SectionHeading>
      <div className="dashboard-kpi-grid">
        <KpiCard icon={Gauge} label="Score médio" valor={summary.score_medio} sufixo="/100" />
        <KpiCard icon={Handshake} label="Leads em negociação" valor={summary.leads_em_negociacao} />
        <KpiCard icon={Percent} label="Taxa de conversão" valor={summary.taxa_conversao} sufixo="%" />
      </div>
    </section>
  );
}

function KpiCard({ icon: Icon, label, valor, sufixo }: { icon: LucideIcon; label: string; valor: number; sufixo?: string }) {
  return (
    <div className="dashboard-kpi-card">
      <div className="dashboard-kpi-icone">
        <Icon size={20} />
      </div>
      <div className="dashboard-kpi-valor">
        {valor}
        {sufixo && <span className="dashboard-kpi-sufixo">{sufixo}</span>}
      </div>
      <div className="dashboard-kpi-label">{label}</div>
    </div>
  );
}

// --- Consumo do Plano ------------------------------------------------------
// Barra de progresso: preenchimento normal em --color-primary; a partir
// de 90% do limite, vira --color-alert (laranja) -- pedido explícito
// oferecia "laranja/vermelho" como exemplos de UMA cor de alerta, não
// dois patamares distintos; laranja foi o escolhido, deixando o
// vermelho (--color-danger) reservado só pra estados de erro/perigo já
// estabelecidos no resto do app (ex.: PGFN ajuizado). Largura da barra
// nunca passa de 100% visualmente mesmo se o consumo ultrapassar o
// limite (`Math.min`), mas o número/texto ao lado sempre mostra a
// contagem real.

function SecaoConsumoPlano({ summary }: { summary: DashboardSummary }) {
  const percentual = summary.leads_no_mes_limite > 0 ? (summary.leads_no_mes / summary.leads_no_mes_limite) * 100 : 0;
  const emAlerta = percentual >= 90;

  return (
    <section className="dashboard-section">
      <SectionHeading icon={Package}>Consumo do plano</SectionHeading>
      <div className="dashboard-card dashboard-plano-card">
        <div className="dashboard-plano-topo">
          <span className="dashboard-plano-contagem">
            {summary.leads_no_mes} / {summary.leads_no_mes_limite} leads este mês
          </span>
          <span className={`dashboard-plano-percentual ${emAlerta ? "dashboard-plano-percentual-alerta" : ""}`}>
            {Math.round(percentual)}%
          </span>
        </div>
        <div className="dashboard-progress-track">
          <div
            className={`dashboard-progress-fill ${emAlerta ? "dashboard-progress-fill-alerta" : ""}`}
            style={{ width: `${Math.min(percentual, 100)}%` }}
          />
        </div>
        {emAlerta && (
          <p className="dashboard-plano-aviso">
            {percentual >= 100
              ? "Limite do plano atingido este mês."
              : "Perto de atingir o limite do plano este mês."}
          </p>
        )}
      </div>
    </section>
  );
}

// --- Funil de Conversão ------------------------------------------------------
// Barras horizontais, largura proporcional ao `percentual` de cada etapa
// (0-100%, sobre o total de leads NÃO perdidos -- ver a mesma ressalva
// abaixo do funil e o docstring de get_funil no backend). Preenchimento
// em --color-primary pra todas as etapas, exceto "ganho", que usa
// --color-success -- judgment call pra marcar visualmente a etapa de
// desfecho positivo do funil, mesma lógica semântica (verde = sucesso)
// já usada no resto do dashboard/dossiê, não pedido explicitamente mas
// consistente com o vocabulário de cor já estabelecido.

function SecaoFunil({ funil }: { funil: FunilEtapa[] }) {
  return (
    <section className="dashboard-section">
      <SectionHeading icon={Funnel}>Funil de conversão</SectionHeading>
      <div className="dashboard-card">
        <div className="dashboard-funil-lista">
          {funil.map((etapa) => (
            <div key={etapa.status} className="dashboard-funil-etapa">
              <span className="dashboard-funil-label">{etapa.label}</span>
              <div className="dashboard-funil-barra-track">
                <div
                  className={`dashboard-funil-barra-fill ${etapa.status === "ganho" ? "dashboard-funil-barra-fill-ganho" : ""}`}
                  style={{ width: `${etapa.percentual}%` }}
                />
              </div>
              <span className="dashboard-funil-numeros">
                {etapa.quantidade} · {etapa.percentual}%
              </span>
            </div>
          ))}
        </div>
        <p className="dashboard-funil-nota">
          Esta é a distribuição atual dos leads por etapa, não uma taxa de conversão histórica -- o sistema ainda não
          guarda um histórico de quando cada lead mudou de etapa, só o status de agora.
        </p>
      </div>
    </section>
  );
}

// --- Motivos de Perda ------------------------------------------------------
// Mesmo padrão visual de barra horizontal do funil, mas em --color-danger
// (perda = semântica de "negativo", mesma cor já usada em outros lugares
// do app) e a largura é relativa ao motivo MAIS frequente da própria
// lista (não a um total geral) -- não faz sentido comparar contra o
// total de leads perdidos aqui, o que importa é o ranking relativo entre
// os motivos.

function SecaoMotivosPerda({ motivos }: { motivos: MotivoPerda[] }) {
  const maiorQuantidade = Math.max(1, ...motivos.map((m) => m.quantidade));

  return (
    <section className="dashboard-section">
      <SectionHeading icon={ThumbsDown}>Motivos de perda</SectionHeading>
      {motivos.length === 0 ? (
        <div className="dashboard-card dashboard-perda-vazio">
          <PartyPopper size={20} />
          <p className="dashboard-perda-vazio-texto">Nenhum lead perdido registrado ainda — ótimo sinal!</p>
        </div>
      ) : (
        <div className="dashboard-card">
          <div className="dashboard-perda-lista">
            {motivos.map((item, indice) => (
              <div key={item.motivo} className="dashboard-perda-item">
                <span className="dashboard-perda-posicao">{indice + 1}</span>
                <div className="dashboard-perda-corpo">
                  <div className="dashboard-perda-topo">
                    <span className="dashboard-perda-motivo">{item.motivo}</span>
                    <span className="dashboard-perda-quantidade">{item.quantidade}</span>
                  </div>
                  <div className="dashboard-perda-barra-track">
                    <div
                      className="dashboard-perda-barra-fill"
                      style={{ width: `${(item.quantidade / maiorQuantidade) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// --- Ações Recomendadas ----------------------------------------------------
// O link "Ver leads →" navega pro Kanban (rota "/", não existe uma rota
// "/kanban" separada nesta app -- ver App.tsx) SEM aplicar filtro real
// ainda -- simplificação aceita explicitamente pra esta fase.
// `kanban_status_filtro`/`filtro_chave` já vêm da API prontos pra quando
// uma versão futura ligar o link a um filtro de verdade no Kanban (ver
// README "O que falta").

function SecaoAcoesRecomendadas({ acoes }: { acoes: AcaoRecomendada[] }) {
  return (
    <section className="dashboard-section">
      <SectionHeading icon={ListChecks}>Ações recomendadas</SectionHeading>
      {acoes.length === 0 ? (
        <div className="dashboard-card">
          <p className="dashboard-muted">Nenhuma ação recomendada no momento -- tudo em dia por aqui.</p>
        </div>
      ) : (
        <div className="dashboard-acoes-lista">
          {acoes.map((acao) => {
            const Icon = ACAO_ICONES[acao.filtro_chave] ?? ACAO_ICONE_PADRAO;
            return (
              <div key={acao.filtro_chave} className="dashboard-acao-card">
                <div className="dashboard-acao-icone">
                  <Icon size={18} />
                </div>
                <div className="dashboard-acao-corpo">
                  <div className="dashboard-acao-titulo">{acao.titulo}</div>
                  <div className="dashboard-acao-quantidade">
                    {acao.quantidade} lead{acao.quantidade === 1 ? "" : "s"}
                  </div>
                </div>
                <Link to="/" className="dashboard-acao-link">
                  <span>Ver leads</span>
                  <ArrowRight size={14} />
                </Link>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// --- Simulador de Receita ---------------------------------------------------

function SecaoSimulador({
  form,
  onChange,
  receitaEstimada,
  salvando,
  salvoAgora,
  erroSalvar,
  onSalvar,
}: {
  form: PremissasForm;
  onChange: (form: PremissasForm) => void;
  receitaEstimada: number;
  salvando: boolean;
  salvoAgora: boolean;
  erroSalvar: string | null;
  onSalvar: () => void;
}) {
  function atualizarCampo(campo: keyof PremissasForm, valor: string) {
    const numero = Number(valor);
    onChange({ ...form, [campo]: Number.isFinite(numero) ? numero : 0 });
  }

  return (
    <section className="dashboard-section">
      <SectionHeading icon={Calculator}>Simulador de receita</SectionHeading>
      <div className="dashboard-card">
        <div className="dashboard-simulador-grid">
          <label className="dashboard-simulador-campo">
            <span>Leads qualificados/mês</span>
            <input
              type="number"
              min={0}
              step={1}
              value={form.leads_qualificados}
              onChange={(e) => atualizarCampo("leads_qualificados", e.target.value)}
            />
          </label>
          <label className="dashboard-simulador-campo">
            <span>Taxa de fechamento (%)</span>
            <input
              type="number"
              min={0}
              max={100}
              step={0.1}
              value={form.taxa_fechamento}
              onChange={(e) => atualizarCampo("taxa_fechamento", e.target.value)}
            />
          </label>
          <label className="dashboard-simulador-campo">
            <span>Ticket médio (R$)</span>
            <input
              type="number"
              min={0}
              step={50}
              value={form.ticket_medio}
              onChange={(e) => atualizarCampo("ticket_medio", e.target.value)}
            />
          </label>
        </div>

        <div className="dashboard-simulador-resultado">
          <span className="dashboard-simulador-resultado-label">Receita estimada / mês</span>
          <span className="dashboard-simulador-resultado-valor">{formatCurrencyBRL(receitaEstimada)}</span>
        </div>

        <div className="dashboard-simulador-acoes">
          <button
            type="button"
            className="dashboard-simulador-btn"
            onClick={onSalvar}
            disabled={salvando}
            aria-busy={salvando}
          >
            {salvando ? <Loader2 size={15} className="spin" aria-label="Salvando..." /> : "Salvar premissas"}
          </button>
          {salvoAgora && <span className="dashboard-simulador-sucesso">Premissas salvas.</span>}
          {erroSalvar && <span className="dashboard-simulador-erro">{erroSalvar}</span>}
        </div>
      </div>
    </section>
  );
}

// --- Helpers compartilhados -------------------------------------------------

function SectionHeading({ icon: Icon, children }: { icon: LucideIcon; children: string }) {
  return (
    <h2 className="dashboard-section-heading">
      <Icon size={16} />
      <span>{children}</span>
    </h2>
  );
}
