/** Authentication context — JWT token + org scoping */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { ApiSession } from "@/lib/api/types";

const TOKEN_KEY = "multiai_token";
const ORG_KEY = "multiai_org_id";

type AuthState = {
  token: string | null;
  orgId: string | null;
  session: ApiSession | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => void;
  refreshSession: () => Promise<void>;
  authHeaders: () => { token: string; orgId: string } | null;
};

const AuthContext = createContext<AuthState | null>(null);

const readStorage = (key: string) =>
  typeof window !== "undefined" ? localStorage.getItem(key) : null;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => readStorage(TOKEN_KEY));
  const [orgId, setOrgId] = useState<string | null>(() => readStorage(ORG_KEY));
  const [session, setSession] = useState<ApiSession | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const authHeaders = useCallback((): { token: string; orgId: string } | null => {
    if (!token || !orgId) return null;
    return { token, orgId };
  }, [token, orgId]);

  const refreshSession = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setSession(null);
      setIsLoading(false);
      return;
    }
    try {
      const s = await api.auth.session(auth);
      setSession(s);
      setOrgId(s.organization.id);
      localStorage.setItem(ORG_KEY, s.organization.id);
    } catch {
      setToken(null);
      setOrgId(null);
      setSession(null);
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(ORG_KEY);
    } finally {
      setIsLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  const signIn = useCallback(async (email: string, password: string) => {
    const { access_token } = await api.auth.signIn({ email, password });
    setToken(access_token);
    localStorage.setItem(TOKEN_KEY, access_token);
    const tempAuth = { token: access_token, orgId: orgId ?? "" };
    const s = await api.auth.session(tempAuth);
    setSession(s);
    setOrgId(s.organization.id);
    localStorage.setItem(ORG_KEY, s.organization.id);
  }, [orgId]);

  const signOut = useCallback(() => {
    setToken(null);
    setOrgId(null);
    setSession(null);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ORG_KEY);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      token,
      orgId,
      session,
      isLoading,
      isAuthenticated: Boolean(token && session),
      signIn,
      signOut,
      refreshSession,
      authHeaders,
    }),
    [token, orgId, session, isLoading, signIn, signOut, refreshSession, authHeaders],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
