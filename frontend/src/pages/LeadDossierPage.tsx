import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { fetchLead, gerarAbordagem, UnauthorizedError, type Lead } from "../api";
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
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !id) return;
    fetchLead(token, id)
      .then(setLead)
      .catch((e) => {
        if (e instanceof UnauthorizedError) {
          logout();
          return;
        }
        setErro(String(e));
      })
      .finally(() => setCarregando(false));
  }, [token, id, logout]);

  return (
    <div className="dossier-page">
      <button className="dossier-voltar" onClick={() => navigate("/")}>
        ← Voltar pro Kanban
      </button>

      {carregando && <p className="dossier-status">Carregando...</p>}
      {erro && <p className="dossier-status dossier-erro">{erro}</p>}

      {lead && (
        <>
          <header className="dossier-header">
            <div>
              <div className="dossier-eyebrow">HunterPro</div>
              <h1 className="dossier-razao">{lead.razao_social}</h1>
              {lead.nome_fantasia && <p className="dossier-fantasia">{lead.nome_fantasia}</p>}
              <p className="dossier-cnpj">{lead.cnpj}</p>
              <KanbanStatusBadge status={lead.kanban_status} motivoPerda={lead.motivo_perda} />
            </div>
            <div className="dossier-header-score">
              <PriorityBadge prioridade={lead.prioridade} />
              <div className="dossier-score-total">{lead.score_total ?? "—"}</div>
              <div className="dossier-score-label">pontos</div>
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
              <h2>Dados cadastrais</h2>
              <dl>
                <Campo label="CNAE" valor={lead.cnae_principal} />
                <Campo label="Município/UF" valor={lead.municipio ? `${lead.municipio}/${lead.uf ?? "—"}` : null} />
                <Campo label="Anos de mercado" valor={lead.anos_mercado} />
                <Campo label="Situação cadastral" valor={lead.situacao_cadastral} />
                <Campo label="Simples Nacional" valor={boolLabel(lead.simples_nacional)} />
              </dl>
            </section>

            <section className="dossier-card">
              <h2>Contato</h2>
              <dl>
                <Campo label="Telefone" valor={lead.telefone} />
                <CampoWhatsapp telefone={lead.telefone} ativo={lead.whatsapp_ativo} />
                <CampoEmail email={lead.email} validado={lead.email_validado} />
                <Campo label="Decisor" valor={lead.decisor_nome} />
              </dl>
            </section>

            <section className="dossier-card">
              <h2>Presença digital</h2>
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

          <SecaoAbordagem leadId={lead.id} mensagem={lead.mensagem_abordagem} onGerada={setLead} />

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
      <h2>Dívida ativa PGFN</h2>
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
          {ajuizado && <span className="dossier-pgfn-ajuizado">⚠️ Execução judicial em andamento</span>}
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
      <h2>RQE / Especialidade</h2>
      <span className={`dossier-rqe-badge ${classe}`}>{texto}</span>
    </section>
  );
}

function SecaoAbordagem({
  leadId,
  mensagem,
  onGerada,
}: {
  leadId: string;
  mensagem?: string | null;
  onGerada: (lead: Lead) => void;
}) {
  const { token, logout } = useAuth();
  const [gerando, setGerando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function gerar() {
    if (!token) return;
    setGerando(true);
    setErro(null);
    try {
      const leadAtualizado = await gerarAbordagem(token, leadId);
      onGerada(leadAtualizado);
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

  return (
    <section className="dossier-card">
      <h2>Abordagem sugerida</h2>
      {mensagem ? (
        <>
          <p className="dossier-abordagem-texto">{mensagem}</p>
          <button type="button" className="dossier-abordagem-btn-secundario" onClick={gerar} disabled={gerando}>
            {gerando ? "Gerando..." : "Gerar novamente"}
          </button>
        </>
      ) : (
        <>
          <p className="dossier-muted">Mensagem ainda não gerada.</p>
          <button type="button" className="dossier-abordagem-btn" onClick={gerar} disabled={gerando}>
            {gerando ? "Gerando..." : "Gerar mensagem com IA"}
          </button>
        </>
      )}
      {erro && <p className="dossier-abordagem-erro">{erro}</p>}
    </section>
  );
}

function SecaoBreakdown({ scoreDetalhes }: { scoreDetalhes: unknown }) {
  const breakdown = getScoreBreakdown(scoreDetalhes);
  return (
    <section className="dossier-card">
      <h2>Breakdown do score</h2>
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
            Abrir WhatsApp ↗
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
