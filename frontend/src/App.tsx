import { useEffect, useState } from "react";
import "./App.css";
import { checkHealth, fetchLeads, type Lead } from "./api";

function App() {
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    checkHealth().then((res) => setApiOnline(!!res));
  }, []);

  useEffect(() => {
    if (!apiOnline) return;
    fetchLeads()
      .then(setLeads)
      .catch((e) => setError(String(e)));
  }, [apiOnline]);

  return (
    <div className="page">
      <header className="header">
        <div>
          <div className="eyebrow">HunterPro</div>
          <h1>Minotto Contabilidade</h1>
        </div>
        <div className={`status-pill ${apiOnline ? "online" : "offline"}`}>
          {apiOnline === null ? "verificando..." : apiOnline ? "API online" : "API offline"}
        </div>
      </header>

      <section className="card">
        <h2>Leads</h2>
        {!apiOnline && (
          <p className="muted">
            Backend ainda não está rodando (ou o pipeline de busca ainda não foi
            implementado — isso é próximo passo). Suba a API local com{" "}
            <code>uvicorn app.main:app --reload</code> pra ver esta lista funcionando.
          </p>
        )}
        {error && <p className="error">{error}</p>}
        {apiOnline && leads.length === 0 && !error && (
          <p className="muted">Nenhum lead no banco ainda — normal nesta fase do projeto.</p>
        )}
        {leads.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Razão social</th>
                <th>Município/UF</th>
                <th>Score</th>
                <th>Prioridade</th>
                <th>Zona Franca</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <tr key={lead.id}>
                  <td>{lead.razao_social}</td>
                  <td>{lead.municipio ? `${lead.municipio}/${lead.uf}` : "—"}</td>
                  <td>{lead.score_total ?? "—"}</td>
                  <td>{lead.prioridade ?? "—"}</td>
                  <td>{lead.zona_franca ? "Sim" : "Não"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <footer className="footer">4HANDS.AI × Minotto Contabilidade</footer>
    </div>
  );
}

export default App;
