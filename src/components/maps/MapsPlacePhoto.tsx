import { Building2 } from "lucide-react";
import { useEffect, useState } from "react";
import { getApiBase } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { mapsPlacePhotoPath } from "@/lib/maps/api";
import { cn } from "@/lib/utils";

// A plain <img src> can't carry the Authorization header this endpoint needs, so
// facility photos are fetched as a blob and cached as an object URL — once per
// place for the whole page session, never re-fetched on scroll/re-render.
const objectUrlCache = new Map<string, string>();
const pendingFetches = new Map<string, Promise<string | null>>();

async function fetchPlacePhotoObjectUrl(
  key: string,
  path: string,
  auth: { token: string; orgId: string },
): Promise<string | null> {
  const cached = objectUrlCache.get(key);
  if (cached) return cached;
  const pending = pendingFetches.get(key);
  if (pending) return pending;

  const promise = (async () => {
    try {
      const response = await fetch(`${getApiBase()}${path}`, {
        headers: {
          Authorization: `Bearer ${auth.token}`,
          "X-Org-Id": auth.orgId,
        },
      });
      if (!response.ok) return null;
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      objectUrlCache.set(key, objectUrl);
      return objectUrl;
    } catch {
      return null;
    } finally {
      pendingFetches.delete(key);
    }
  })();
  pendingFetches.set(key, promise);
  return promise;
}

export function MapsPlacePhoto({
  runId,
  placeId,
  hasPhoto,
  alt,
  className,
}: {
  runId: string;
  placeId: string;
  hasPhoto: boolean;
  alt: string;
  className?: string;
}) {
  const { authHeaders } = useAuth();
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!hasPhoto) return;
    const auth = authHeaders();
    if (!auth) return;
    setFailed(false);
    let cancelled = false;
    void fetchPlacePhotoObjectUrl(
      `${runId}:${placeId}`,
      mapsPlacePhotoPath(runId, placeId),
      auth,
    ).then((url) => {
      if (cancelled) return;
      if (url) setSrc(url);
      else setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [runId, placeId, hasPhoto, authHeaders]);

  if (!hasPhoto || failed || !src) {
    return (
      <div
        className={cn(
          "grid shrink-0 place-items-center rounded-xl bg-muted/60 text-muted-foreground",
          className,
        )}
      >
        <Building2 className="size-5" />
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      className={cn("shrink-0 rounded-xl object-cover", className)}
    />
  );
}
