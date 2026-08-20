import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  ExternalLink,
  FileText,
  Gauge,
  Globe,
  Landmark,
  Lightbulb,
  Link2,
  Loader2,
  Mail,
  MessageCircle,
  MessageSquare,
  Phone,
  Send,
  ShieldCheck,
  Sparkles,
  Target,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  fetchLead,
  fetchMensagens,
  gerarAbordagemCanal,
  gerarInsights,
  UnauthorizedError,
  type CanalAbordagem,
  type InsightsIA,
  type Lead,
  type LeadMessage,
} from "../api";
import { PriorityBadge } from "../components/PriorityBadge";
import { useAuth } from "../context/AuthContext";
import { formatarNumeroWhatsapp, formatCurrencyBRL, formatDate, formatRelative } from "../format";
import { getInsights } from "../insights";
import { KANBAN_COLUMNS, STATUS_PERDIDO } from "../kanbanStatuses";
import { getScoreBreakdown } from "../leadScore";
import { SIGNAL_LAYER_LABELS } from "../scoreLayers";
import "./LeadDossierPage.css";

type Aba = "dados" | "contatos" | "analise" | "mensagens" | "insights";

const ABAS: { id: Aba; label: string; icon: LucideIcon }[] = [
  { id: "dados", label: "Dados", icon: FileText },
  { id: "contatos", label: "Contatos", icon: Users },
  { id: "analise", label: "Análise IA", icon: Gauge },
  { id: "mensagens", label: "Mensagens", icon: MessageSquare },
  { id: "insights", label: "Insights", icon: Lightbulb },
];

export function LeadDossierPage() {
  const { id } = useParams<{ id: string }>();
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [lead, setLead] = useState<Lead | null>(null);
  const [mensagens, setMensagens] = useState<LeadMessage[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [abaAtiva, setAbaAtiva] = useState<Aba>("dados");

  useEffect(() => {
    if (!token || !id) return;
    Promise.all([fetchLead(token, id), fetchMensagens(token, id)])
      .then(([leadData, mensagensData]) => {
        setLead(leadData);
        setMensagens(mensagensData);
      })
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErro(String(e));
      })
      .finally(() => setCarregando(false));
  }, [token, id, logout]);

  function registrarNovaMensagem(mensagem: LeadMessage) {
    setMensagens((atuais) => [mensagem, ...atuais.filter((m) => m.canal !== mensagem.canal)]);
  }

  return (
    <div className="dossier-page">
      <button className="dossier-voltar" onClick={() => navigate("/")}>
        <ArrowLeft size={15} />
        <span>Voltar pro Kanban</span>
      </button>

      {carregando && <p className="dossier-status">Carregando...</p>}
      {erro && <p className="dossier-status dossier-erro">{erro}</p>}

      {lead && (
        <>
          <DossierHeader lead={lead} />

          <AbaNav ativa={abaAtiva} onChange={setAbaAtiva} />

          {abaAtiva === "dados" && <AbaDados lead={lead} />}

          {abaAtiva === "contatos" && <AbaContatos lead={lead} />}

          {abaAtiva === "analise" && (
            <AbaAnalise scoreDetalhes={lead.score_detalhes} />
          )}

          {abaAtiva === "mensagens" && (
            <SecaoAbordagem
              leadId={lead.id}
              email={lead.email}
              temWhatsapp={!!lead.whatsapp_ativo}
              telefone={lead.telefone}
              mensagens={mensagens}
              onGerada={registrarNovaMensagem}
            />
          )}

          {abaAtiva === "insights" && (
            <AbaInsights
              leadId={lead.id}
              insights={getInsights(lead.insights_ia)}
              geradoEm={lead.insights_gerado_em}
              onGerado={setLead}
            />
          )}
        </>
      )}
    </div>
  );
}

// --- Cabeçalho (fixo, fora das abas) ---------------------------------------

// Mapeamento simples A/B/C -> rótulo de classificação, derivado da
// `prioridade` que já existe no lead (nenhum campo novo). "Excelente"/
// "Bom"/"Regular" são só um rótulo mais legível pro mesmo valor que o
// badge de prioridade já mostra.
const CLASSIFICACAO_LABELS: Record<string, string> = {
  A: "Excelente",
  B: "Bom",
  C: "Regular",
};

