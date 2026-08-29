/** Authentication context — JWT token + org scoping */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
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
  signIn: (email: string, password: string) => Promise<ApiSession>;
  signOut: () => void;
  refreshSession: () => Promise<void>;
  authHeaders: () => { token: string; orgId: string } | null;
};

const AuthContext = createContext<AuthState | null>(null);

const readStorage = (key: string) =>
  typeof window !== "undefined" ? localStorage.getItem(key) : null;

/** Read org_id embedded in JWT when localStorage org is missing or stale. */
function orgIdFromToken(token: string): string | null {
  try {
    const segment = token.split(".")[1];
    if (!segment) return null;
    const padded = segment.replace(/-/g, "+").replace(/_/g, "/");
    const json = JSON.parse(atob(padded)) as { org_id?: unknown };
    return typeof json.org_id === "string" ? json.org_id : null;
  } catch {
    return null;
  }
}

function resolveOrgId(storedOrg: string | null, token: string | null): string | null {
  if (storedOrg) return storedOrg;
  if (token) return orgIdFromToken(token);
  return null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => readStorage(TOKEN_KEY));
  const [orgId, setOrgId] = useState<string | null>(() =>
    resolveOrgId(readStorage(ORG_KEY), readStorage(TOKEN_KEY)),
  );
  const [session, setSession] = useState<ApiSession | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshGenRef = useRef(0);

  const authHeaders = useCallback((): { token: string; orgId: string } | null => {
    if (!token) return null;
    const resolvedOrg = orgId ?? orgIdFromToken(token);
    if (!resolvedOrg) return null;
    return { token, orgId: resolvedOrg };
  }, [token, orgId]);

  const refreshSession = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setSession(null);
      setIsLoading(false);
      return;
    }

    const gen = ++refreshGenRef.current;

    try {
      const s = await api.auth.session(auth);
      if (gen !== refreshGenRef.current) return;
      setSession(s);
      setOrgId(s.organization.id);
      localStorage.setItem(ORG_KEY, s.organization.id);
    } catch {
      if (gen !== refreshGenRef.current) return;
      setToken(null);
      setOrgId(null);
      setSession(null);
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(ORG_KEY);
    } finally {
      if (gen === refreshGenRef.current) {
        setIsLoading(false);
      }
    }
  }, [authHeaders]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  const signIn = useCallback(async (email: string, password: string) => {
    refreshGenRef.current += 1;
    const result = await api.auth.signIn({ email, password });
    const tokenOrg = orgIdFromToken(result.access_token);

    let sessionData: ApiSession;
    if (result.user && result.organization) {
      sessionData = { user: result.user, organization: result.organization };
    } else {
      const sessionOrg = tokenOrg ?? orgId ?? "";
      sessionData = await api.auth.session({
        token: result.access_token,
        orgId: sessionOrg,
      });
    }

    setToken(result.access_token);
    setOrgId(sessionData.organization.id);
    setSession(sessionData);
    setIsLoading(false);
    localStorage.setItem(TOKEN_KEY, result.access_token);
    localStorage.setItem(ORG_KEY, sessionData.organization.id);
    return sessionData;
  }, [orgId]);

  const signOut = useCallback(() => {
    refreshGenRef.current += 1;
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
