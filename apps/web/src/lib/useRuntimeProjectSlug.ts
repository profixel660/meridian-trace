"use client";

import { useEffect, useState } from "react";

/**
 * Read the project slug from `window.location.pathname` at runtime.
 *
 * Why this exists: the frontend is a Next.js static export. The build
 * pre-renders `/projects/[name]` with the placeholder slug `_`, since
 * project names are runtime-only data. Alpha-18 added a FastAPI SPA
 * fallback so URLs like `/projects/bod-2` serve that same `_` HTML and
 * the React app then hydrates from the URL. But pages that read the
 * slug via `use(params)` get the build-time `_` instead of the real
 * runtime slug — every API call goes to `/projects/_/coverage` and
 * 404s. This hook reads the slug from `window.location.pathname`
 * after mount, returning `null` until the client takes over so SSR
 * hydration matches the build output (which had `_`).
 */
export function useRuntimeProjectSlug(): string | null {
  const [slug, setSlug] = useState<string | null>(null);
  useEffect(() => {
    const m = window.location.pathname.match(/^\/projects\/([^/]+)/);
    if (m) setSlug(decodeURIComponent(m[1]));
  }, []);
  return slug;
}
