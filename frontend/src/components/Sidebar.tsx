import { BarChart3, CircleUserRound, LayoutGrid, LogOut, Search } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import "./Sidebar.css";

const ROLE_LABELS: Record<string, string> = {
  admin: "Administrador",
  client: "Cliente",
};

/**
 * Decodifica só o payload do JWT (sem verificar assinatura -- não tem
 * como verificar client-side mesmo, o token já veio de um login bem-
 * sucedido no backend; isso é só leitura de um campo pra exibição, não
 * uma checagem de segurança). O payload hoje só tem `user_id` e `role`
 * (ver app/api/routes/auth.py) -- sem e-mail nem nome, por isso a
 * sidebar mostra o papel do usuário ("Administrador"/"Cliente"), não um
 * nome de verdade. Mostrar um nome real exigiria o backend incluir
 * e-mail/nome no payload (ou um endpoint tipo GET /api/auth/me) -- fora
 * do escopo desta sessão (só polimento visual), fica como próximo passo
 * natural se o cliente pedir.
 */
function decodeRole(token: string): string | null {
  try {
    const payloadBase64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(payloadBase64)) as { role?: unknown };
    return typeof payload.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

export function Sidebar() {
  const { token, logout } = useAuth();
  const location = useLocation();

  const role = token ? decodeRole(token) : null;
  const roleLabel = role ? (ROLE_LABELS[role] ?? role) : "Usuário";

  const kanbanAtivo = location.pathname === "/";
  const dashboardAtivo = location.pathname === "/dashboard";

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

        <span className="sidebar-nav-item sidebar-nav-item-desabilitado" aria-disabled="true">
          <Search size={18} />
          <span>Busca de Leads</span>
          <span className="sidebar-nav-badge">em breve</span>
        </span>

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
