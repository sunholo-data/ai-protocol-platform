# Frontend Architecture

**Status**: Implemented
**Priority**: P2 (Low)
**Estimated**: 1 week
**Scope**: Frontend
**Dependencies**: [Streaming & Protocols](../streaming-and-protocols.md), [Skills Data Model](skills-data-model.md), [Auth & Permissions](auth-and-permissions.md)
**Created**: 2026-04-10
**Last Updated**: 2026-04-21

## Problem Statement

The v6 frontend is 100% empty — directory structure exists but zero files are implemented. v5's frontend is Next.js + React but tightly coupled to the "assistant" concept and custom SSE streaming. v6 needs a fresh frontend built on:

- CopilotKit / AG-UI for streaming (replacing custom SSE)
- A2UI for declarative UI rendering (new)
- MCP Apps for tool UIs (new)
- Skills as the primary concept (replacing assistants)

**Current State:**
- `frontend/src/` has directory structure with empty folders
- `package.json` has all dependencies declared
- v5 frontend at `<your-v5-source>/src/`
- No components, contexts, hooks, or pages implemented

**Impact:**
- Required for user-facing product
- Blocks skill marketplace, chat interface, skill builder
- Frontend is the primary way users interact with skills

## Goals

**Primary Goal:** Build a responsive chat-first frontend with skill marketplace, supporting AG-UI streaming, A2UI components, and MCP Apps iframes.

**Success Metrics:**
- Skill marketplace loads in <2s, displays skills with protocol badges
- Chat sends message → first token appears in <500ms (frontend overhead)
- A2UI components render inline in chat
- Auth flow works (Google Sign-In → protected routes)

**Non-Goals:**
- Mobile app (responsive web only)
- Offline support
- Custom design system (use Radix + Tailwind)
- Admin dashboard (manage via Firestore console)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | <500ms chat overhead, <2s marketplace load, virtual scroll for long conversations |
| 2 | EARNED TRUST | 0 | Frontend renders what backend provides — trust lives in agent responses |
| 3 | SKILLS, NOT FEATURES | +1 | Marketplace, skill builder wizard, skill-centric navigation |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Model selection is backend |
| 5 | GRACEFUL DEGRADATION | 0 | Not detailed in design |
| 6 | PROTOCOL OVER CUSTOM | +1 | CopilotKit for AG-UI, A2UI renderer, MCP Apps iframe — no custom streaming |
| 7 | API FIRST | +1 | All calls through /api/proxy — no direct backend coupling |
| 8 | OBSERVABLE BY DEFAULT | 0 | Frontend metrics not detailed |
| 9 | SECURE BY CONSTRUCTION | +1 | JWT on all API calls, MCP Apps sandboxed, no secrets client-side |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | **Core principle**: frontend is a renderer, zero business logic |
| | **Net Score** | **+6** | Threshold: >= +4 |

## Design

### Overview

The frontend is a Next.js 14 App Router application with three main views: (1) skill marketplace, (2) skill chat, and (3) skill builder. State flows through React contexts for auth and skill selection, with CopilotKit handling AG-UI streaming.

### Page Structure

```
frontend/src/app/
├── page.tsx                        # Home → redirect to /skills
├── layout.tsx                      # Root layout with providers
├── skills/
│   └── page.tsx                    # Skill marketplace
├── skill/[skillId]/
│   └── page.tsx                    # Skill chat interface
├── create/
│   └── page.tsx                    # Skill builder wizard
└── api/
    ├── proxy/route.ts              # Backend proxy (all /api/* calls)
    ├── telegram/webhook/route.ts   # Telegram webhook passthrough
    ├── email/webhook/route.ts      # Email webhook passthrough
    └── gcs/route.ts                # GCS signed URL generator
```

### Component Hierarchy

```
<html>
  <body>
    <FirebaseProvider>              ← Firebase SDK initialization
      <AuthProvider>                ← Auth state (user, token)
        <Layout>                   ← Nav, sidebar, footer
          {children}               ← Page content
        </Layout>
      </AuthProvider>
    </FirebaseProvider>

--- Per-page ---

Skills Marketplace (/skills):
  <SkillList>
    <SkillCard />                  ← Grid of skill cards
    <SkillCard />
    ...
  </SkillList>

Skill Chat (/skill/[skillId]):
  <AGUIProvider skillId={id}>      ← CopilotKit AG-UI transport
    <SkillProvider skillId={id}>   ← Skill config context
      <ChatInterface>
        <ChatMessages>
          <TextMessage />          ← Plain text bubbles
          <A2UIRenderer />         ← Inline declarative UI
          <MCPAppFrame />          ← Sandboxed tool UIs
          <ToolIndicator />        ← Tool execution status
        </ChatMessages>
        <ChatInput />              ← Message input + file upload
      </ChatInterface>
    </SkillProvider>
  </AGUIProvider>

Skill Builder (/create):
  <SkillBuilderWizard>
    <Step1_BasicInfo />            ← Name, description, avatar
    <Step2_ModelConfig />          ← Model selection, instruction
    <Step3_ToolSelection />        ← Tool picker with configs
    <Step4_Preview />              ← Test the skill before saving
  </SkillBuilderWizard>
```

