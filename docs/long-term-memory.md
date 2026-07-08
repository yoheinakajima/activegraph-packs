# Conversation-driven long-term memory

The assistant builds and uses durable, cross-session memory by connecting the
**Chat Pack** to the **Memory Gateway** lifecycle. Out of the box this works
with **no LLM, no API key, and no external service** — and every piece is a
swappable seam.

```
chat turn ──▶ memory_candidate ──▶ evaluation ──▶ memory_item ──▶ (backend)
   write path        contract        governance      stored          recall
                                                                       │
new chat turn ◀── prompt ◀── memory_context ◀── retrieve_memories ◀────┘
   read path
```

There are three independent seams, each described below:

1. **Write path** — how a chat turn becomes a memory candidate.
2. **Backend** — where memories are stored and how they are recalled.
3. **Embeddings** — lexical recall by default, vector recall when you opt in.

---

## 1. The default write path (zero-LLM heuristic)

`chat_memory_proposer` (in `packs/chat/behaviors.py`) inspects each inbound chat
message and, when it states something durable (a preference, instruction,
decision, or first-person fact), emits a `memory_candidate`. It is a small,
explainable keyword heuristic — see `_CHAT_MEMORY_CUES` — chosen so that the
assistant builds memory at **zero cost**.

Core's generic `source → observation → memory_candidate` pipeline also proposes
candidates from the same message. Running both is safe: `memory_writer`
deduplicates by normalized text, so the same statement is never stored twice.

Governance is unchanged: `candidate_evaluator` accepts a candidate when its
confidence clears `MemoryGatewaySettings.acceptance_threshold` (or its category
is in `auto_accept_categories`). Nothing is written directly — everything goes
through the candidate → evaluation → item lifecycle.

### Swapping in a different ingestion strategy

The contract is the `memory_candidate` object. **Any** pack that emits
`memory_candidate` objects feeds the same lifecycle, so you can replace the
heuristic without editing the Chat Pack:

1. Turn the default off:

   ```python
   from packs.chat import ChatSettings
   chat_settings = ChatSettings(memory_write_path="off")
   ```

2. Load your own ingestion pack (an LLM extractor, an entity-extraction pack, a
   mem0 importer, …) whose behavior creates `memory_candidate` objects from
   whatever signal you care about — for example:

   ```python
   graph.add_object("memory_candidate", {
       "text": "Prefers async standups over live meetings.",
       "confidence": 0.9,
       "category": "preference",
       "source_ids": [source_id],
   })
   ```

That's the whole seam — no monkey-patching, no Chat Pack changes.

---

## 1b. Curation: what deserves to be memory (v0.2)

The plumbing decides *how* memories flow; curation decides *what* gets in.
Three guards, all on by default:

- **Questions are not memory.** Both write paths skip interrogatives —
  "What's my favorite color?" is classified as a question and dropped, while
  "My favorite color is green" is stored. (Core checks `?` before keyword
  categories; chat also catches bare interrogatives without punctuation.)
- **Provenance admission — documents don't give orders.** The evaluator
  (the lifecycle's governance point) admits conversational sources freely
  (the reply gate already governs who converses, and the memory is
  subject-scoped to the speaker), but guidance categories
  (instruction/preference/decision) extracted from non-conversational
  content (emails, documents, tool results) are rejected — with a written
  rationale on the evaluation object — unless the sender resolves to a
  trusted principal (owner/admin/collaborator). Enforced only when the
  Identity/Auth Pack has registered principals; without identity, behavior
  is unchanged. Knobs: `MemoryGatewaySettings.provenance_admission`
  ('trusted_senders' | 'off').
- **Frame-scoped recall.** Items record the frame they were born in, and
  chat recall excludes the asking frame (`exclude_frame_id`), so a memory
  created by the current turn can never answer it — a designed invariant,
  not a cascade-ordering accident.

Category priority is also real now: candidates in
`auto_accept_categories` accept at `auto_accept_min_confidence` (default
0.5) instead of the full `acceptance_threshold` (default 0.6).

## 2. Using memory (recall) and the backend

`chat_memory_context` (in `packs/chat/behaviors.py`) runs **before** the LLM
responder on every inbound chat message. It calls `retrieve_memories_fn`,
attaches the top matches to the message as a `memory_context` object, and the
responder's scoped graph view folds that text straight into the prompt — the
same mechanism used for conversation history and the assistant's identity.

It is bounded and configurable via `ChatSettings`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `include_memory` | `True` | Master switch for cross-session recall. |
| `memory_write_path` | `"heuristic"` | `"heuristic"` or `"off"` (see §1). |
| `memory_backend_url` | `":memory:"` | Backend to recall from — **must match** `MemoryGatewaySettings.backend_url`. |
| `memory_top_k` | `3` | Max memories folded into the prompt. |
| `memory_min_score` | `0.1` | Minimum similarity score to recall. |
| `memory_subject_scoped` | `True` | Recall only the message sender's own memories (see below). |
| `memory_include_global` | `False` | When subject-scoped, also recall subject-less "global" memories. |

### Multi-user isolation (subject scoping)

Recalled memory is folded straight into the LLM prompt, so **recall is an
access-control boundary**: in a multi-user deployment one user must never receive
another user's memories. Two mechanisms enforce this:

- **Writes are tagged with their author.** Every memory carries a `subject_ref`
  (the originating `sender_ref`). The heuristic write path sets it directly; for
  candidates from Core's generic extraction path (which don't set it),
  `memory_writer` derives it from the candidate's source object. A memory with no
  resolvable author stays subject-less (genuinely global).