// Nicho é fixo pra todo lead deste projeto (consultórios/clínicas com
// RQE, ver CLAUDE.md) -- não há campo de especialidade estruturado no
// Lead pra derivar um texto mais específico, então não inventamos um.
const NICHO_FIXO = "Clínica Médica";

/** WhatsApp antes de e-mail -- mesma preferência já assumida no resto
 * do dossiê (a ação rápida de WhatsApp aparece antes da de e-mail na
 * seção de Contato). Só considera canal *confirmado* (whatsapp_ativo/
 * email_validado), não a mera presença do dado. */
function canalPreferido(lead: Lead): string {
  if (lead.whatsapp_ativo) return "WhatsApp";
  if (lead.email_validado) return "E-mail";
  return "—";
}

function DossierHeader({ lead }: { lead: Lead }) {
  const classificacao = lead.prioridade ? CLASSIFICACAO_LABELS[lead.prioridade.toUpperCase()] ?? lead.prioridade : "—";

  return (
    <header className="dossier-header">
      <div className="dossier-header-principal">
        <div className="dossier-eyebrow">Dossiê do lead</div>
        <h1 className="dossier-razao">{lead.razao_social}</h1>
        {lead.nome_fantasia && <p className="dossier-fantasia">{lead.nome_fantasia}</p>}
        <p className="dossier-cnpj">{lead.cnpj}</p>

        <p className="dossier-secundaria">
          {lead.municipio ? `${lead.municipio}/${lead.uf ?? "—"}` : "Localização não informada"} · {NICHO_FIXO}
        </p>

        {/* "Score:" saiu daqui -- o número grande no canto (dossier-header-score,
            à direita) já mostra o mesmo dado com muito mais destaque
            visual; manter os dois seria redundante (julgamento pedido
            explicitamente pelo usuário nesta sessão). */}
        <div className="dossier-resumo">
          <span>
            Classificação: <strong>{classificacao}</strong>
          </span>
          <span className="dossier-resumo-sep">·</span>
          <span>Prioridade: {lead.prioridade ? <PriorityBadge prioridade={lead.prioridade} /> : <strong>—</strong>}</span>
          <span className="dossier-resumo-sep">·</span>
          <span>
            Canal: <strong>{canalPreferido(lead)}</strong>
          </span>
          <RqeResumoBadge confirmado={lead.rqe_confirmado} fonte={lead.rqe_fonte} />
        </div>

        <div className="dossier-header-footer">
          <KanbanStatusBadge status={lead.kanban_status} motivoPerda={lead.motivo_perda} />
          <p className="dossier-datas">
            Encontrado em {formatDate(lead.created_at)} · Atualizado {formatRelative(lead.updated_at)}
          </p>
        </div>
      </div>

      {/* Número de score "hero" -- de volta no canto superior direito
          do cabeçalho, do jeito que já existia antes da reestruturação
          em abas (badge de prioridade grande acima, número gigante com
          brilho dourado, label embaixo). A aba "Análise IA" não repete
          mais esse bloco (ver AbaAnalise) -- repetir seria redundante,
          já que agora ele fica visível o tempo todo aqui no cabeçalho,
          em toda aba, não só na Análise IA. */}
      <div className="dossier-header-score">
        <PriorityBadge prioridade={lead.prioridade} size="lg" />
        <div className="dossier-score-total">{lead.score_total ?? "—"}</div>
        <div className="dossier-score-label">pontos de score</div>
      </div>
    </header>
  );
}

function KanbanStatusBadge({ status, motivoPerda }: { status: string; motivoPerda?: string | null }) {
  const label = KANBAN_COLUMNS.find((c) => c.status === status)?.label ?? status;
  const perdido = status === STATUS_PERDIDO;
  return (
    <div className="dossier-kanban-status">
      <span className={`dossier-kanban-pill ${perdido ? "dossier-kanban-pill-perdido" : ""}`}>{label}</span>
      {perdido && motivoPerda && <span className="dossier-motivo-perda">Motivo: {motivoPerda}</span>}
    </div>
  );
}

// --- Navegação por abas ------------------------------------------------

