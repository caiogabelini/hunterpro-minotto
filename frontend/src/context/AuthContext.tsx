import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { login as apiLogin } from "../api";

interface AuthContextValue {
  token: string | null;
  isAuthenticated: boolean;
  login: (email: string, senha: string) => Promise<void>;
  logout: () => void;
}

// Token guardado só em memória (useState), nunca em localStorage/
// sessionStorage/cookie -- recarregar a página desloga de propósito.
// Evita o risco básico de um token de longa duração ficar acessível a
// qualquer script (XSS) que rode na página via localStorage.
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);

  const login = useCallback(async (email: string, senha: string) => {
    const novoToken = await apiLogin(email, senha);
    setToken(novoToken);
  }, []);

  const logout = useCallback(() => setToken(null), []);

  const value = useMemo(
    () => ({ token, isAuthenticated: token !== null, login, logout }),
    [token, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth precisa ser usado dentro de <AuthProvider>");
  return ctx;
}
