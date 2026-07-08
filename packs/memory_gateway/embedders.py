"""Embedder implementations for the Memory Gateway embedding seam.

The backend defines only the ``Embedder`` protocol (see ``backend.py``); this
module provides the implementations an application can plug in:

  * ``OpenAIEmbedder``          — real semantic vectors via the OpenAI
    embeddings API. Implemented with stdlib ``urllib`` so the pack keeps zero
    embedding dependencies; honors ``OPENAI_BASE_URL`` for proxies.
  * ``HashEmbedder``            — deterministic hashing-trick bag-of-words
    vectors. No key, no network, byte-stable across runs: the embedder for
    fixtures and tests (the same pattern the regimes eval harness uses).
  * ``default_embedder_factory``— the zero-config wiring: returns an
    OpenAIEmbedder when ``OPENAI_API_KEY`` is set, else None (recall stays
    lexical). Applications enable it with::

        from packs.memory_gateway.backend import (
            auto_configure_embedder, set_embedder_factory)
        from packs.memory_gateway.embedders import default_embedder_factory

        set_embedder_factory(default_embedder_factory)
        auto_configure_embedder()   # embedding recall iff a key is present

Nothing in this module runs on import; registration is always an explicit
application-side call (the demo server does it at startup).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from typing import Optional

# SHIM RETIRED (v0.3.1, activegraph >=1.3): the runtime now defines the
# EmbeddingProvider protocol (embed(*, texts, model) + default_model,
# activegraph.llm.embedding) — the seam this pack's ad-hoc Embedder
# protocol anticipated. Both embedders here now implement the RUNTIME
# protocol; retrieval logic stays in this pack as agreed. The backend
# accepts both the runtime shape and the legacy embed(texts) shape, since
# the pack-level seam is public API (see backend._invoke_embedder).


class HashEmbedder:
    """Deterministic hashing-trick embedder: token → sha1 bucket counts.

    Implements the runtime's EmbeddingProvider protocol. Not semantic —
    texts are close iff they share tokens — but deterministic,
    dependency-free, and shaped exactly like a real embedder, which is what
    fixtures and tests need to exercise the vector path with no API key.
    (The runtime ships its own HashEmbeddingProvider test double; this one
    stays because the token-bucket shape gives tests meaningful overlap
    semantics, e.g. the SynonymEmbedder subclass in the retrieval suite.)
    """

    default_model = "hash-256"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, *, texts: list[str], model: str = "") -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                bucket = int(hashlib.sha1(token.encode()).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
            vectors.append(vec)
        return vectors


class OpenAIEmbedder:
    """OpenAI embeddings via stdlib HTTP — no ``openai`` package required.

    Implements the runtime's EmbeddingProvider protocol
    (``embed(*, texts, model)`` + ``default_model``), so it can also be
    handed to ``Runtime(embedding_provider=...)`` where forks inherit it.
    Honors ``OPENAI_BASE_URL`` (proxies, Azure-style gateways) and reads the
    key from ``OPENAI_API_KEY`` unless one is passed explicitly. Errors
    propagate to the caller; the backend's ``_safe_embed`` already converts
    any embedder failure into a lexical fallback, so a flaky network can
    degrade recall quality but never break recall.
    """

    default_model = "text-embedding-3-small"

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.default_model = model  # instance override, protocol-visible
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        base = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        self.base_url = base.rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise ValueError("OpenAIEmbedder requires an API key (OPENAI_API_KEY)")

    def embed(self, *, texts: list[str], model: str = "") -> list[list[float]]:
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps({"model": model or self.default_model,
                             "input": texts}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode())
        # The API returns entries with an index; sort so vectors align with inputs.
        data = sorted(payload["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


def default_embedder_factory() -> Optional[OpenAIEmbedder]:
    """Environment-driven embedder discovery for ``auto_configure_embedder``.

    Returns an OpenAIEmbedder when ``OPENAI_API_KEY`` is present, else None
    (lexical recall). Model overridable via ``ACTIVEGRAPH_EMBEDDING_MODEL``.
    Must not raise — auto_configure swallows exceptions, but be a good citizen.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    model = os.environ.get("ACTIVEGRAPH_EMBEDDING_MODEL", "text-embedding-3-small")
    try:
        return OpenAIEmbedder(model=model)
    except Exception:
        return None
