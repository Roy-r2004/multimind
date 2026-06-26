import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

export function useAdminData<T>(loader: (auth: { token: string; orgId: string }) => Promise<T>) {
  const { authHeaders, isLoading: authLoading } = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setData(await loader(auth));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, [authHeaders, loader]);

  useEffect(() => {
    if (authLoading) return;
    void reload();
  }, [authLoading, reload]);

  return { data, loading: loading || authLoading, error, reload };
}