function AbaNav({ ativa, onChange }: { ativa: Aba; onChange: (aba: Aba) => void }) {
  return (
    <div className="dossier-tabs" role="tablist">
      {ABAS.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={ativa === id}
          className={`dossier-tab ${ativa === id ? "dossier-tab-ativa" : ""}`}
          onClick={() => onChange(id)}
        >
          <Icon size={15} />
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}

// --- Aba "Dados" ---------------------------------------------------------
// Mesmos 3 cards (Dados cadastrais/Contato/Presença digital) que já
// existiam antes das abas -- só reagrupados aqui, campos inalterados.
// Dívida ativa PGFN fica no topo da aba, antes dos cards, por ser a
// informação mais crítica (pedido explícito). O card "RQE / Especialidade"
// que ficava aqui como 4º card foi removido nesta sessão (sobrava
// sozinho na última linha do grid) -- RQE virou um badge condicional na
// linha de resumo do cabeçalho fixo (ver RqeResumoBadge), não uma seção
// desta aba.

function AbaDados({ lead }: { lead: Lead }) {
  return (
    <>
      <SecaoPgfn
        qtdInscricoes={lead.divida_ativa_qtd_inscricoes}
        valorTotal={lead.divida_ativa_valor_total}
        ajuizado={lead.divida_ativa_ajuizado}
      />

      <div className="dossier-grid">
        <section className="dossier-card">
          <SectionHeading icon={Building2}>Dados cadastrais</SectionHeading>
          <dl>
            <Campo label="CNAE" valor={lead.cnae_principal} />
            <Campo label="Município/UF" valor={lead.municipio ? `${lead.municipio}/${lead.uf ?? "—"}` : null} />
            <Campo label="Anos de mercado" valor={lead.anos_mercado} />
            <Campo label="Situação cadastral" valor={lead.situacao_cadastral} />
            <Campo label="Simples Nacional" valor={boolLabel(lead.simples_nacional)} />
          </dl>
        </section>

        <section className="dossier-card">
          <SectionHeading icon={Phone}>Contato</SectionHeading>
          <dl>
            <Campo label="Telefone" valor={lead.telefone} />
            <CampoWhatsapp telefone={lead.telefone} ativo={lead.whatsapp_ativo} />
            <CampoEmail email={lead.email} validado={lead.email_validado} />
            <Campo label="Decisor" valor={lead.decisor_nome} />
          </dl>
        </section>

        <section className="dossier-card">
          <SectionHeading icon={Globe}>Presença digital</SectionHeading>
          <dl>
            <Campo label="Site" valor={lead.site} link={lead.site} />
            <Campo label="Instagram" valor={lead.instagram} />
            <Campo label="LinkedIn (empresa)" valor={lead.linkedin_empresa} link={lead.linkedin_empresa} />
            <Campo label="LinkedIn (decisor)" valor={lead.linkedin_decisor} link={lead.linkedin_decisor} />
            <Campo
              label="Rating Google"
              valor={lead.google_rating != null ? `${lead.google_rating} (${lead.google_avaliacoes ?? 0} avaliações)` : null}
            />
            <Campo label="Serviços" valor={lead.servicos} />
          </dl>
        </section>
      </div>
    </>
  );
}

function SecaoPgfn({
  qtdInscricoes,
  valorTotal,
  ajuizado,
}: {
  qtdInscricoes?: number | null;
  valorTotal?: number | null;
  ajuizado?: boolean | null;
}) {
  const temPendencia = !!qtdInscricoes && qtdInscricoes > 0;
  return (
    <section className={`dossier-pgfn ${temPendencia ? "dossier-pgfn-alerta" : "dossier-pgfn-ok"}`}>
      <h2 className="dossier-pgfn-heading">
        {temPendencia ? <Landmark size={17} /> : <ShieldCheck size={17} />}
        <span>Dívida ativa PGFN</span>
      </h2>
      {temPendencia ? (
        <>
          <p>
            {qtdInscricoes} inscrição{qtdInscricoes === 1 ? "" : "ões"} ativa{qtdInscricoes === 1 ? "" : "s"} na
            dívida ativa da União
            {valorTotal != null && (
              <>
                {" "}
                — valor total <strong>{formatCurrencyBRL(valorTotal)}</strong>
              </>
            )}
            {" "}— verificar antes de avançar a negociação.
          </p>
          {ajuizado && (
            <span className="dossier-pgfn-ajuizado">
              <AlertTriangle size={14} />
              <span>Execução judicial em andamento</span>
            </span>
          )}
        </>
      ) : (
        <p>Nenhuma pendência encontrada na Receita/PGFN.</p>
      )}
    </section>
  );
}

/**
 * Badge de RQE pra linha de resumo do cabeçalho -- substitui o antigo
 * card isolado "RQE / Especialidade" (removido nesta sessão por
 * feedback visual: sobrava sozinho na última linha da aba Dados). RQE
 * é um sinal estratégico de ICP (ajuda a confirmar que o lead é
 * médico/clínica de verdade), por isso precisa estar sempre visível no
 * cabeçalho, não escondido numa aba -- diferente do antigo card, que só
 * aparecia na aba "Dados".
 *
 * Omite o badge inteiramente (devolve `null`) quando `confirmado` é
 * `null`/`false`/`undefined` -- pedido explícito: só mostrar RQE quando
 * há algo de fato confirmado ou inferido pra destacar, não um estado
 * "não confirmado" ocupando espaço permanente no cabeçalho (diferente
 * do antigo card, que tinha um terceiro estado "RQE não confirmado").
 * Reaproveita as mesmas classes de cor `rqe-alta-confianca`/
 * `rqe-inferencia` que o card antigo já usava (mesma cor verde de alta
 * confiança / dourada de inferência) -- só o `.dossier-resumo-rqe` de
 * tamanho/formato é novo, ajustado pra caber inline na linha de resumo
 * em vez do badge maior que o card isolado usava.
 */
function RqeResumoBadge({ confirmado, fonte }: { confirmado?: boolean | null; fonte?: string | null }) {
  if (!confirmado) return null;

  const viaCnes = fonte === "cnes";
  return (
    <>
      <span className="dossier-resumo-sep">·</span>
      <span className={`dossier-resumo-rqe ${viaCnes ? "rqe-alta-confianca" : "rqe-inferencia"}`}>
        RQE: {viaCnes ? "Confirmado via CNES" : "Inferido (site)"}
      </span>
    </>
  );
}

// --- Aba "Contatos" --------------------------------------------------------
// Mesmo dado do decisor (decisor_nome, email, whatsapp_ativo,
// linkedin_decisor) que já aparece na aba "Dados", só apresentado de um
// jeito diferente: como um card de pessoa/contato com os canais dele em
// chips clicáveis, em vez de uma linha de spec sheet. Estruturado como
// lista (<ul>) mesmo só tendo hoje um único contato possível
// (decisor_nome é um campo único no Lead) -- comporta mais contatos no
// futuro sem mudar a estrutura, só o array de origem.

function AbaContatos({ lead }: { lead: Lead }) {
  const numeroWhatsapp = formatarNumeroWhatsapp(lead.telefone);
  const temWhatsapp = !!lead.whatsapp_ativo && !!numeroWhatsapp;
  const temEmail = !!lead.email;
  const temLinkedin = !!lead.linkedin_decisor;

  if (!lead.decisor_nome) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Users}>Contatos</SectionHeading>
        <p className="dossier-muted">Nenhum decisor identificado ainda pra este lead.</p>
      </section>
    );
  }

  return (
    <section className="dossier-card">
      <SectionHeading icon={Users}>Contatos</SectionHeading>
      <ul className="dossier-contatos-lista">
        <li className="dossier-contato-card">
          <div className="dossier-contato-nome">{lead.decisor_nome}</div>
          <div className="dossier-contato-canais">
            {temWhatsapp && (
              <a className="dossier-chip" href={`https://wa.me/${numeroWhatsapp}`} target="_blank" rel="noreferrer">
                <MessageCircle size={13} />
                <span>WhatsApp</span>
              </a>
            )}
            {temEmail && (
              <a className="dossier-chip" href={`mailto:${lead.email}`}>
                <Mail size={13} />
                <span>E-mail</span>
              </a>
            )}
            {lead.linkedin_decisor && (
              // Link2 (ícone genérico), não um logo da LinkedIn -- essa
              // versão do lucide-react (1.33) não exporta mais ícones de
              // marca (removidos por questão de trademark); o texto "LinkedIn"
              // ao lado do ícone já deixa claro o canal.
              <a className="dossier-chip" href={lead.linkedin_decisor} target="_blank" rel="noreferrer">
                <Link2 size={13} />
                <span>LinkedIn</span>
              </a>
            )}
            {!temWhatsapp && !temEmail && !temLinkedin && (
              <span className="dossier-muted">Nenhum canal confirmado ainda.</span>
            )}
          </div>
        </li>
      </ul>
    </section>
  );
}

