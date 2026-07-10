"""The R0 public-presence fetch capability (zero-key floor).

Stdlib HTTP GET + stdlib HTML→text. Registered through the Tool Gateway
so every fetch is a recorded, policy-checked capability call. An HTTP
GET observes the outside world and changes nothing in it: action class
R0.

The keyed upgrade seam: a Firecrawl-grade provider registers its own
capability (e.g. ``firecrawl.scrape``) and
``PublicPresenceSettings.fetch_provider`` / ``fetch_capability`` select
it — suggested, never required; the bootstrap tool, budget, recording,
and injection posture are identical either way.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from packs.tool_gateway.tools import CapabilitySpec, register_local_capability

from .html_text import html_to_text


class FetchPageInput(BaseModel):
    url: str = Field(description="HTTP(S) URL of a public page to fetch")
    timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    max_chars: int = Field(default=200_000, ge=1_000, le=2_000_000)


def _fetch_page(
    url: str = "",
    timeout_seconds: float = 10.0,
    max_chars: int = 200_000,
    execution_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Fetch one public page and reduce it to text (stdlib only)."""
    del execution_context
    from activegraph.tools.context import ToolContext
    from activegraph.tools.web_fetch import WebFetchInput, web_fetch

    # ``live_unrecorded`` is the runtime-tool dimension only: THIS fetch
    # is recorded twice over — the gateway's capability_call/result audit
    # pair, and the acquisition behavior's artifact-mode replay payload.
    ctx = ToolContext(
        behavior_name="public_presence.fetch_page",
        event_id="",
        frame=None,
        idempotency_key="",
        timeout_seconds=timeout_seconds,
        external_io_mode="live_unrecorded",
    )
    out = web_fetch.fn(
        WebFetchInput(url=url, timeout_seconds=timeout_seconds), ctx
    )
    text, title = html_to_text(out.text or "")
    truncated = len(text) > max_chars
    return {
        "url": url,
        "final_url": out.final_url,
        "status": out.status,
        "title": title,
        "text": text[:max_chars],
        "truncated": truncated,
    }


def register_public_presence_capability() -> CapabilitySpec:
    """Register ``public_presence.fetch_page``. Hosts call this at startup."""
    return register_local_capability(
        "public_presence", "fetch_page", _fetch_page,
        input_schema=FetchPageInput,
        description=(
            "Fetch one public web page (profile, personal site) and reduce "
            "it to plain text. Read-only, zero-key, stdlib parsing."
        ),
        risk_class="low",
        action_class="R0",
    )


__all__ = ["FetchPageInput", "register_public_presence_capability"]
