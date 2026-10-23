import { Catalog, type ComponentApi } from "@a2ui/web_core/v0_9";

// SDK 0.10 moved the Basic catalog URL. Existing server messages and persisted
// surfaces still name the v0.9 URL; both IDs must use our patched implementations.
export const LEGACY_BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/basic_catalog.json";

export function withBasicCatalogAliases<T extends ComponentApi>(catalog: Catalog<T>): Catalog<T>[] {
  if (catalog.id === LEGACY_BASIC_CATALOG_ID) return [catalog];
  return [catalog, new Catalog(
    LEGACY_BASIC_CATALOG_ID,
    [...catalog.components.values()],
    [...catalog.functions.values()],
    catalog.themeSchema,
  )];
}
