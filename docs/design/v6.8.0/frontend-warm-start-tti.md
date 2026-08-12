# Frontend Warm-Start & Time-to-Interactive

**Status**: Design-ahead (deferred — nothing built this push)
**Priority**: P2
**Estimated**: ~2 days (3 phases)
**Scope**: Frontend
**Dependencies**: 6.5.0 authenticated-landing ✅, 6.1.0 ttft-instrumentation ✅
**Created**: 2026-07-14
**Last Updated**: 2026-07-14

> **Deferred by decision (2026-07-14).** Captured now so the analysis isn't lost and the
> work is schedulable later. Not part of the 8.1/8.2/8.3 build.

## Problem Statement

Backend TTFT is well-optimized and never-dead-air is solid, but an authed user's
**time-to-interactive** (able to type the first message) is gated by a serial frontend path.

**Current State (from the 2026-07-14 audit):**
- **Two sequential redirect hops**, each with its own round-trips and its own loading UI,
  nothing parallelized across the boundary:
  - Hop A on `/`: auth rehydrate → `GET /sessions/recent` → (miss) `GET /clients/me` +
    `GET /skills` → `router.replace` (`frontend/src/hooks/useLandingTarget.ts:45-69`,
    `components/home/HomeGate.tsx`).
  - Hop B on `/chat/@owner/slug`: `GET /api/skills/by-slug/...` (`useSlugResolution`) → then
    inside `ChatShell`: `useSkillMeta` (`GET /api/skills/{id}`) **+** `useBackendReady`
    (`/health` poll).
- **Cold-start `/health` poll is the real TTI ceiling** — the chat renders but the composer
  is disabled for **5–30s** on a cold backend (`hooks/useBackendReady.ts:46`; banner shown,
  so not *silent*, but not typeable). The backend is **not warmed during the HomeGate spinner**.
- **Duplicate skill fetch**: `useSlugResolution` (by-slug) and `useSkillMeta` (by-id) fetch
  the same skill — two round-trips for overlapping data.
- A bland bare **"Loading…"** micro-moment (no skeleton) between the branded HomeGate spinner
  and the chat shell (`app/chat/[...path]/page.tsx:43-45`).

**Impact:** Best case ≈ 3–4 serial backend round-trips before typeable; worst case gated by a
cold `/health`. Undercuts the fast first-impression the front door (8.2) delivers on the backend.

## Goals

**Primary Goal:** An authed user is typeable in ≈1 round-trip of perceived time, with the
backend warmed before they arrive at the chat shell.

**Success Metrics:**
- Warm the backend (`/health`) **during** the HomeGate redirect spinner so the cold-start window is hidden.
- Collapse the duplicate by-slug + by-id skill fetch to one payload.
- Remove/skeleton the bare "Loading…" micro-moment.

**Non-Goals:** Backend TTFT changes (already covered by 6.1.0); the handoff feature (8.2).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Directly attacks the TTI ceiling — the axiom's core KPI (frontend overhead <500ms). |
| 5 | GRACEFUL DEGRADATION | +1 | Warm-start failure degrades to today's behavior (banner + disabled composer), never worse. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Reuse `latencyStore`/`LatencyHUD` to measure TTI before/after. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Frontend-only orchestration of existing calls; no business logic added. |
| (others) | | 0 | Neutral. |
| | **Net Score** | **+3 (indicative)** | Finalize when scheduled. |

## Design (sketch)

- **Warm-start:** fire a `/health` (and optionally a skill-meta prefetch) as soon as auth is
  known in `HomeGate`, so `useBackendReady` on the chat page resolves against an already-warm
  backend. Consider a tiny keep-warm on the landing route.
- **Dedup fetch:** have `useSlugResolution` return (or cache) enough skill meta that
  `useSkillMeta` is served from cache, or fold both into one endpoint/one SWR key.
- **Micro-moment:** replace the bare "Loading…" with a skeleton consistent with the HomeGate/ChatShell chrome.
- Consider collapsing the two redirect hops (resolve the target skill's shell + meta in Hop A so Hop B has nothing to fetch).

## Testing Strategy

- [ ] `latencyStore` TTI measurement before/after (cold + warm backend).
- [ ] Real-browser cold-start: composer typeable materially sooner; no regression to never-silent banners.

## Success Criteria

- [ ] Cold-start un-typeable window materially reduced/hidden.
- [ ] One skill fetch on the chat page, not two.
- [ ] No bare unstyled loading text on the critical path.

## Related Documents

- [authenticated-landing.md](../v6.5.0/authenticated-landing.md)
- [ttft-optimization.md](../v6.1.0/implemented/ttft-optimization.md)
- [first-impression-elicited-handoff.md](first-impression-elicited-handoff.md) — the backend-side first-impression this complements
