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

### Plugging in an external backend (mem0, pgvector, Supermemory, …)

The backend interface is intentionally tiny (see the docstring in
`packs/memory_gateway/backend.py`):

```
store_item(item_id, text, category, confidence, metadata)
retrieve_by_query(query, top_k, min_score, category) -> list[dict]
find_by_text(text) -> Optional[str]
enforce_limit(max_items)
clear()
```

To integrate an external store (for example **mem0**), implement these methods
against that service and have `get_backend()` return your implementation for the
configured `backend_url`. `store_item` maps to the service's "add memory" call,
`retrieve_by_query` to its "search", and `find_by_text` to a dedup lookup (or a
no-op returning `None` if the service dedups itself). Nothing else in the
lifecycle changes — candidates, evaluation, and the chat behaviors all stay the
same.

---

## 3. Enabling embedding-based recall

Lexical Jaccard overlap is the default: dependency-free, never errors, and works
with no API key. It needs **keyword overlap** between the query and the stored
text, so natural-language questions ("what theme do I like?" vs. a stored "I
prefer dark mode") recall better with embeddings.

Embeddings are a drop-in. Register an embedder — any object with
`embed(texts) -> list[list[float]]` — and the backend automatically embeds new
items at write time and ranks by cosine similarity at recall time, falling back
to lexical for any item without a vector or if embedding fails:

```python
from packs.memory_gateway.backend import set_embedder

class OpenAIEmbedder:
    def embed(self, texts):
        # call your provider; return one vector per input text
        ...

set_embedder(OpenAIEmbedder())   # recall is now embedding-based
```

For automatic activation when a key is present, register a factory and call the
switch once at startup (it never raises — no key means it stays lexical):

```python
from packs.memory_gateway.backend import set_embedder_factory, auto_configure_embedder

def make_embedder():
    import os
    if not os.environ.get("OPENAI_API_KEY"):
        return None            # no key → stay lexical
    return OpenAIEmbedder()

set_embedder_factory(make_embedder)
auto_configure_embedder()
```

We deliberately **do not bundle** an embedding provider, so the library stays
installable and testable with zero dependencies. The seam — `set_embedder` /
`set_embedder_factory` — is all you need to wire one in.

---

## Verifying it works

- `python packs/fixtures/chat_memory_cross_session.py` — proves a preference
  written in session 1 is recalled in a fresh session 2 sharing a memory file.
- `pytest tests/test_memory_embedding_seam.py` — proves the embedding seam
  (vector recall, lexical fallback, no-key safety).
