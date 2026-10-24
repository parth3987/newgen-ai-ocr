import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { get, post, setCsrf, setUnauthenticatedHandler } from "./api";
import { ROLES, type Role, type SessionInfo } from "./types";

interface Auth {
  session: SessionInfo | null;
  loading: boolean;
  login: (user: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  expired: () => void;
  notice: string | null;
}

const AuthContext = createContext<Auth | null>(null);

export function atLeast(role: Role | undefined, required: Role): boolean {
  return role !== undefined && ROLES.indexOf(role) >= ROLES.indexOf(required);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);

  const apply = useCallback((info: SessionInfo | null) => {
    setCsrf(info?.csrf_token ?? null);
    setSession(info);
  }, []);

  const expired = useCallback(() => {
    apply(null);
    setNotice("Your session ended. Please sign in again.");
  }, [apply]);

  useEffect(() => {
    setUnauthenticatedHandler(expired);
    get<SessionInfo>("/api/v1/auth/session")
      .then(apply)
      .catch(() => apply(null))
      .finally(() => setLoading(false));
  }, [apply, expired]);

  const login = useCallback(
    async (user: string, password: string) => {
      const info = await post<SessionInfo>("/api/v1/auth/login", { user, password });
      setNotice(null);
      apply(info);
    },
    [apply],
  );

  const logout = useCallback(async () => {
    try {
      await post("/api/v1/auth/logout");
    } finally {
      apply(null);
      setNotice("You are signed out.");
    }
  }, [apply]);

  const value = useMemo(
    () => ({ session, loading, login, logout, expired, notice }),
    [session, loading, login, logout, expired, notice],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}
