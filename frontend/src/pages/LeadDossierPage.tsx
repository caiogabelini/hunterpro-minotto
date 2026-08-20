import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  ExternalLink,
  Gauge,
  Globe,
  Landmark,
  Loader2,
  Mail,
  Phone,
  Send,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  fetchLead,
  fetchMensagens,
  gerarAbordagemCanal,
  UnauthorizedError,
  type CanalAbordagem,
  type Lead,
  type LeadMessage,
} from "../api";
import { PriorityBadge } from "../components/PriorityBadge";
import { useAuth } from "../context/AuthContext";
import { formatarNumeroWhatsapp, formatCurrencyBRL, formatDate, formatRelative } from "../format";
import { KANBAN_COLUMNS, STATUS_PERDIDO } from "../kanbanStatuses";
import { getScoreBreakdown } from "../leadScore";
import { SIGNAL_LAYER_LABELS } from "../scoreLayers";
import "./LeadDossierPage.css";

export function LeadDossierPage() {
  const { id } = useParams<{ id: string }>();
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [lead, setLead] = useState<Lead | null>(null);
  const [mensagens, setMensagens] = useState<LeadMessage[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

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
          <header className="dossier-header">
            <div>
              <div className="dossier-eyebrow">Dossiê do lead</div>
              <h1 className="dossier-razao">{lead.razao_social}</h1>
              {lead.nome_fantasia && <p className="dossier-fantasia">{lead.nome_fantasia}</p>}
              <p className="dossier-cnpj">{lead.cnpj}</p>
              <KanbanStatusBadge status={lead.kanban_status} motivoPerda={lead.motivo_perda} />
            </div>
            <div className="dossier-header-score">
              <PriorityBadge prioridade={lead.prioridade} size="lg" />
              <div className="dossier-score-total">{lead.score_total ?? "—"}</div>
              <div className="dossier-score-label">pontos de score</div>
            </div>
          </header>

          <p className="dossier-datas">
            Encontrado em {formatDate(lead.created_at)} · Atualizado {formatRelative(lead.updated_at)}
          </p>

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

            <SecaoRqe confirmado={lead.rqe_confirmado} fonte={lead.rqe_fonte} />
          </div>

          <SecaoAbordagem
            leadId={lead.id}
            email={lead.email}
            temWhatsapp={!!lead.whatsapp_ativo}
            telefone={lead.telefone}
            mensagens={mensagens}
            onGerada={registrarNovaMensagem}
          />

          <SecaoBreakdown scoreDetalhes={lead.score_detalhes} />
        </>
      )}
    </div>
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

function SecaoRqe({ confirmado, fonte }: { confirmado?: boolean | null; fonte?: string | null }) {
  let texto: string;
  let classe: string;

  if (confirmado && fonte === "cnes") {
    texto = "RQE confirmado via CNES";
    classe = "rqe-alta-confianca";
  } else if (confirmado) {
    texto = "RQE mencionado no site (não confirmado via CNES)";
    classe = "rqe-inferencia";
  } else {
    texto = "RQE não confirmado";
    classe = "rqe-desconhecido";
  }

  return (
    <section className="dossier-card">
      <SectionHeading icon={Stethoscope}>RQE / Especialidade</SectionHeading>
      <span className={`dossier-rqe-badge ${classe}`}>{texto}</span>
    </section>
  );
}

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

function SecaoBreakdown({ scoreDetalhes }: { scoreDetalhes: unknown }) {
  const breakdown = getScoreBreakdown(scoreDetalhes);
  return (
    <section className="dossier-card">
      <SectionHeading icon={Gauge}>Breakdown do score</SectionHeading>
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

/** Título de seção com ícone -- usado em todo `<h2>` deste dossiê
 * (exceto o de "Dívida ativa PGFN", que troca de ícone conforme
 * pendência/ok, ver SecaoPgfn). `icon` recebe o componente do
 * lucide-react direto (não um elemento já instanciado), pra poder
 * controlar `size` num lugar só. */
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