// --- Aba "Análise IA" --------------------------------------------------
// O bloco "hero" de score (badge de prioridade grande + número gigante
// + brilho dourado) voltou pro cabeçalho fixo (ver DossierHeader) nesta
// sessão -- não repetido aqui pra não duplicar o mesmo elemento visual
// em todo lugar. Esta aba foca no que só ela tem: o breakdown dos 9
// critérios e os "Sinais positivos" derivados dele. "Sinais positivos"
// é reorganização do próprio breakdown, não é uma chamada de IA nova:
// qualquer critério que bateu o peso máximo dele vira um chip.

function AbaAnalise({ scoreDetalhes }: { scoreDetalhes: unknown }) {
  const breakdown = getScoreBreakdown(scoreDetalhes);
  // weight > 0 exclui rating_google (peso zerado a pedido do cliente --
  // nunca deveria aparecer como "sinal positivo" de nada, mesmo que
  // pontue 0/0). points >= weight - 0.01 tolera arredondamento de
  // ponto flutuante vindo do backend (ex.: 6 × 20/6 pode chegar como
  // 19.999999999998, não exatamente 20.0).
  const sinaisPositivos = breakdown.filter((item) => item.weight > 0 && item.points >= item.weight - 0.01);

  return (
    <section className="dossier-card dossier-analise">
      {sinaisPositivos.length > 0 && (
        <div className="dossier-sinais-positivos">
          <h3 className="dossier-subheading">
            <CheckCircle2 size={15} />
            <span>Sinais positivos</span>
          </h3>
          <ul className="dossier-sinais-lista">
            {sinaisPositivos.map((item) => (
              <li key={item.key} className="dossier-sinal-chip">
                {item.label}
              </li>
            ))}
          </ul>
        </div>
      )}

      <h3 className="dossier-subheading">
        <Gauge size={15} />
        <span>Breakdown do score</span>
      </h3>
      {breakdown.length ? (
        <ul className="dossier-breakdown-list">
          {breakdown.map((item) => (
            <li key={item.key} className={`dossier-breakdown-item layer-${item.layer}`}>
              <span className="dossier-breakdown-layer-tag">{SIGNAL_LAYER_LABELS[item.layer] ?? item.layer}</span>
              <span className="dossier-breakdown-label">{item.label}</span>
              <span className="dossier-breakdown-pontos">
                {formatPontos(item.points)}/{item.weight} pts
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="dossier-muted">Score ainda não calculado pra este lead.</p>
      )}
    </section>
  );
}

// --- Aba "Mensagens" (abordagem sugerida) -----------------------------

function SecaoAbordagem({
  leadId,
  email,
  temWhatsapp,
  telefone,
  mensagens,
  onGerada,
}: {
  leadId: string;
  email?: string | null;
  temWhatsapp: boolean;
  telefone?: string | null;
  mensagens: LeadMessage[];
  onGerada: (mensagem: LeadMessage) => void;
}) {
  const mostrarEmail = !!email;
  // WhatsApp aqui exige o sinal *confirmado ativo* (whatsapp_ativo),
  // não só ter um telefone cadastrado -- gerar mensagem pra um número
  // que nem confirmamos ter WhatsApp não faz sentido.
  const mostrarWhatsapp = temWhatsapp;

  if (!mostrarEmail && !mostrarWhatsapp) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Sparkles}>Abordagem sugerida</SectionHeading>
        <p className="dossier-muted">Nenhum canal disponível ainda (sem e-mail nem WhatsApp confirmado pra este lead).</p>
      </section>
    );
  }

  return (
    <section className="dossier-card">
      <SectionHeading icon={Sparkles}>Abordagem sugerida</SectionHeading>
      <div className="dossier-abordagem-canais">
        {mostrarEmail && (
          <CanalAbordagemCard
            leadId={leadId}
            canal="email"
            titulo="E-mail"
            mensagem={mensagens.find((m) => m.canal === "email") ?? null}
            onGerada={onGerada}
            email={email}
          />
        )}
        {mostrarWhatsapp && (
          <CanalAbordagemCard
            leadId={leadId}
            canal="whatsapp"
            titulo="WhatsApp"
            mensagem={mensagens.find((m) => m.canal === "whatsapp") ?? null}
            onGerada={onGerada}
            telefone={telefone}
          />
        )}
      </div>
    </section>
  );
}

