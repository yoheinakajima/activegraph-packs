"""Reference capability registrations for the Tool Gateway.

The runtime ships reference *tools* (activegraph.tools.web_fetch); this
module re-exposes them as gateway *capabilities*, so they arrive with the
metadata (schema, risk class, description) that makes them policy-checkable
and LLM-exposable via llm_tools.as_llm_tool. Hosts call these once at
startup; nothing here touches a graph.
"""

from __future__ import annotations

from .tools import CapabilitySpec, register_local_capability


def register_web_fetch_capability(*, risk_class: str = "low") -> CapabilitySpec:
    """Register ``web.fetch_url`` — read-only page fetch, stdlib only.

    Backed by the runtime's reference ``web_fetch`` tool (which ignores its
    ToolContext, so it is safe to invoke directly). Risk class defaults to
    'low' (read-only, no side effects), making it auto-approvable under the
    default gateway policy — the canonical first tool for agentic chat.
    """
    from activegraph.tools.web_fetch import WebFetchInput, web_fetch

    def _fetch(url: str = "", timeout_seconds: float = 10.0) -> dict:
        out = web_fetch.fn(
            WebFetchInput(url=url, timeout_seconds=timeout_seconds), None
        )
        # Size limiting happens at the gateway (max_output_chars) — return
        # the full result and let policy own truncation.
        return {"text": out.text, "status": out.status, "final_url": out.final_url}

    return register_local_capability(
        "web", "fetch_url", _fetch,
        input_schema=WebFetchInput,
        description=(
            "Fetch the text of a public web page by URL (HTTP GET, follows "
            "redirects, read-only)."
        ),
        risk_class=risk_class,
    )