### State Management

**Contexts:**

```typescript
// frontend/src/contexts/AuthContext.tsx
interface AuthContextType {
  user: User | null;
  loading: boolean;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  getToken: () => Promise<string>;
}

// frontend/src/contexts/SkillContext.tsx
interface SkillContextType {
  skill: SkillConfig | null;
  loading: boolean;
  sessionId: string | null;
  setSessionId: (id: string) => void;
}
```

**Hooks:**

```typescript
// frontend/src/hooks/useAuth.ts
function useAuth(): AuthContextType;

// frontend/src/hooks/useSkill.ts
function useSkill(skillId: string): {
  skill: SkillConfig | null;
  loading: boolean;
  error: Error | null;
};

// frontend/src/hooks/useSkills.ts
function useSkills(filters?: SkillFilters): {
  skills: SkillConfig[];
  loading: boolean;
  hasMore: boolean;
  loadMore: () => void;
};

// frontend/src/hooks/useSkillAgent.ts
function useSkillAgent(skillId: string): {
  messages: Message[];
  sendMessage: (content: string) => Promise<void>;
  isLoading: boolean;
  stop: () => void;
};
```

### API Communication

All backend calls go through `/api/proxy` — the Next.js API route that forwards to the backend.

```typescript
// frontend/src/services/api.ts

const API_BASE = "/api/proxy";

export async function fetchSkills(filters?: SkillFilters): Promise<SkillConfig[]> {
  const params = new URLSearchParams(filters as Record<string, string>);
  const res = await fetch(`${API_BASE}/api/skills?${params}`, {
    headers: { Authorization: `Bearer ${await getToken()}` },
  });
  return res.json();
}

export async function fetchSkill(skillId: string): Promise<SkillConfig> {
  const res = await fetch(`${API_BASE}/api/skills/${skillId}`, {
    headers: { Authorization: `Bearer ${await getToken()}` },
  });
  return res.json();
}

export async function createSkill(config: Partial<SkillConfig>): Promise<SkillConfig> {
  const res = await fetch(`${API_BASE}/api/skills`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${await getToken()}`,
    },
    body: JSON.stringify(config),
  });
  return res.json();
}
```

```typescript
// frontend/src/app/api/proxy/route.ts

import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:1956";

export async function GET(request: NextRequest) {
  const path = request.nextUrl.pathname.replace("/api/proxy", "");
  const res = await fetch(`${BACKEND_URL}${path}${request.nextUrl.search}`, {
    headers: request.headers,
  });
  return new NextResponse(res.body, { status: res.status, headers: res.headers });
}

// POST, PUT, DELETE handlers follow same pattern
```

### Key Components

#### SkillCard

```typescript
// frontend/src/components/skill/SkillCard.tsx

interface SkillCardProps {
  skill: SkillConfig;
  onClick: () => void;
}

// Displays: avatar, name, description, protocol badges, tags
// Protocol badges: AG-UI (always), A2UI, MCP Apps, MCP, A2A
```

#### ChatInterface

```typescript
// frontend/src/components/chat/ChatInterface.tsx

// Main chat UI — wraps ChatMessages + ChatInput
// Uses useSkillAgent() hook for AG-UI streaming
// Handles file upload, message submission, stop generation
```

#### ChatMessages

```typescript
// frontend/src/components/chat/ChatMessages.tsx

// Renders message list with auto-scroll
// Detects A2UI blocks in messages → renders A2UIRenderer
// Detects ui:// URIs in tool results → renders MCPAppFrame
// Shows tool execution indicators (loading, tool name)
```

#### ChatInput

```typescript
// frontend/src/components/chat/ChatInput.tsx

// Text input with:
// - Send button
// - File upload (drag-and-drop + button)
// - Stop generation button (when streaming)
// - Shift+Enter for newline
```

### Styling

- **Radix UI** for accessible primitives (Dialog, Select, Tabs, etc.)
- **Tailwind CSS** for utility-first styling
- **Design tokens** via Tailwind config (colors, spacing, fonts)
- **Dark mode** support via Tailwind `dark:` variants
- Follow v5 visual patterns where appropriate

### Architecture Diagram

```
[Browser]
    │
    ▼
