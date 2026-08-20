import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { fetchLead, UnauthorizedError, type Lead } from "../api";
import { PriorityBadge } from "../components/PriorityBadge";
import { useAuth } from "../context/AuthContext";
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
            </div>
            <div className="dossier-header-score">
              <PriorityBadge prioridade={lead.prioridade} />
              <div className="dossier-score-total">{lead.score_total ?? "—"}</div>
              <div className="dossier-score-label">pontos</div>
            </div>
          </header>

          <SecaoPgfn qtdInscricoes={lead.divida_ativa_qtd_inscricoes} />

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
                <CampoIndicador label="WhatsApp" ativo={lead.whatsapp_ativo} textoAtivo="Ativo" textoInativo="Não confirmado" />
                <Campo label="E-mail" valor={lead.email} />
                <CampoIndicador label="E-mail validado" ativo={lead.email_validado} textoAtivo="Validado" textoInativo="Não validado" />
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
          </div>

          <section className="dossier-card">
            <h2>Breakdown do score</h2>
            {lead.score_detalhes?.breakdown?.length ? (
              <ul className="dossier-breakdown-list">
                {lead.score_detalhes.breakdown.map((item) => (
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
        </>
      )}
    </div>
  );
}

function SecaoPgfn({ qtdInscricoes }: { qtdInscricoes?: number | null }) {
  const temPendencia = !!qtdInscricoes && qtdInscricoes > 0;
  return (
    <section className={`dossier-pgfn ${temPendencia ? "dossier-pgfn-alerta" : "dossier-pgfn-ok"}`}>
      <h2>Dívida ativa PGFN</h2>
      {temPendencia ? (
        <p>
          {qtdInscricoes} inscrição{qtdInscricoes === 1 ? "" : "ões"} ativa{qtdInscricoes === 1 ? "" : "s"} na dívida
          ativa da União — verificar antes de avançar a negociação.
        </p>
      ) : (
        <p>Nenhuma pendência encontrada na Receita/PGFN.</p>
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
  const conhecido = ativo !== null && ativo !== undefined;
  return (
    <div className="dossier-campo">
      <dt>{label}</dt>
      <dd>
        <span className={`dossier-indicador ${conhecido ? (ativo ? "indicador-on" : "indicador-off") : "indicador-desconhecido"}`}>
          {conhecido ? (ativo ? textoAtivo : textoInativo) : "—"}
        </span>
      </dd>
    </div>
  );
}
