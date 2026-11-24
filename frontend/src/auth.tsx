import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrf } from "./api";

export type User = { id: number; username: string; role: "admin" | "operador" | "visualizador"; must_change_password: boolean };
type Ctx = {
  user: User | null; loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>; reload: () => Promise<void>;
};
const AuthCtx = createContext<Ctx>(null!);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      const r = await api<{ user: User; csrf_token: string }>("/api/auth/me");
      setCsrf(r.csrf_token); setUser(r.user);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setUser(null);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  // Renova o token periodicamente enquanto a aba está aberta (o limite absoluto da sessão é mantido no servidor).
  useEffect(() => {
    if (!user) return;
    const t = setInterval(() => {
      api<{ csrf_token: string }>("/api/auth/refresh", { method: "POST" })
        .then((r) => setCsrf(r.csrf_token)).catch(() => setUser(null));
    }, 10 * 60 * 1000);
    return () => clearInterval(t);
  }, [user]);

  const login = async (username: string, password: string) => {
    const r = await api<{ user: User; csrf_token: string }>("/api/auth/login", { method: "POST", body: { username, password } });
    setCsrf(r.csrf_token); setUser(r.user);
  };
  const logout = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } finally { setCsrf(""); setUser(null); }
  };
  return <AuthCtx.Provider value={{ user, loading, login, logout, reload }}>{children}</AuthCtx.Provider>;
}
