// useToolLabels — friendly tool labels backed by the server catalog.
//
// Loads `GET /api/tools` once per page load (module-level cache shared by every
// chip / Activity row), never throws, and until — or if never — the catalog
// arrives, labels come from the static framework map + humanise in
// `@/lib/toolLabels`. See that module for the resolution order.

"use client";

import { useCallback, useSyncExternalStore } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { toolLabel, type ToolCatalogLabels } from "@/lib/toolLabels";

let _catalog: ToolCatalogLabels | null = null;
let _requested = false;
const _listeners = new Set<() => void>();

function loadCatalog(): void {
  if (_requested) return;
  _requested = true;
  Promise.resolve()
    .then(() => fetchWithAuth("/api/proxy/api/tools"))
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((d: { tools?: { name?: unknown; label?: unknown }[] }) => {
      const labels: ToolCatalogLabels = {};
      for (const t of d?.tools ?? []) {
        if (typeof t?.name === "string" && typeof t?.label === "string" && t.label) {
          labels[t.name] = t.label;
        }
      }
      _catalog = labels;
      _listeners.forEach((l) => l());
    })
    .catch(() => {
      // Fall back to the static map + humanise — labels still render.
    });
}

function subscribe(listener: () => void): () => void {
  _listeners.add(listener);
  loadCatalog();
  return () => {
    _listeners.delete(listener);
  };
}

const getSnapshot = () => _catalog;
const getServerSnapshot = () => null;

/** Returns `(name) => friendly label`; re-renders once the catalog loads. */
export function useToolLabels(): (name: string) => string {
  const catalog = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return useCallback((name: string) => toolLabel(name, catalog), [catalog]);
}

/** Test seam — reset the module cache between tests. */
export function __resetToolLabelsCache(): void {
  _catalog = null;
  _requested = false;
  _listeners.clear();
}
