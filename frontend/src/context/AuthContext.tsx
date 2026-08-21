import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { login as apiLogin } from "../api";

interface AuthContextValue {
  token: string | null;
  role: string | null;
  isAuthenticated: boolean;
  login: (email: string, senha: string) => Promise<void>;
  logout: () => void;
}

// Token guardado só em memória (useState), nunca em localStorage/
// sessionStorage/cookie -- recarregar a página desloga de propósito.
// Evita o risco básico de um token de longa duração ficar acessível a
// qualquer script (XSS) que rode na página via localStorage.
const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Decodifica só o payload do JWT (sem verificar assinatura -- não tem
 * como verificar client-side mesmo, o token já veio de um login bem-
 * sucedido no backend; isso é só leitura de um claim já confiável por
 * ter passado pelo login, não uma checagem de segurança nova). O
 * payload hoje só tem `user_id` e `role` (ver app/api/routes/auth.py)
 * -- sem e-mail nem nome.
 *
 * Movida de `components/Sidebar.tsx` (onde vivia sozinha) pra cá nesta
 * sessão -- `role` passou a ser necessário em mais de um lugar (o item
 * "Busca de Leads" da sidebar E o guard `AdminRoute`, ambos condicionais
 * a `role === "admin"`), então centralizar em `useAuth()` evita
 * duplicar a mesma lógica de decode pela terceira vez.
 *
 * ⚠️ Isso NÃO é uma fronteira de segurança -- é só leitura de um claim
 * pra fins de UX (mostrar/esconder um item de menu, redirecionar de uma
 * tela). A autorização de verdade continua inteiramente no backend
 * (`require_admin`, 403 pra "client") -- esconder algo aqui nunca é o
 * que impede o acesso real aos dados.
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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const role = useMemo(() => (token ? decodeRole(token) : null), [token]);

  const login = useCallback(async (email: string, senha: string) => {
    const novoToken = await apiLogin(email, senha);
    setToken(novoToken);
  }, []);

  const logout = useCallback(() => setToken(null), []);

  const value = useMemo(
    () => ({ token, role, isAuthenticated: token !== null, login, logout }),
    [token, role, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth precisa ser usado dentro de <AuthProvider>");
  return ctx;
}