- **Reads are scoped to the sender.** With `memory_subject_scoped=True` (default),
  `chat_memory_context` passes the inbound message's `sender_ref` and only recalls
  memories tagged for that user. `memory_include_global=False` (default) keeps
  recall **strict** — only the sender's own memories — so untagged or legacy
  subject-less rows can never leak across users. Set `memory_include_global=True`
  to also surface shared/global facts to everyone, or `memory_subject_scoped=False`
  for a single-user assistant where every memory is shared.

At the backend, `retrieve_by_query(subject_ref=…, subject_scoped=True,
include_global=…)` applies the same filter, so alternative read paths inherit the
boundary.

### Persistence across sessions

Recall reads the **same backend** that the writer persists to. For memories to
survive a restart, point both at the same SQLite file:

```python
from packs.memory_gateway import MemoryGatewaySettings
from packs.chat import ChatSettings

memory_gateway_settings = MemoryGatewaySettings(backend_url="data/memory.sqlite")
chat_settings           = ChatSettings(memory_backend_url="data/memory.sqlite")
```

The demo server already wires this (`packs/demo_server.py`), so memories written
in one chat session are recalled in the next — even after the server restarts.

### Plugging in an external backend (mem0, Zep, pgvector, Supermemory, …)

The store is a first-class seam. The contract is the `MemoryBackend` protocol
(`packs/memory_gateway/backend.py`); only two methods carry real semantics —
`store_item` (the service's "add memory") and `retrieve_by_query` (its
"search"). Subclass `ExternalMemoryBackend` and the rest (write-path dedup,
LRU eviction, retrieval stats) defaults to safe no-ops, since external stores
handle retention and dedup themselves.

Switching store is one registration plus one settings value — nothing else in
the lifecycle changes:

```python
from packs.memory_gateway.backend import register_backend

register_backend("myscheme", lambda url: MyBackend(url))

MemoryGatewaySettings(backend_url="myscheme://default")
ChatSettings(memory_backend_url="myscheme://default")   # must match
```

**mem0 ships as a working adapter** (`packs/memory_gateway/adapters.py`). It
maps `subject_ref` → mem0 `user_id` (so subject scoping keeps isolating
users), carries `category`/`frame_id`/`item_id` in mem0 metadata (so category
filtering and same-frame exclusion keep working), and clamps mem0's relevance
score onto the shared `[0, 1]` / `min_score` scale:

```python
from packs.memory_gateway.adapters import register_mem0_backend

register_mem0_backend()          # pip install mem0ai; or pass client=...
MemoryGatewaySettings(backend_url="mem0://default")
ChatSettings(memory_backend_url="mem0://default")
```

The adapter accepts any client with the mem0 `add`/`search` surface (OSS
`Memory`, platform `MemoryClient`, or your own), which is also how it is
tested deterministically — `tests/test_memory_backend_registry.py` runs the
full mapping against a fake client, no service required. Use it as the
template for other services.

---

## 3. Recall scoring: hybrid lexical + embeddings

Every item always gets a **lexical score**: `max(Jaccard overlap, query-term
coverage)`. Coverage — the fraction of the *query's* content words present in
the stored text — is what makes short and natural queries work: `"teal"`
against *"my favorite color is teal and I run a bakery called Crumbtown"*
scores 1.0 (Jaccard alone gives ~0.14 and misses the default threshold — the
exact failure the July 2026 evaluation caught). Interrogative words ("what",
"how", "should", …) are treated as stopwords so questions don't dilute their
own coverage. Dependency-free, never errors, no API key.

With an embedder registered, items are embedded at write time and recall adds
a **cosine signal**; an item's score is the **max of the two signals** — a
memory is as relevant as its strongest signal. Embeddings add rephrasing
recall ("what theme do I like?" → *"I prefer dark mode"*) without ever
costing exact-keyword recall, and items without a vector (stored before the
embedder existed, or if embedding fails) simply keep scoring lexically. Both
signals share the `[0, 1]` scale, so one `min_score` governs everything.

Enabling embeddings is one call at startup, using the bundled
environment-driven factory (`packs/memory_gateway/embedders.py`):

```python
from packs.memory_gateway.backend import set_embedder_factory, auto_configure_embedder
from packs.memory_gateway.embedders import default_embedder_factory

set_embedder_factory(default_embedder_factory)
auto_configure_embedder()   # embedding recall iff OPENAI_API_KEY is set
```

The demo server does exactly this — with a key, memory recall is hybrid; with
no key it stays lexical and nothing errors. `OpenAIEmbedder` is implemented
with stdlib HTTP (honors `OPENAI_BASE_URL`, model via
`ACTIVEGRAPH_EMBEDDING_MODEL`), so the pack still has **zero embedding
dependencies**. Any object with `embed(texts) -> list[list[float]]` works via
`set_embedder(...)` — and `HashEmbedder` (deterministic, no network) is what
fixtures and tests use to exercise the vector path.

---

## Verifying it works

- `python packs/fixtures/chat_memory_cross_session.py` — proves a preference
  written in session 1 is recalled in a fresh session 2 sharing a memory file.
- `pytest tests/test_memory_retrieval_quality.py` — the regression suite built
  from the July 2026 evaluation's verified recall failures ("teal", "bakery",
  rephrased questions), plus the hybrid-scoring guarantees.
- `pytest tests/test_memory_backend_registry.py` — proves the external-backend
  seam: scheme registration, the tools path against a custom store, and the
  mem0 adapter mapping (against a fake client, no service needed).
- `pytest tests/test_memory_embedding_seam.py` — proves the embedding seam
  (vector recall, lexical fallback, no-key safety).
