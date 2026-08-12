# Publishing the platform to Google Cloud Marketplace (Pattern C)

This doc tracks the workstream for listing the platform as a public AI
agent on the [Google Cloud Marketplace](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents),
so any Gemini Enterprise customer can discover and install it without us
running their subscription or sharing IAM with them.

It is the third operational pattern after the two in
[gemini-enterprise.md](./gemini-enterprise.md):

| Pattern | Distribution | Subscription owner | Who sees the agent |
|---|---|---|---|
| **A — register-shared** | We point at someone else's GE engine | Their org | Their org only |
| **B — self-host** | Each fork stands up its own GE app | Each fork operator | That operator's org only |
| **C — Marketplace** *(this doc)* | We publish once; customers install themselves | Each customer's org | Any GE customer who installs us |

Patterns A/B are zero-to-one operations on a specific engine. Pattern C
is a one-time partner enrolment plus a per-release listing update — same
A2A agent card flows through all three, but Pattern C adds a Cloud
Marketplace vendor identity, a Producer Portal listing, a pricing model,
and a customer-account-linking contract.

---

## Status

**Not yet started.** Workstream tracker, not an implementation plan.
Concrete next action is in [Phase 0](#phase-0--decide-whether-to-pursue-this).

| Phase | Owner | Status |
|---|---|---|
| 0. Go/no-go decision | mark | open |
| 1. Vendor enrolment | — | blocked on phase 0 |
| 2. Producer Portal listing | — | blocked on phase 1 |
| 3. Technical integration (account linking) | — | blocked on phase 1 |
| 4. Pricing + review + publish | — | blocked on phases 2+3 |

---

## Phase 0 — Decide whether to pursue this

Marketplace publishing is a heavy commitment: vendor enrolment, billing
integration, a published pricing model, a public review process, and a
sign-in flow that links customer Google accounts to platform accounts.

Do this only when at least one is true:

- A real customer wants to install us through *their* GE Console rather
  than through Pattern A (shared engine) or Pattern B (self-host).
- We're treating Marketplace presence itself as a marketing channel —
  showing up in GE customers' agent picker is the GTM motion.
- A partner contract requires Marketplace billing (the customer pays
  Google, Google pays us).

If none of those is true yet, Patterns A and B cover every fork the
template ships for. Park this doc until a concrete trigger appears.

**Next action:** decide trigger. Until a trigger exists, no further
phases are blocked-but-actionable — they're just blocked.

---

## Phase 1 — Vendor enrolment

One-time Sunholo-level setup. Independent of the platform code.

1. Submit the Cloud Marketplace Project Info Form (via the Google
   contact who manages the preview program — same channel).
2. Receive Producer Portal access for the vendor identity that will
   own the listing (Sunholo? Aitana Labs? — decide before submitting,
   because rename-after-the-fact is painful).
3. Confirm the billing entity that receives Marketplace payouts.

**Open questions:**

- Vendor identity: Sunholo (the org behind the template) or Aitana
  Labs (the product brand)? Probably Aitana Labs since the platform
  is the listed product, but Sunholo owns the upstream A2A
  infrastructure. Pick before form submission.
- Does the existing Holosun Billing account work, or does Marketplace
  payout require a separate billing entity?

---

## Phase 2 — Producer Portal listing

In the Producer Portal, the listing has four data surfaces:

1. **Product details** — name, description, logo, screenshots,
   long-form copy, support contact. We already have the brand assets
   (`backend/assets/` + `frontend/public/`). The logo we ship on the
   agent card today (Sunholo) is probably wrong for an Aitana-branded
   listing — Marketplace presents one face.
2. **Agent card upload** — the same `/.well-known/agent.json` we
   produce for Patterns A/B, but uploaded as a static JSON file to a
   Cloud Storage bucket Marketplace reads. The card's `url` field
   still has to be a publicly reachable deployment — Marketplace
   doesn't host the runtime.
3. **Listing metadata** — categories, tags, supported regions. See
   [ai-agent-metadata](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/ai-agent-metadata).
4. **Pricing** — see Phase 4.

**Open questions:**

- Does Marketplace's agent-card upload require a static snapshot, or
  does it dial the deployed URL the way Discovery Engine does at
  Pattern A/B registration time? If snapshot, we need a CI step that
  publishes the card to GCS on every backend release. If live URL,
  we already satisfy this.

---

## Phase 3 — Technical integration

The piece that doesn't exist in Patterns A/B: when a customer installs
us from Marketplace, Google hands us a *Google identity* and expects us
to create a platform account linked to it.

Required changes (estimate; verify against
[technical-integration](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/technical-integration)):

- **Google sign-in** at the platform front door. We have Firebase Auth
  with Google as a provider already — confirm Marketplace's flow
  accepts Firebase-mediated Google identity or requires direct OAuth.
- **Account provisioning webhook** — Marketplace posts an
  account-creation event when a customer installs; we create the
  matching `users/{uid}` + tenant scaffold without the user having
  to sign up manually.
- **Account linking** for existing platform users who later install
  through Marketplace — UX decision: silently merge, or prompt to
  confirm. Probably prompt; silent merge is a security surprise.
- **Entitlement check** — every request from a Marketplace-installed
  tenant carries a Marketplace entitlement we must validate (and
  refuse service if the entitlement is suspended for non-payment).

This is the heaviest phase by far. Pattern A/B fork operators handle
their own auth; Pattern C means we handle it for every Marketplace
customer.

**Open questions:**

- How do we represent a "Marketplace-installed tenant" in our
  `clients/{domain}` model? New `installSource: "marketplace"` field,
  or separate collection?
- Does Marketplace expect us to enforce the per-seat licence count
  Google sold the customer, or just to accept whatever requests
  arrive? (Almost certainly the former; check
  [pricing-models](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/pricing-models).)

---

## Phase 4 — Pricing, review, publish

### Pricing model

Four options (see
[pricing-models](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/pricing-models)):

| Model | What we charge for | Fit for the platform |
|---|---|---|
| Free | Nothing — customer pays only their own GCP runtime | Workshop / open-source distribution; no revenue |
| Subscription | Flat monthly per seat | Mirrors the per-seat model GE already uses; clean to bill |
| Usage-based | Metered (tokens? skill invocations? documents processed?) | Closest match to actual cost drivers; requires usage reporting wired into the backend |
| Combined | Subscription floor + usage on top | Most flexible; most engineering work |

Pricing review takes ~4 business days (per Google's docs) and runs in
parallel with the rest of the integration.

### Review

Google reviews the listing components together. No published timeline;
expect 2–6 weeks based on the breadth of artefacts being reviewed
(pricing model + agent card + technical integration + listing metadata).

### Activation

On approval, Google hands over a `gcloud` command that flips the listing
public. Until that command runs, the listing is reviewable but invisible.

---

## What we already have

- **A2A v0.2-compliant agent card** at `/.well-known/agent.json` (G43;
  see `docs/design/template/template-a2a-spec-compliance.md`).
- **Card validator** — `scripts/verify-a2a.sh` runs 12 spec checks
  Marketplace's reviewer will almost certainly run.
- **Public deployment story** — `docs/ops/deployed-urls.md` documents
  the live URLs, and per-fork operators already produce a public
  deployment for Patterns A/B.
- **Firebase Auth with Google provider** — partial coverage of
  Phase 3's sign-in requirement.
- **Per-tenant client scaffolding** — `clients/{domain}` Firestore
  model + `aiplatform client` CLI (v6.3.0). Probably extensible to a
  Marketplace install-source.

## What's the gap

- Vendor identity decision (Phase 1).
- Marketplace account-linking webhook + entitlement validation
  (Phase 3) — the only net-new code surface.
- Pricing-model decision + (if usage-based) usage reporting wiring
  (Phase 4).
- An updated logo / brand pack for the listing if we publish as
  Aitana rather than Sunholo (the card currently advertises the
  Sunholo logo per
  [3716cec](https://github.com/sunholo-data/ai-protocol-platform/commit/3716cec)).

---

## References

- [Cloud Marketplace AI Agents — partner overview](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents)
- [Add your AI agent](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/add-product)
- [Product details](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/product-details)
- [Agent card integration](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/agent-card)
- [Listing requirements & metadata](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/ai-agent-metadata)
- [Technical integration](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/technical-integration)
- [Pricing models](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/pricing-models)
- [Publication process](https://docs.cloud.google.com/marketplace/docs/partners/ai-agents/publish)
- [A2A protocol spec](https://a2a-protocol.org/latest/)
- Sibling docs: [gemini-enterprise.md](./gemini-enterprise.md) (Patterns A/B),
  `docs/design/template/template-a2a-spec-compliance.md` (G43).
