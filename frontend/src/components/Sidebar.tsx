import { BarChart3, CircleUserRound, LayoutGrid, LogOut, Search } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import "./Sidebar.css";

const ROLE_LABELS: Record<string, string> = {
  admin: "Administrador",
  client: "Cliente",
};

export function Sidebar() {
  const { role, logout } = useAuth();
  const location = useLocation();

  const roleLabel = role ? (ROLE_LABELS[role] ?? role) : "Usuário";
  const ehAdmin = role === "admin";

  const kanbanAtivo = location.pathname === "/";
  const dashboardAtivo = location.pathname === "/dashboard";
  const buscaLeadsAtivo = location.pathname === "/busca-leads";

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-eyebrow">HunterPro</div>
        <div className="sidebar-brand-nome">Minotto Contabilidade</div>
      </div>

      <nav className="sidebar-nav">
        <Link to="/" className={`sidebar-nav-item ${kanbanAtivo ? "sidebar-nav-item-ativo" : ""}`}>
          <LayoutGrid size={18} />
          <span>Kanban</span>
        </Link>

        {/* "Busca de Leads" -- painel de disparo da busca mensal, só
            admin (Caio/Vinícius). Pra um usuário "client" (Douglas/
            Sulamita) o item nem existe na lista -- não é um estado
            desabilitado/"em breve" como já foi no passado, é omitido
            de verdade. Isso é só UX (esconder o que a pessoa não pode
            usar); a proteção real é o `require_admin` do backend (403)
            + o guard `AdminRoute` na rota (ver App.tsx), que redireciona
            mesmo que alguém digite a URL direto. */}
        {ehAdmin && (
          <Link to="/busca-leads" className={`sidebar-nav-item ${buscaLeadsAtivo ? "sidebar-nav-item-ativo" : ""}`}>
            <Search size={18} />
            <span>Busca de Leads</span>
          </Link>
        )}

        <Link to="/dashboard" className={`sidebar-nav-item ${dashboardAtivo ? "sidebar-nav-item-ativo" : ""}`}>
          <BarChart3 size={18} />
          <span>Dashboard</span>
        </Link>
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-user">
          <span className="sidebar-user-icone">
            <CircleUserRound size={16} />
          </span>
          <span className="sidebar-user-nome">{roleLabel}</span>
        </div>
        <button type="button" className="sidebar-logout" onClick={logout}>
          <LogOut size={16} />
          <span>Sair</span>
        </button>
      </div>
    </aside>
  );
}