function CanalAbordagemCard({
  leadId,
  canal,
  titulo,
  mensagem,
  onGerada,
  email,
  telefone,
}: {
  leadId: string;
  canal: CanalAbordagem;
  titulo: string;
  mensagem: LeadMessage | null;
  onGerada: (mensagem: LeadMessage) => void;
  email?: string | null;
  telefone?: string | null;
}) {
  const { token, logout } = useAuth();
  const [gerando, setGerando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function gerar() {
    if (!token) return;
    setGerando(true);
    setErro(null);
    try {
      const nova = await gerarAbordagemCanal(token, leadId, canal);
      onGerada(nova);
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErro("Não foi possível gerar a mensagem agora. Tente novamente.");
    } finally {
      setGerando(false);
    }
  }

  const numeroWhatsapp = formatarNumeroWhatsapp(telefone);

  return (
    <div className="dossier-abordagem-canal">
      <h3 className="dossier-abordagem-canal-titulo">{titulo}</h3>

      {mensagem ? (
        <>
          {canal === "email" && mensagem.assunto && (
            <p className="dossier-abordagem-assunto">
              <strong>Assunto:</strong> {mensagem.assunto}
            </p>
          )}
          <p className="dossier-abordagem-texto">{mensagem.conteudo}</p>
          <div className="dossier-abordagem-acoes">
            {canal === "email" && email && (
              <a
                className="dossier-abordagem-btn-acao"
                href={`mailto:${email}?subject=${encodeURIComponent(mensagem.assunto ?? "")}&body=${encodeURIComponent(mensagem.conteudo)}`}
              >
                <Mail size={14} />
                <span>Abrir no e-mail</span>
              </a>
            )}
            {canal === "whatsapp" && numeroWhatsapp && (
              <a
                className="dossier-abordagem-btn-acao"
                href={`https://wa.me/${numeroWhatsapp}?text=${encodeURIComponent(mensagem.conteudo)}`}
                target="_blank"
                rel="noreferrer"
              >
                <Send size={14} />
                <span>Enviar mensagem gerada</span>
              </a>
            )}
            <button
              type="button"
              className="dossier-abordagem-btn-secundario"
              onClick={gerar}
              disabled={gerando}
              aria-busy={gerando}
            >
              {gerando ? <Loader2 size={14} className="spin" aria-label="Gerando..." /> : "Gerar novamente"}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="dossier-muted">Mensagem ainda não gerada.</p>
          <button type="button" className="dossier-abordagem-btn" onClick={gerar} disabled={gerando} aria-busy={gerando}>
            {gerando ? <Loader2 size={15} className="spin" aria-label="Gerando..." /> : "Gerar mensagem com IA"}
          </button>
        </>
      )}
      {erro && <p className="dossier-abordagem-erro">{erro}</p>}
    </div>
  );
}

// --- Aba "Insights" (análise estratégica via IA) ------------------------
// Mesmo padrão de UX de "Gerar"/"Gerando..."/"Gerar novamente" já usado em
// CanalAbordagemCard (Abordagem sugerida). Diferença: a rota
// `POST /gerar-insights` devolve o Lead INTEIRO já atualizado (não um
// objeto isolado como LeadMessage), então `onGerado` recebe o Lead
// completo -- quem chama (LeadDossierPage) só faz `setLead` direto, sem
// precisar mesclar `insights_ia`/`insights_gerado_em` manualmente.

const POTENCIAL_LABELS: Record<string, string> = {
  alto: "Alto potencial",
  médio: "Médio potencial",
  baixo: "Baixo potencial",
};

/** Verde=alto, dourado=médio, cinza=baixo -- mesma linguagem de cor do
 * resto do dossiê (verde/dourado/cinza já usados em indicador-on/
 * rqe-alta-confianca/layer-estruturado etc.). Um valor fora dos 3
 * esperados (a IA não é forçada a um enum, ver InsightsIA em api.ts) cai
 * no badge neutro/cinza "desconhecido" em vez de quebrar a tela. */
function PotencialBadge({ potencial }: { potencial: string }) {
  const chave = potencial.toLowerCase();
  const classe =
    chave === "alto" ? "potencial-alto" : chave === "médio" ? "potencial-medio" : chave === "baixo" ? "potencial-baixo" : "potencial-desconhecido";
  return <span className={`dossier-potencial-badge ${classe}`}>{POTENCIAL_LABELS[chave] ?? potencial}</span>;
}

function AbaInsights({
  leadId,
  insights,
  geradoEm,
  onGerado,
}: {
  leadId: string;
  insights: InsightsIA | null;
  geradoEm?: string | null;
  onGerado: (lead: Lead) => void;
}) {
  const { token, logout } = useAuth();
  const [gerando, setGerando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function gerar() {
    if (!token) return;
    setGerando(true);
    setErro(null);
    try {
      const leadAtualizado = await gerarInsights(token, leadId);
      onGerado(leadAtualizado);
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        logout();
        return;
      }
      setErro("Não foi possível gerar os insights agora. Tente novamente.");
    } finally {
      setGerando(false);
    }
  }

  if (!insights) {
    return (
      <section className="dossier-card">
        <SectionHeading icon={Lightbulb}>Insights estratégicos</SectionHeading>
        <p className="dossier-muted">
          Ainda não geramos uma análise estratégica pra este lead. A IA olha pro score, dívida PGFN, RQE,
          presença digital e outros sinais já coletados e sugere como priorizar a abordagem.
        </p>
        <button type="button" className="dossier-abordagem-btn" onClick={gerar} disabled={gerando} aria-busy={gerando}>
          {gerando ? <Loader2 size={15} className="spin" aria-label="Gerando..." /> : "Gerar Insights com IA"}
        </button>
        {erro && <p className="dossier-abordagem-erro">{erro}</p>}
      </section>
    );
  }

  return (
    <section className="dossier-card dossier-insights">
      <div className="dossier-insights-header">
        <SectionHeading icon={Lightbulb}>Insights estratégicos</SectionHeading>
        <button
          type="button"
          className="dossier-abordagem-btn-secundario"
          onClick={gerar}
          disabled={gerando}
          aria-busy={gerando}
        >
          {gerando ? <Loader2 size={14} className="spin" aria-label="Gerando..." /> : "Gerar novamente"}
        </button>
      </div>

      {geradoEm && <p className="dossier-insights-data">Gerado {formatRelative(geradoEm)}</p>}

      {insights.resumo_estrategico && <p className="dossier-insights-resumo">{insights.resumo_estrategico}</p>}

      {insights.potencial_oportunidade && (
        <div className="dossier-insights-potencial">
          <span className="dossier-insights-potencial-label">Potencial de oportunidade</span>
          <PotencialBadge potencial={insights.potencial_oportunidade} />
        </div>
      )}

      {insights.recomendacao_abordagem.length > 0 && (
        <div className="dossier-insights-bloco">
          <h3 className="dossier-subheading">
            <Target size={15} />
            <span>Recomendação de abordagem</span>
          </h3>
          <ul className="dossier-insights-recomendacao-lista">
            {insights.recomendacao_abordagem.map((item) => (
              <li key={item}>
                <CheckCircle2 size={14} />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {insights.estrategia_comunicacao && (
        <div className="dossier-insights-bloco">
          <h3 className="dossier-subheading">
            <MessageSquare size={15} />
            <span>Estratégia de comunicação</span>
          </h3>
          <p className="dossier-insights-texto">{insights.estrategia_comunicacao}</p>
        </div>
      )}

      {insights.cta_sugerido && (
        <div className="dossier-insights-cta">
          <span className="dossier-insights-cta-label">CTA sugerido</span>
          <p className="dossier-insights-cta-texto">{insights.cta_sugerido}</p>
        </div>
      )}

      {erro && <p className="dossier-abordagem-erro">{erro}</p>}
    </section>
  );
}

// --- Helpers compartilhados ---------------------------------------------

/** Título de seção com ícone -- usado nos cards das abas "Dados",
 * "Contatos" e "Mensagens" (exceto o de "Dívida ativa PGFN", que troca
 * de ícone conforme pendência/ok, ver SecaoPgfn, e os subtítulos da aba
 * "Análise IA", que usam `.dossier-subheading` -- um nível abaixo, já
 * que a aba inteira é um card só, não vários). `icon` recebe o
 * componente do lucide-react direto (não um elemento já instanciado),
 * pra poder controlar `size` num lugar só. */
function SectionHeading({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <h2 className="dossier-section-heading">
      <Icon size={16} />
      <span>{children}</span>
    </h2>
  );
}

function boolLabel(v?: boolean | null): string | null {
  if (v === null || v === undefined) return null;
  return v ? "Sim" : "Não";
}

function formatPontos(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function Campo({ label, valor, link }: { label: string; valor?: string | number | null; link?: string | null }) {
  const vazio = valor === null || valor === undefined || valor === "";
  return (
    <div className="dossier-campo">
      <dt>{label}</dt>
      <dd className={vazio ? "dossier-campo-vazio" : undefined}>
        {vazio ? "—" : link ? (
          <a href={link} target="_blank" rel="noreferrer">
            {valor}
          </a>
        ) : (
          valor
        )}
      </dd>
    </div>
  );
}

function Indicador({
  ativo,
  textoAtivo,
  textoInativo,
}: {
  ativo?: boolean | null;
  textoAtivo: ReactNode;
  textoInativo: ReactNode;
}) {
  const conhecido = ativo !== null && ativo !== undefined;
  return (
    <span className={`dossier-indicador ${conhecido ? (ativo ? "indicador-on" : "indicador-off") : "indicador-desconhecido"}`}>
      {conhecido ? (ativo ? textoAtivo : textoInativo) : "—"}
    </span>
  );
}

function CampoIndicador({
  label,
  ativo,
  textoAtivo,
  textoInativo,
}: {
  label: string;
  ativo?: boolean | null;
  textoAtivo: ReactNode;
  textoInativo: ReactNode;
}) {
  return (
    <div className="dossier-campo">
      <dt>{label}</dt>
      <dd>
        <Indicador ativo={ativo} textoAtivo={textoAtivo} textoInativo={textoInativo} />
      </dd>
    </div>
  );
}

function CampoWhatsapp({ telefone, ativo }: { telefone?: string | null; ativo?: boolean | null }) {
  const numero = formatarNumeroWhatsapp(telefone);
  return (
    <div className="dossier-campo">
      <dt>WhatsApp</dt>
      <dd className="dossier-campo-acao">
        <Indicador ativo={ativo} textoAtivo="Ativo" textoInativo="Não confirmado" />
        {numero && (
          <a className="dossier-whatsapp-link" href={`https://wa.me/${numero}`} target="_blank" rel="noreferrer">
            <span>Abrir WhatsApp</span>
            <ExternalLink size={12} />
          </a>
        )}
      </dd>
    </div>
  );
}

function CampoEmail({ email, validado }: { email?: string | null; validado?: boolean | null }) {
  const [copiado, setCopiado] = useState(false);

  async function copiar() {
    if (!email) return;
    try {
      await navigator.clipboard.writeText(email);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1500);
    } catch {
      // clipboard indisponível (ex.: contexto não-seguro) -- silencioso,
      // não é crítico o suficiente pra mostrar erro na tela
    }
  }

  return (
    <>
      <div className="dossier-campo">
        <dt>E-mail</dt>
        <dd className="dossier-campo-acao">
          {email ?? <span className="dossier-campo-vazio">—</span>}
          {email && (
            <button type="button" className="dossier-copy-btn" onClick={copiar}>
              {copiado ? "Copiado!" : "Copiar"}
            </button>
          )}
        </dd>
      </div>
      <CampoIndicador label="E-mail validado" ativo={validado} textoAtivo="Validado" textoInativo="Não validado" />
    </>
  );
}
