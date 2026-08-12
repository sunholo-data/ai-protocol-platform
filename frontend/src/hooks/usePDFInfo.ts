"use client";

import { useState, useEffect } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

interface PDFInfo {
  filename: string;
  pages: number | null;
}

// Module-level cache: avoids duplicate requests within a session.
const _cache = new Map<string, PDFInfo | "error">();

/** Pre-populate the cache (e.g. dev fixtures that know page counts ahead of time). */
export function seedPDFInfoCache(url: string, info: PDFInfo): void {
  _cache.set(url, info);
}

export function usePDFInfo(url: string): { info: PDFInfo | null; loading: boolean } {
  const cached = _cache.get(url);
  const [info, setInfo] = useState<PDFInfo | null>(
    cached && cached !== "error" ? cached : null,
  );
  const [loading, setLoading] = useState(!cached);

  useEffect(() => {
    if (_cache.has(url)) {
      const hit = _cache.get(url)!;
      setInfo(hit !== "error" ? hit : null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    fetchWithAuth(`/api/proxy/api/media/pdf-info?url=${encodeURIComponent(url)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`);
        return res.json() as Promise<PDFInfo>;
      })
      .then((data) => {
        if (cancelled) return;
        _cache.set(url, data);
        setInfo(data);
      })
      .catch(() => {
        if (cancelled) return;
        _cache.set(url, "error");
        setInfo(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [url]);

  return { info, loading };
}