[Next.js App Router]
    │
    ├── /skills ──────────── [SkillList] → fetchSkills() → /api/proxy/api/skills
    │
    ├── /skill/[id] ──────── [AGUIProvider] → SSE to /api/proxy/api/skill/{id}/stream
    │   │                      │
    │   │                      ├── [ChatMessages] → text + A2UI + MCP Apps
    │   │                      └── [ChatInput] → sendMessage()
    │   │
    │   └── [SkillProvider] → fetchSkill(id) → /api/proxy/api/skills/{id}
    │
    ├── /create ──────────── [SkillBuilderWizard] → createSkill() → POST /api/proxy/api/skills
    │
    └── /api/proxy/* ──────── [Backend Proxy] → http://localhost:1956/*
```

## Implementation Plan

### Phase 1: Foundation (~2 days)
- [ ] Implement root `layout.tsx` with providers (Firebase, Auth)
- [ ] Implement `FirebaseProvider` and `AuthProvider`
- [ ] Implement `useAuth` hook and login page
- [ ] Implement `/api/proxy/route.ts` (backend proxy)
- [ ] Implement `services/api.ts` (API client)
- [ ] Implement basic `types/skill.ts` and `types/message.ts`

### Phase 2: Skill Marketplace (~2 days)
- [ ] Implement `SkillCard` component with protocol badges
- [ ] Implement `SkillList` component with grid layout
- [ ] Implement `/skills` page with filtering (by tag, search)
- [ ] Implement `useSkills` hook
- [ ] Style with Radix + Tailwind

### Phase 3: Chat Interface (~3 days)
- [ ] Implement `AGUIProvider` with CopilotKit
- [ ] Implement `SkillProvider` and `useSkill` hook
- [ ] Implement `ChatInterface`, `ChatMessages`, `ChatInput`
- [ ] Implement `useSkillAgent` hook (AG-UI streaming)
- [ ] Implement `A2UIRenderer` component
- [ ] Implement `MCPAppFrame` component
- [ ] Implement `ToolIndicator` component
- [ ] Implement `/skill/[skillId]` page

### Phase 4: Skill Builder (~2 days)
- [ ] Implement `SkillBuilderWizard` with 4 steps
- [ ] Model selector component
- [ ] Tool picker with config forms
- [ ] Instruction editor (textarea with char count)
- [ ] Preview/test before save
- [ ] Implement `/create` page

## Migration & Rollout

**No migration needed** — frontend is greenfield.

**Rollback Plan:** v5 frontend is still deployable from v5 repo.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] AuthContext: provides user state, handles sign-in/sign-out
- [ ] SkillCard: renders skill info, protocol badges, tags
- [ ] ChatInput: sends message on submit, supports file upload
- [ ] useSkillAgent: handles AG-UI events, updates messages
- [ ] A2UIRenderer: renders form, table, chart components
- [ ] API proxy: forwards requests with auth headers

### E2E Tests (Playwright — future)
- [ ] Login → browse skills → open skill → send message → see response
- [ ] Create skill → fill wizard → save → skill appears in marketplace

## Security Considerations

- All API calls include Firebase JWT
- `/api/proxy` validates auth before forwarding
- No secrets in client-side code
- MCP Apps iframes sandboxed (no same-origin access)
- File uploads go through backend (no direct GCS upload from client)

## Performance Considerations

- **Bundle size targets:** <200KB initial JS (excl. CopilotKit ~15KB)
- **Code splitting:** Each page lazy-loaded via Next.js App Router
- **Image optimization:** Next.js `<Image>` for avatars
- **Skill list:** Paginated (20 per page), no infinite scroll of full list
- **Chat:** Virtual scroll for long conversations (react-virtuoso)

## Success Criteria

- [ ] Login works (Google Sign-In)
- [ ] Skill marketplace displays skills with filtering
- [ ] Chat interface streams responses via AG-UI
- [ ] A2UI components render inline
- [ ] Skill builder creates new skills
- [ ] All frontend tests passing
- [ ] Lint and typecheck clean
- [ ] `npm run build` succeeds

## Open Questions

- Should the frontend support offline/PWA features?
- Dark mode by default or follow system preference?
- Should skill builder have a "fork from existing" option?
- How to handle A2UI actions (user clicks a form button) — POST back to agent or handle client-side?

## Related Documents

- [Migration to v6](../v5.0.0/migration-to-v6.md) — Frontend architecture (lines 597-626), client-side strategy (lines 1043-1065)
- [Streaming & Protocols](../streaming-and-protocols.md) — AG-UI, A2UI, MCP Apps integration
- [Skills Data Model](skills-data-model.md) — SkillConfig type definitions
- [Auth & Permissions](auth-and-permissions.md) — Firebase auth flow

---

## Implementation Report

**Completed**: 2026-04-21
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
