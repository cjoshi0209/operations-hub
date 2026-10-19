import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getToken, request, setToken } from "./api/client";
import { useMe } from "./api/hooks";
import type { Me } from "./api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [hasToken, setHasToken] = useState(() => !!getToken());
  const meQuery = useMe(hasToken);

  const logout = useCallback(() => {
    setToken(null);
    setHasToken(false);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    window.addEventListener("auth:logout", logout);
    return () => window.removeEventListener("auth:logout", logout);
  }, [logout]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await request<{ token: string }>("/auth/login", { method: "POST", body: { email, password } });
      qc.clear();
      setToken(res.token);
      setHasToken(true);
    },
    [qc],
  );

  return (
    <Ctx.Provider
      value={{ me: hasToken ? meQuery.data ?? null : null, loading: hasToken && meQuery.isLoading, login, logout }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
