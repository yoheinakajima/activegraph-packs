# Public Presence Bootstrap Fetcher

ADR 0024 rung 1: a gateway-routed, budgeted, **R0** capability that
fetches the owner's shared public handles (GitHub profile, personal
site, X profile page) with the zero-key floor — stdlib HTTP GET +
stdlib HTML→text. No keys, no SDKs.

## How a fetch happens

1. `bootstrap_public_presence` maps free-text handles to URLs
   deterministically and PROPOSES `capability_call` objects — this pack
   never touches the network directly.
2. The Tool Gateway's policy enforcer auto-approves R0 and the executor
   runs `public_presence.fetch_page`; every call, approval, and result
   is a recorded graph object.
3. `acquire_presence_result` lands each result as strict normalizer
   handoffs on the `public_presence` surface: replay payload retained in
   **artifact mode** (CAS), content **injection-scanned** (ADR 0023 —
   labels in normalized metadata), dedup key `presence:<url>`.

## Budget

Hard per-run budget (default ≤ 10 fetches, `max_fetches_per_run`).
Overflow is logged in the `presence_bootstrap_run` ledger with reason
`budget_exhausted` — never silently fetched, never silently dropped.
Handles with no fetch strategy (e.g. `company`) are logged too.

## Category

Fetched self-descriptions land under `local_knowledge` (public knowledge
about the owner). The category set is closed (SCORING_CONTRACT); this
slice does not invent categories.

## The keyed upgrade seam

A Firecrawl-grade fetcher registers its own gateway capability and is
selected via `PublicPresenceSettings.fetch_provider` /
`fetch_capability` — same budget, recording, and injection posture.
Suggested, never required.
