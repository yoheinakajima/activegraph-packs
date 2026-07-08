#!/usr/bin/env python3
"""ActiveGraph Inspector Demo Server.

Runs an ActiveGraph runtime with the Assistant Bundle loaded and seeded
with demo data.  Exposes a lightweight JSON REST API that the Express
API server proxies to the React Inspector UI.

Usage:
    python packs/demo_server.py [--port PORT]

Port defaults to env var ACTIVEGRAPH_PORT or 7788.
"""

from __future__ import annotations

import os
import sys

# ── sys.path fix ──────────────────────────────────────────────────────────────
# When this file is run as a script, Python inserts the script's directory
# (packs/) into sys.path[0].  That causes packs/email/__init__.py to shadow
# the stdlib 'email' package, breaking http.server (which imports email.utils).
# Remove the packs/ dir and ensure the workspace root is on the path instead.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_workspace = os.path.dirname(_this_dir)
if _this_dir in sys.path:
    sys.path.remove(_this_dir)
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

import json
import threading
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

# ─── Runtime singleton ────────────────────────────────────────────────────────

# ── Runtime executor ──────────────────────────────────────────────────────
# The runtime is single-threaded by design (its SQLite event store is bound
# to the thread that created it), so ONE dedicated thread owns every graph
# touch. Request threads and the schedule tick driver submit closures and
# wait: the socket layer is concurrent (ThreadingHTTPServer — a slow chat
# turn no longer blocks /health at accept()), while graph access stays
# strictly serial. _runtime_lock remains as a re-entrant, contention-free
# lock for handlers that nest (everything already runs on the executor
# thread).


class _RuntimeExecutor:
    def __init__(self) -> None:
        import queue

        self._q: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="runtime-executor", daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, fut = self._q.get()
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except BaseException as e:  # surfaced to the submitting thread
                fut.set_exception(e)

    def run(self, fn):
        """Run fn on the runtime thread and return its result (or raise)."""
        import concurrent.futures

        if threading.current_thread() is self._thread:
            return fn()  # already on the runtime thread — run inline
        fut: "concurrent.futures.Future" = concurrent.futures.Future()
        self._q.put((fn, fut))
        return fut.result()


_EXECUTOR = _RuntimeExecutor()
_runtime_lock = threading.RLock()
_rt = None                  # the live Runtime
_initial_events: list = []  # events captured at startup (for reset)
_frames: dict = {}          # frame_id -> {id, status, started_at, ended_at, events[]}
_chat_config: dict = {}     # last-resolved chat LLM config (mode/provider/model/...)

# Env vars that must never be overwritten via POST /secrets — setting these
# would break the running process. Arbitrary credential names are still allowed.
_RESERVED_ENV_NAMES = frozenset({
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME",
    "HOME", "SHELL", "IFS", "BASH_ENV", "ENV", "PWD", "PORT",
})

# Allowed enum values for the agent-profile editor, mirroring the pydantic
# Literal fields in packs/agent_profile/object_types.py. Validated server-side
# so the editor cannot write a value the assembler would not understand.
_TONES = ("neutral", "warm", "direct", "formal", "casual", "technical")
_VERBOSITIES = ("concise", "balanced", "detailed")
_FORMALITIES = ("informal", "neutral", "formal")
_GOAL_PRIORITIES = ("low", "medium", "high", "critical")
_GOAL_STATUSES = ("active", "paused", "completed", "cancelled")

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_path() -> str:
    """Path to the SQLite event-log file backing the demo runtime.

    ActiveGraph persists the runtime as an append-only event log. We keep
    it under <workspace>/data so it survives process restarts; the run is
    resumed via Runtime.load on the next boot instead of re-seeded.
    Override with the ACTIVEGRAPH_DB env var.
    """
    override = os.environ.get("ACTIVEGRAPH_DB")
    if override:
        return override
    data_dir = os.path.join(_workspace, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "activegraph_demo.sqlite")


def _memory_db_path() -> str:
    """Path to the SQLite file backing the Memory Gateway's stored items.

    Separate from the event-log store: the Memory Gateway keeps its own
    SQLite backend. Pointing it at a file (instead of the default
    ``:memory:``) makes stored memories durable across restarts too.
    Override with the ACTIVEGRAPH_MEMORY_DB env var.
    """
    override = os.environ.get("ACTIVEGRAPH_MEMORY_DB")
    if override:
        return override
    data_dir = os.path.join(_workspace, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "activegraph_memory.sqlite")


_mcp_settings_cache = None
_mcp_discovered: dict = {}
_mcp_gateway = None
_chat_allow_list: list = []


def _current_allow_list() -> list:
    """The live chat tool allow-list (set by _build_runtime)."""
    return list(_chat_allow_list)


def _get_mcp_settings():
    """Build MCPSettings from the environment (cached).

    ACTIVEGRAPH_MCP_TOKENS  — inbound bearer tokens: 'tok1:you@x.com,tok2:agent:foo'
                              (token:identifier pairs; identifier may contain colons).
    ACTIVEGRAPH_MCP_SERVERS — outbound servers as a JSON list (see MCPSettings.servers).
    ACTIVEGRAPH_MCP_EXPOSE  — comma-separated gateway capability keys offered inbound
                              (default: the chat tool allow-list).
    """
    global _mcp_settings_cache
    if _mcp_settings_cache is not None:
        return _mcp_settings_cache
    from packs.mcp import MCPSettings

    tokens = {}
    for pair in os.environ.get("ACTIVEGRAPH_MCP_TOKENS", "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        token, identifier = pair.split(":", 1)
        if token.strip() and identifier.strip():
            tokens[token.strip()] = identifier.strip()

    servers = []
    raw_servers = os.environ.get("ACTIVEGRAPH_MCP_SERVERS", "").strip()
    if raw_servers:
        try:
            parsed = json.loads(raw_servers)
            if isinstance(parsed, list):
                servers = parsed
        except json.JSONDecodeError:
            print("[demo_server] ACTIVEGRAPH_MCP_SERVERS is not valid JSON — "
                  "no outbound MCP servers connected", flush=True)

    expose_env = os.environ.get("ACTIVEGRAPH_MCP_EXPOSE", "").strip()
    expose = ([k.strip() for k in expose_env.split(",") if k.strip()]
              if expose_env
              else ["web.fetch_url", "schedule.create_reminder"])

    _mcp_settings_cache = MCPSettings(
        tokens=tokens,
        servers=servers,
        expose_capabilities=expose,
        memory_backend_url=_memory_db_path(),
    )
    return _mcp_settings_cache


def _token_db_path() -> str:
    """Path to the SQLite file holding managed OAuth tokens.

    Deliberately separate from the event log and memory stores: those may
    be exported or inspected; this file holds secret VALUES and must never
    be. Override with ACTIVEGRAPH_TOKEN_DB."""
    override = os.environ.get("ACTIVEGRAPH_TOKEN_DB")
    if override:
        return override
    data_dir = os.path.join(_workspace, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "activegraph_tokens.sqlite")


_oauth_source = None
_oauth_pending: dict = {}  # credential_name -> {"flow": ..., "device_code": ...}


def _get_oauth_source():
    """The managed OAuth credential source, registered once behind the
    Secrets Pack resolution chain (env still wins)."""
    global _oauth_source
    if _oauth_source is None:
        from packs.secrets.managed import (
            OAuthCredentialSource,
            OAuthDeviceFlow,
            OAuthTokenStore,
            register_credential_source,
        )
        store = OAuthTokenStore(_token_db_path())
        _oauth_source = OAuthCredentialSource(store)
        # Rebuild refresh flows for tokens stored in earlier sessions: the
        # provider column carries the flow config (endpoints + client id)
        # as JSON, so refresh keeps working across restarts.
        for name in store.names():
            record = store.get(name) or {}
            try:
                config = json.loads(record.get("provider") or "")
                _oauth_source.add_flow(name, OAuthDeviceFlow(
                    provider=config["provider"],
                    client_id=config["client_id"],
                    client_secret=config.get("client_secret", ""),
                    device_authorization_endpoint=config["device_authorization_endpoint"],
                    token_endpoint=config["token_endpoint"],
                    scope=config.get("scope", ""),
                ))
            except Exception:
                pass  # env-style or legacy row: resolvable until expiry, no refresh
        register_credential_source(_oauth_source)
    return _oauth_source


def _evolution_enabled() -> bool:
    """Self-modification is opt-in: ACTIVEGRAPH_EVOLUTION=1."""
    return os.environ.get("ACTIVEGRAPH_EVOLUTION", "").strip() == "1"


def _store_has_run(path: str) -> bool:
    """True if `path` is an existing SQLite store with at least one run."""
    if not os.path.exists(path):
        return False
    try:
        from activegraph.store import SQLiteEventStore
        return SQLiteEventStore.most_recent_run_id(path) is not None
    except Exception:
        return False


def _wipe_store(path: str) -> list[str]:
    """Delete the SQLite store and its WAL/SHM sidecars.

    Returns a list of paths that could not be removed (empty on full success)
    so callers can surface a failure instead of silently leaving stale data.
    """
    failed: list[str] = []
    for p in (path, path + "-wal", path + "-shm"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            failed.append(p)
    return failed


def _seed_demo(rt) -> None:
    """Add the initial demo objects to a fresh runtime."""
    rt.graph.add_object("source", {
        "kind": "chat_message",
        "content": "What is the current deal status for Northwind Robotics?",
        "channel": "chat",
        "sender_ref": "user:alice",
    })
    rt.graph.add_object("source", {
        "kind": "email",
        "content": "Please review the attached term sheet for the Series B round.",
        "channel": "email",
        "sender_ref": "founder@northwind.ai",
    })
    rt.graph.add_object("source", {
        "kind": "meeting_note",
        "content": "Kickoff call with Northwind team. Strong ARR traction, 3x YoY.",
        "channel": "internal",
        "sender_ref": "user:bob",
    })
    rt.graph.add_object("source", {
        "kind": "url",
        "content": "https://arxiv.org/abs/2312.00752",
        "channel": "api",
        "sender_ref": "user:alice",
    })


def _register_demo_capabilities():
    """Register the demo's gateway capabilities (idempotent).

    web.fetch_url — read-only page fetch: live chat grounds answers in a
    real page while every fetch is a recorded, policy-checked call.
    schedule.create_reminder — "remind me tomorrow at 9am" as a one-turn,
    policy-governed chat tool call (the gateway hands the handler the graph
    via execution_context at execution time).
    """
    from packs.schedule.capabilities import register_reminder_capability
    from packs.telegram.capabilities import register_send_capability as register_telegram_send
    from packs.tool_gateway.capabilities import register_web_fetch_capability
    from packs.whatsapp.capabilities import register_send_capability as register_whatsapp_send

    register_web_fetch_capability()
    register_reminder_capability()
    # Messenger delivery capabilities: registration is name-only (tokens are
    # resolved by the Secrets Pack at execution time), so registering without
    # credentials configured is harmless — sends just fail with the fix named.
    register_telegram_send()
    register_whatsapp_send(
        phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
    )
    # MCP: the governed exposure-editing capability (high risk → owner
    # approval) and any outbound MCP servers configured in the environment.
    # Outbound registration happens HERE (before chat settings are built) so
    # discovered mcp_<server>.<tool> keys can join the chat allow-list.
    from packs.mcp.server import register_set_exposure_capability
    register_set_exposure_capability()

    global _mcp_discovered
    mcp_settings = _get_mcp_settings()
    if mcp_settings.servers:
        from packs.mcp.registry import register_configured_servers
        _mcp_discovered = register_configured_servers(mcp_settings, graph=None)
        for server_name, keys in _mcp_discovered.items():
            print(f"[demo_server] MCP outbound '{server_name}': "
                  f"{len(keys)} tools registered"
                  f"{' (UNREACHABLE)' if not keys else ''}", flush=True)


def _build_runtime():
    """Build the runtime, resuming from the SQLite event log if one exists.

    On first boot (no store) we build a fresh assistant backed by a durable
    SQLite store and seed the demo objects — those writes are persisted. On
    every subsequent boot we resume the most recent run from the same store
    via Runtime.load (which replays the event log to rebuild graph state),
    then re-register the bundle packs so future events fire behaviors. This
    means chat history and any added objects survive restarts instead of
    being re-seeded from scratch.
    """
    from activegraph import Runtime
    from bundles import (
        build_messaging_assistant,
        load_messaging_packs,
        seed_owner_principals,
    )
    from packs.identity_auth import IdentitySettings
    from packs.identity_auth.behaviors import rebuild_principal_registry
    from packs.memory_gateway import MemoryGatewaySettings
    from packs.chat import ChatSettings

    from packs.chat.llm import select_chat_provider

    db = _db_path()
    # Retention housekeeping BEFORE the runtime attaches (retire/compact
    # are offline operations per the runtime's contract): archive trial
    # forks nothing wants anymore. Promoted-from forks are pinned by the
    # runtime's retention API and stay, as provenance.
    if _evolution_enabled() and os.path.exists(db):
        from packs.evolution.boot import retire_unpinned_trial_forks
        try:
            housekeeping = retire_unpinned_trial_forks(db)
            retired = sum(1 for v in housekeeping.values()
                          if v.startswith("retired"))
            if housekeeping:
                print(f"[demo_server] Evolution retention: {retired} trial "
                      f"fork(s) retired, "
                      f"{len(housekeeping) - retired} kept", flush=True)
        except Exception as exc:
            print(f"[demo_server] Evolution retention skipped: {exc}",
                  flush=True)
    mem_settings = MemoryGatewaySettings(backend_url=_memory_db_path())
    # Long-term memory recall (chat_memory_context) must query the SAME backend
    # memory_writer persists to, so point ChatSettings.memory_backend_url at the
    # same SQLite file. This is what makes cross-session recall work: memories
    # written in one session are retrieved in the next, even across a restart.
    #
    # tool_allow_list makes demo chat AGENTIC: the responder may call the
    # web.fetch_url gateway proxy (registered below) in the native LLM tool
    # loop. Every such call is recorded/policy-checked/sanitized by the Tool
    # Gateway. In mock mode the provider never requests tools, so this is
    # inert without an API key.
    _register_demo_capabilities()
    # Reply gating (identity on the respond path): ACTIVEGRAPH_OWNER is a
    # comma-separated list of owner identifiers (emails/handles); the seeded
    # principals make the fail-closed 'known' / 'owner_only' policies usable
    # from the first message. Default policy stays 'open' for the demo.
    owner_refs = [s.strip() for s in os.environ.get("ACTIVEGRAPH_OWNER", "").split(",") if s.strip()]
    identity_settings = IdentitySettings(owner_identifiers=owner_refs)
    # Chat tool allow-list: the base capabilities, the governed MCP-exposure
    # editor (so the assistant can PROPOSE changes to its own MCP surface —
    # high risk, always held for owner approval), and every tool discovered
    # from configured outbound MCP servers (high-risk ones are held on call;
    # that is the designed UX for untrusted breadth).
    mcp_tool_keys = [k for keys in _mcp_discovered.values() for k in keys]
    chat_settings = ChatSettings(
        memory_backend_url=_memory_db_path(),
        tool_allow_list=(
            ["web.fetch_url", "schedule.create_reminder", "mcp.set_exposure",
             "catalog.search"]
            + mcp_tool_keys
        ),
        reply_policy=os.environ.get("ACTIVEGRAPH_REPLY_POLICY", "open"),
    )
    # The capability catalog: the agent queries what exists (and what its
    # allow-list grants) through a governed low-risk call instead of
    # memorizing tool names. The module-level allow-list keeps allowed_now
    # live for both the agent's catalog.search and GET /capabilities.
    global _chat_allow_list
    _chat_allow_list = list(chat_settings.tool_allow_list)
    from packs.tool_gateway.catalog import register_catalog_capability
    register_catalog_capability(_current_allow_list)
    resuming = _store_has_run(db)

    # Resolve the chat LLM provider from the environment (live if a provider
    # key is present, MockChatProvider otherwise). The runtime owns the LLM
    # lifecycle, so the provider must be attached at construction on BOTH the
    # fresh and the resume path.
    provider, info = select_chat_provider()
    global _chat_config
    _chat_config = info
    print(f"[demo_server] Chat LLM: mode={info['mode']} "
          f"provider={info['provider']} model={info.get('model')}", flush=True)

    # Memory recall quality: switch the memory backend to hybrid
    # lexical+embedding scoring when an embedding provider is configured in
    # the environment (OPENAI_API_KEY). With no key this is a no-op and
    # recall stays lexical — the demo must never require a key. Memories
    # stored before an embedder existed have no vector and keep scoring
    # lexically; new writes are embedded from here on.
    from packs.memory_gateway.backend import (
        auto_configure_embedder,
        set_embedder_factory,
    )
    from packs.memory_gateway.embedders import default_embedder_factory
    set_embedder_factory(default_embedder_factory)
    _embedder = auto_configure_embedder()
    print(f"[demo_server] Memory recall: "
          f"{'hybrid (lexical + embeddings)' if _embedder else 'lexical'}",
          flush=True)

    if resuming:
        rt = Runtime.load(db, llm_provider=provider)
        load_messaging_packs(
            rt,
            memory_gateway_settings=mem_settings,
            chat_settings=chat_settings,
            identity_settings=identity_settings,
        )
        # Replay rebuilds graph objects without firing behaviors, so the
        # in-memory principal dedup registry is empty — repopulate it from
        # the replayed principals to avoid creating duplicates on the next
        # message from an already-known sender.
        n = rebuild_principal_registry(rt.graph)
        # Same problem for the profile registry: replay didn't fire the profile
        # recorders, so chat_profile_context would find no profile to assemble.
        # Rebuild it from the replayed profile objects (incl. the seeded default).
        from packs.agent_profile.behaviors import rebuild_profile_registry
        from bundles import seed_default_profile
        rebuild_profile_registry(rt.graph)
        # Stores created before self-knowledge existed have no profile; seed one
        # now (idempotent — skips if the resumed store already has a profile).
        seed_default_profile(rt)
        # Owner principals: idempotent (register_principal_fn dedups against
        # the just-rebuilt registry), so newly configured owners get seeded
        # and already-seeded ones are left alone.
        seed_owner_principals(rt, identity_settings=identity_settings)
        print(f"[demo_server] Resumed run {rt.run_id} from {db} "
              f"({n} principals re-indexed)", flush=True)
    else:
        rt = build_messaging_assistant(
            persist_to=db,
            memory_gateway_settings=mem_settings,
            chat_settings=chat_settings,
            identity_settings=identity_settings,
            llm_provider=provider,
        )
        print(f"[demo_server] Fresh run {rt.run_id} persisting to {db}", flush=True)

    # Attach a listener to collect frame events
    def _on_evt(evt):
        fid = getattr(evt, "frame_id", None)
        if fid and fid not in _frames:
            _frames[fid] = {
                "id": fid,
                "status": "running",
                "frame_type": "behavior",
                "started_at": _ts(),
                "ended_at": None,
                "event_count": 0,
                "events": [],
            }
        if fid and fid in _frames:
            _frames[fid]["event_count"] += 1
            _frames[fid]["events"].append(_event_to_dict(evt))
            if evt.type in ("frame.completed", "frame.failed", "runtime.idle"):
                _frames[fid]["status"] = "completed" if evt.type != "frame.failed" else "failed"
                _frames[fid]["ended_at"] = _ts()

    rt.graph.add_listener(_on_evt)

    # Seed demo objects only on a fresh store; a resumed run already has
    # them (and any later additions) replayed from the event log.
    if not resuming:
        _seed_demo(rt)

    # Represent any env / Replit-Secret provider keys as name-only
    # credential_refs in the graph (values are never read here).
    _ensure_provider_credential_refs(rt.graph)

    # Managed auth: register the OAuth token store behind the Secrets Pack
    # resolution chain (env still wins) so previously connected accounts
    # resolve, with refresh, from the first request after a restart.
    _get_oauth_source()

    # ── Evolution (opt-in: ACTIVEGRAPH_EVOLUTION=1) ─────────────────────────
    # Self-modification is never ambient. When enabled: load the pack,
    # register the governed adoption capabilities (registration REFUSES
    # without a verified approver, so ACTIVEGRAPH_OWNER must be set), and
    # re-load previously adopted packs from the graph (bundle-hash checked).
    if _evolution_enabled():
        from packs.evolution import pack as evolution_pack, EvolutionSettings
        from packs.evolution.adopt import register_adoption_capabilities
        from packs.evolution.boot import reload_adopted_packs
        from packs.tool_gateway import ToolGatewaySettings

        rt.load_pack(evolution_pack, settings=EvolutionSettings(enabled=True))
        try:
            register_adoption_capabilities(
                gateway_settings=ToolGatewaySettings(), graph=rt.graph)
            reloaded = reload_adopted_packs(rt)
            print(f"[demo_server] Evolution: ON, adopted packs: "
                  f"{reloaded or 'none'}", flush=True)
        except ValueError as exc:
            print(f"[demo_server] Evolution: adoption registration refused "
                  f"({exc})", flush=True)

    # ── MCP (bidirectional) ─────────────────────────────────────────────────
    # Load the pack (object types for exposure rules + audit records), seed
    # the fail-closed default exposures (idempotent — operator edits win on
    # resume), record any outbound discovery in the graph, and build the
    # inbound gateway mounted at POST /mcp.
    from packs.mcp import pack as mcp_pack
    from packs.mcp.server import MCPGateway, ensure_default_exposures

    mcp_settings = _get_mcp_settings()
    rt.load_pack(mcp_pack, settings=mcp_settings)
    ensure_default_exposures(rt.graph, mcp_settings)
    for server_name, keys in _mcp_discovered.items():
        try:
            rt.graph.add_object("mcp_server", {
                "name": server_name,
                "direction": "outbound",
                "capability_keys": keys,
                "status": "connected" if keys else "unreachable",
                "connected_at": _ts(),
            })
        except Exception:
            pass

    global _mcp_gateway
    _mcp_gateway = MCPGateway(
        lambda: _get_rt().graph,
        mcp_settings,
        chat_fn=_mcp_chat_fn,
        memory_fn=_mcp_memory_fn,
    )
    if mcp_settings.tokens:
        print(f"[demo_server] MCP inbound: {len(mcp_settings.tokens)} token(s) "
              f"configured, POST /mcp is live", flush=True)
    else:
        print("[demo_server] MCP inbound: no tokens configured "
              "(set ACTIVEGRAPH_MCP_TOKENS) — POST /mcp will refuse calls",
              flush=True)

    rt.run_until_idle()

    # Arm the gateway's registration enforcement LAST: every trusted
    # boot registration above is done, and from here on any native
    # register_local_capability call (including from a hot-loaded,
    # agent-authored pack) is checked against graph-derived pack
    # declarations: undeclared pairs, risk drift, and disabled packs'
    # surfaces all refuse (Q8 chain step 3).
    from packs.tool_gateway.registration_check import (
        arm_registration_enforcement,
    )
    arm_registration_enforcement(rt.graph)
    return rt


def _mcp_chat_fn(message: str, user_ref: str, session_id=None) -> dict:
    """Drive one chat turn for an inbound MCP caller.

    Mirrors POST /chat's pipeline (submit_chat_input → full cascade → read
    the ChatTurn), with the caller's resolved identifier as user_ref so
    identity, reply gating, memory scoping, and persona shaping treat the
    MCP caller exactly like any other sender. Caller must hold
    _runtime_lock (RLock — the /mcp handler does)."""
    from packs.chat.tools import submit_chat_input_fn

    rt = _get_rt()
    frame_id = str(uuid.uuid4())
    submit_chat_input_fn(
        rt.graph,
        user_ref=user_ref,
        content=message,
        session_id=session_id,
        frame_id=frame_id,
        metadata={"via": "mcp"},
    )
    rt.run_until_idle()

    turns = [
        o for o in rt.graph.all_objects()
        if o.type == "chat_turn" and (o.data or {}).get("frame_id") == frame_id
    ]
    turns.sort(key=lambda t: (t.data or {}).get("turn_number", 0))
    turn = turns[-1] if turns else None
    reply = ((turn.data.get("assistant_message") if turn else None) or "").strip()
    return {
        "content": reply or "No assistant reply was produced for this message.",
        "session_id": (turn.data or {}).get("session_id") if turn else session_id,
    }


def _mcp_memory_fn(query: str, subject_ref: str, top_k: int = 5) -> list:
    """Subject-scoped memory search for an inbound MCP caller."""
    from packs.memory_gateway.tools import retrieve_memories_fn

    return retrieve_memories_fn(
        query,
        top_k=top_k,
        min_score=0.1,
        backend_url=_memory_db_path(),
        subject_ref=subject_ref,
        subject_scoped=True,
        include_global=True,
    )


def _get_rt():
    global _rt
    if _rt is None:
        with _runtime_lock:
            if _rt is None:
                # Constructed ON the runtime executor thread: the SQLite
                # event store binds to its creating thread, and every later
                # graph touch happens on that same thread via _EXECUTOR.
                _rt = _EXECUTOR.run(_build_runtime)
    return _rt


def _reset_rt() -> list[str]:
    """Reset the runtime to the initial demo state.

    Returns a list of store paths that could not be deleted (empty on success)
    so the caller can report a partial reset rather than silently succeeding
    while stale data survives.
    """
    global _rt, _frames
    with _runtime_lock:
        _frames = {}
        # Close the live store handle, then wipe the persisted event log so
        # the rebuild starts from a fresh, re-seeded run rather than
        # resuming the old one.
        if _rt is not None and getattr(_rt.graph, "store", None) is not None:
            try:
                _rt.graph.store.close()
            except Exception:
                pass
        # Clear module-level dedup state so the re-seed produces the full
        # initial graph (these caches otherwise persist within the process
        # and would suppress re-created principals / memory items). Clearing
        # the memory backends also closes their SQLite connections so the
        # files below can be deleted.
        try:
            from packs.identity_auth.behaviors import clear_principal_registry
            clear_principal_registry()
        except Exception:
            pass
        try:
            from packs.memory_gateway.backend import clear_all_backends
            clear_all_backends()
        except Exception:
            pass
        failed = _wipe_store(_db_path()) + _wipe_store(_memory_db_path())
        _rt = _build_runtime()
        return failed


def _ensure_provider_credential_refs(graph) -> int:
    """Register a name-only ``credential_ref`` for every provider key present
    in the environment (env / Replit Secrets), regardless of how it was set.

    The task requires that a credential supplied via env or Replit Secrets is
    represented in the graph the same way a Secrets-page entry is — a name-only
    reference, never the value. Idempotent: skips names already registered.
    Returns the number of refs added.
    """
    from packs.chat.llm import SUPPORTED_PROVIDERS, provider_key_env

    existing = {
        (o.data or {}).get("name")
        for o in graph.all_objects()
        if o.type == "credential_ref"
    }
    added = 0
    for pid in SUPPORTED_PROVIDERS:
        env = provider_key_env(pid)
        if env and os.environ.get(env) and env not in existing:
            graph.add_object("credential_ref", {
                "name": env,
                "scope": "read",
                "provider_hint": pid,
            })
            existing.add(env)
            added += 1
    return added


def _refresh_chat_provider() -> dict:
    """Re-resolve the chat LLM provider from the current environment and
    hot-swap it onto the live runtime.

    Called after a key/model/provider change (Secrets or /chat/config) so chat
    upgrades to a real LLM — or downgrades back to mock — without a restart.
    The runtime reads ``self.llm_provider`` at call time, so reassigning it is
    enough; chat_llm_responder uses model=None, so no re-validation is needed.
    """
    global _chat_config
    from packs.chat.llm import select_chat_provider

    provider, info = select_chat_provider()
    with _runtime_lock:
        if _rt is not None:
            _rt.llm_provider = provider
            # A newly-detected env/Replit key should also appear in the graph
            # as a name-only credential_ref, not just page-entered ones.
            _ensure_provider_credential_refs(_rt.graph)
        _chat_config = info
    return info


def _chat_config_payload() -> dict:
    """Public, secret-free view of the chat LLM configuration."""
    from packs.chat.llm import SUPPORTED_PROVIDERS, provider_key_env

    labels = {"openai": "OpenAI", "anthropic": "Anthropic"}
    providers = []
    for pid in SUPPORTED_PROVIDERS:
        env = provider_key_env(pid)
        providers.append({
            "id": pid,
            "label": labels.get(pid, pid.title()),
            "key_env": env,
            "key_present": bool(env and os.environ.get(env)),
        })
    return {
        "mode": _chat_config.get("mode", "mock"),
        "provider": _chat_config.get("provider"),
        "model": _chat_config.get("model"),
        "key_present": _chat_config.get("key_present", False),
        "providers": providers,
    }


def _secrets_payload(graph) -> dict:
    """Secret-free view of registered credentials.

    Lists name-only ``credential_ref`` objects from the graph plus whether the
    matching environment value is currently present in-process. Secret VALUES
    are never read or returned here.
    """
    credentials = []
    for o in graph.all_objects():
        if o.type != "credential_ref":
            continue
        d = o.data or {}
        name = d.get("name")
        credentials.append({
            "id": str(o.id),
            "name": name,
            "provider_hint": d.get("provider_hint"),
            "scope": d.get("scope"),
            "value_present": bool(name and os.environ.get(str(name))),
            "last_used_at": d.get("last_used_at"),
            "use_count": d.get("use_count", 0),
        })
    credentials.sort(key=lambda c: c.get("name") or "")
    return {"credentials": credentials, "total": len(credentials)}


# ─── Agent profile helpers ────────────────────────────────────────────────────

def _active_profile_id(graph) -> Any:
    """Return the id of the active AgentProfile (or the first one), else None.

    graph.all_objects() is safe here — the demo server runs these reads at
    request time, outside any behavior context.
    """
    profiles = [o for o in graph.all_objects() if o.type == "agent_profile"]
    for o in profiles:
        if (o.data or {}).get("active"):
            return str(o.id)
    return str(profiles[0].id) if profiles else None


def _owned_profile_object(graph, oid: str, expected_type: str, pid: str):
    """Return the object iff it exists, is ``expected_type`` and belongs to ``pid``.

    Guards the goal/instruction update & delete endpoints against caller-supplied
    IDs that point at unrelated objects (other types, or another profile's data).
    Returns None when the object is missing, the wrong type, or owned by a
    different profile so callers can reject with a 404.
    """
    obj = graph.get_object(str(oid))
    if obj is None or obj.type != expected_type:
        return None
    if (obj.data or {}).get("profile_id") != pid:
        return None
    return obj


def _profile_payload(graph) -> dict:
    """Assemble the editor view of the agent's identity from graph objects.

    Returns the active AgentProfile plus its global PersonalityProfile, goals,
    and standing instructions (all filtered by profile_id). When no profile
    exists, ``exists`` is False so the UI can offer to seed the default.
    """
    pid = _active_profile_id(graph)
    if not pid:
        return {
            "exists": False,
            "profile": None,
            "personality": None,
            "goals": [],
            "instructions": [],
        }

    profile_obj = graph.get_object(pid)
    pdata = (profile_obj.data if profile_obj else {}) or {}
    profile = {
        "id": pid,
        "name": pdata.get("name", ""),
        "mission": pdata.get("mission", ""),
        "personality_description": pdata.get("personality_description", ""),
        "owner_name": pdata.get("owner_name"),
        "version": str(pdata.get("version", "1")),
        "active": bool(pdata.get("active", True)),
    }

    goals: list[dict] = []
    instructions: list[dict] = []
    personality: dict | None = None
    for o in graph.all_objects():
        d = o.data or {}
        if d.get("profile_id") != pid:
            continue
        if o.type == "goal":
            goals.append({
                "id": str(o.id),
                "text": d.get("text", ""),
                "priority": d.get("priority", "medium"),
                "status": d.get("status", "active"),
                "domain": d.get("domain"),
            })
        elif o.type == "standing_instruction":
            instructions.append({
                "id": str(o.id),
                "text": d.get("text", ""),
                "scope": d.get("scope", "global"),
                "priority": int(d.get("priority", 50)),
                "active": bool(d.get("active", True)),
                "applies_to_channel": d.get("applies_to_channel"),
                "applies_to_audience_role": d.get("applies_to_audience_role"),
            })
        elif o.type == "personality_profile":
            is_global = (
                d.get("applies_to_channel") is None
                and d.get("applies_to_audience_role") is None
            )
            # Prefer the global (unscoped) personality; fall back to any.
            if personality is None or is_global:
                cand = {
                    "id": str(o.id),
                    "tone": d.get("tone", "neutral"),
                    "verbosity": d.get("verbosity", "balanced"),
                    "formality": d.get("formality", "neutral"),
                }
                if is_global or personality is None:
                    personality = cand

    return {
        "exists": True,
        "profile": profile,
        "personality": personality,
        "goals": goals,
        "instructions": instructions,
    }


def _global_personality_obj(graph, profile_id: str):
    """Return the unscoped (global) personality_profile object for a profile."""
    for o in graph.all_objects():
        if o.type != "personality_profile":
            continue
        d = o.data or {}
        if (
            d.get("profile_id") == profile_id
            and d.get("applies_to_channel") is None
            and d.get("applies_to_audience_role") is None
        ):
            return o
    return None


# ─── Serialisation helpers ────────────────────────────────────────────────────

def _event_to_dict(evt) -> dict:
    payload = {}
    try:
        p = evt.payload
        if p is not None:
            payload = p if isinstance(p, dict) else vars(p)
    except Exception:
        pass

    # Infer pack / behavior / object info from payload or actor
    pack_name = None
    behavior_name = None
    object_type = None
    object_id = None

    try:
        actor = getattr(evt, "actor", None) or {}
        if isinstance(actor, dict):
            pack_name = actor.get("pack")
            behavior_name = actor.get("behavior")
        if isinstance(payload, dict):
            object_type = payload.get("object_type") or payload.get("type")
            object_id = payload.get("object_id") or payload.get("id")
    except Exception:
        pass

    return {
        "id": str(evt.id),
        "event_type": str(evt.type),
        "timestamp": str(evt.timestamp) if evt.timestamp else _ts(),
        "pack": pack_name,
        "behavior_name": behavior_name,
        "frame_id": str(evt.frame_id) if evt.frame_id else None,
        "object_type": object_type,
        "object_id": str(object_id) if object_id else None,
        "payload": _safe_json(payload),
    }


def _object_to_dict(obj) -> dict:
    pack_name = "unknown"
    try:
        # object id format: "<type>#<n>"  e.g. "source#1"
        t = str(obj.type)
        # try to look up which pack declares this type
        rt = _rt
        if rt:
            for p in rt.loaded_packs():
                for ot in p.object_types:
                    if ot.name == t:
                        pack_name = p.name
                        break
    except Exception:
        pass

    data = {}
    try:
        raw = obj.data
        if raw is not None:
            data = raw if isinstance(raw, dict) else vars(raw)
        data = _safe_json(data)
    except Exception:
        pass

    created_at = None
    try:
        provenance = obj.provenance
        if provenance:
            created_at = str(provenance.get("timestamp", ""))
    except Exception:
        pass

    return {
        "id": str(obj.id),
        "type": str(obj.type),
        "pack": pack_name,
        "data": data,
        "created_at": created_at,
    }


def _relation_to_dict(rel) -> dict:
    data = {}
    try:
        raw = rel.data
        if raw is not None:
            data = raw if isinstance(raw, dict) else vars(raw)
        data = _safe_json(data)
    except Exception:
        pass
    # Relation fields are exactly what they say: source/target are object
    # ids, type is the relation type label. (An earlier comment here claimed
    # the fields were shuffled — it was decoding malformed relations created
    # by add_relation calls with the arguments in the wrong order, since
    # fixed across all packs.)
    return {
        "id": str(rel.id),
        "type": str(rel.type),
        "source_id": str(rel.source),
        "target_id": str(rel.target),
        "data": data,
    }


def _pack_to_dict(pack) -> dict:
    behaviors = []
    for b in pack.behaviors:
        behaviors.append({
            "name": b.name,
            "trigger": str(b.on[0]) if b.on else None,
            "description": None,
            "creates": list(b.creates) if b.creates else [],
            "capabilities": [],
        })

    object_types = []
    for ot in pack.object_types:
        desc = None
        try:
            desc = ot.schema.__doc__
            if desc:
                desc = desc.strip().split("\n")[0]
        except Exception:
            pass
        object_types.append({"name": ot.name, "description": desc})

    relation_types = []
    try:
        for rt_type in pack.relation_types:
            relation_types.append({
                "name": rt_type.name,
                "source_types": list(rt_type.source_types) if rt_type.source_types else [],
                "target_types": list(rt_type.target_types) if rt_type.target_types else [],
                "description": rt_type.description if hasattr(rt_type, "description") else None,
            })
    except Exception:
        pass

    return {
        "name": pack.name,
        "version": str(pack.version),
        "description": pack.description if hasattr(pack, "description") else None,
        "object_types": object_types,
        "relation_types": relation_types,
        "behaviors": behaviors,
    }


def _safe_json(obj: Any) -> Any:
    """Recursively convert obj to JSON-safe types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(i) for i in obj]
    try:
        return str(obj)
    except Exception:
        return None


# ─── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # suppress default access logs (noisy)
        pass

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, msg: str, status: int = 500):
        self._send_json({"error": msg}, status)

    def _send_html(self, text: str, status: int = 200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _wants_html(self) -> bool:
        return "text/html" in (self.headers.get("Accept") or "")

    def _parse_qs(self) -> dict:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return {k: v[0] for k, v in qs.items()}

    def _path(self) -> str:
        return urlparse(self.path).path

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self._path()
        qs = self._parse_qs()
        try:
            # All graph access happens on the runtime executor thread; the
            # request thread only parses the request and (after the closure
            # returns) has already had its response written.
            _EXECUTOR.run(lambda: self._dispatch_get(path, qs))
        except Exception as e:
            traceback.print_exc()
            self._send_error(str(e), 500)

    def _dispatch_get(self, path, qs):
        if True:
            if path == "/trace":
                self._handle_trace(qs)
            elif path == "/graph":
                self._handle_graph(qs)
            elif path == "/packs":
                self._handle_packs()
            elif path == "/frames":
                self._handle_frames()
            elif path == "/summary":
                self._handle_summary()
            elif path == "/chat/config":
                self._handle_chat_config_get()
            elif path == "/secrets":
                self._handle_secrets_get()
            elif path == "/profile":
                self._handle_profile_get()
            elif path == "/approvals":
                self._handle_approvals_get()
            elif path == "/approvals/review":
                self._handle_approvals_review_get(qs)
            elif path == "/sessions":
                self._handle_sessions_get()
            elif path == "/channels/whatsapp/webhook":
                self._handle_whatsapp_verify(qs)
            elif path == "/capabilities":
                self._handle_capabilities_get()
            elif path == "/health":
                self._send_json({"status": "ok"})
            else:
                self._send_error("Not found", 404)

    def do_POST(self):
        path = self._path()
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        try:
            _EXECUTOR.run(lambda: self._dispatch_post(path, body))
        except Exception as e:
            traceback.print_exc()
            self._send_error(str(e), 500)

    def _dispatch_post(self, path, body):
        if True:
            if path == "/chat":
                self._handle_chat(body)
            elif path == "/reset":
                self._handle_reset()
            elif path == "/chat/config":
                self._handle_chat_config_post(body)
            elif path == "/secrets":
                self._handle_secrets_post(body)
            elif path == "/profile":
                self._handle_profile_post(body)
            elif path == "/profile/seed":
                self._handle_profile_seed()
            elif path == "/profile/personality":
                self._handle_profile_personality_post(body)
            elif path == "/profile/goal":
                self._handle_profile_goal_post(body)
            elif path == "/profile/goal/delete":
                self._handle_profile_goal_delete(body)
            elif path == "/profile/instruction":
                self._handle_profile_instruction_post(body)
            elif path == "/profile/instruction/delete":
                self._handle_profile_instruction_delete(body)
            elif path == "/approvals":
                self._handle_approvals_post(body)
            elif path == "/channels/telegram/update":
                self._handle_telegram_update(body)
            elif path == "/channels/whatsapp/webhook":
                self._handle_whatsapp_webhook(body)
            elif path == "/mcp":
                self._handle_mcp(body)
            elif path == "/secrets/oauth/start":
                self._handle_oauth_start(body)
            elif path == "/secrets/oauth/poll":
                self._handle_oauth_poll(body)
            else:
                self._send_error("Not found", 404)

    # ── POST /secrets/oauth/start + /secrets/oauth/poll ─────────────────────
    #
    # OAuth 2.0 Device Authorization Grant (RFC 8628), the managed-auth path
    # behind the Secrets Pack's resolve_credential_fn. start begins the flow
    # and returns the verification URL + user code for the OWNER to visit;
    # poll exchanges the device code once the owner approved, and stores the
    # token in the token DB (never the graph). After that, any capability
    # whose credential_ref_name matches resolves through the managed source,
    # with the same SecretUsageEvent audit trail as env credentials.

    def _handle_oauth_start(self, body: dict):
        from packs.secrets.managed import OAuthDeviceFlow

        required = ["credential_name", "client_id",
                    "device_authorization_endpoint", "token_endpoint"]
        missing = [k for k in required if not body.get(k)]
        if missing:
            self._send_error(f"missing fields: {', '.join(missing)}", 400)
            return
        flow = OAuthDeviceFlow(
            provider=body.get("provider", body["credential_name"]),
            client_id=body["client_id"],
            client_secret=body.get("client_secret", ""),
            device_authorization_endpoint=body["device_authorization_endpoint"],
            token_endpoint=body["token_endpoint"],
            scope=body.get("scope", ""),
        )
        started = flow.start()
        name = body["credential_name"]
        _oauth_pending[name] = {"flow": flow, "device_code": started["device_code"]}
        self._send_json({
            "credential_name": name,
            "verification_uri": started.get("verification_uri")
            or started.get("verification_url", ""),
            "user_code": started.get("user_code", ""),
            "interval": started.get("interval", 5),
            "expires_in": started.get("expires_in"),
            "next": "POST /secrets/oauth/poll {\"credential_name\": ...} after approving",
        })

    def _handle_oauth_poll(self, body: dict):
        name = (body.get("credential_name") or "").strip()
        pending = _oauth_pending.get(name)
        if not pending:
            self._send_error(f"no pending OAuth flow for {name!r}", 404)
            return
        flow = pending["flow"]
        outcome = flow.poll(pending["device_code"])
        if outcome["status"] == "pending":
            self._send_json({"status": "pending", "error": outcome.get("error")})
            return
        if outcome["status"] == "error":
            _oauth_pending.pop(name, None)
            self._send_json({"status": "error", "error": outcome.get("error")})
            return
        token = outcome["token"]
        source = _get_oauth_source()
        # provider holds the flow config as JSON (endpoints + client id, no
        # token material) so refresh still works after a server restart.
        flow_config = json.dumps({
            "provider": flow.provider,
            "client_id": flow.client_id,
            "client_secret": flow.client_secret,
            "device_authorization_endpoint": flow.device_authorization_endpoint,
            "token_endpoint": flow.token_endpoint,
            "scope": flow.scope,
        })
        source.store.put(
            name,
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            expires_in=token.get("expires_in"),
            token_type=token.get("token_type", "Bearer"),
            scope=token.get("scope", ""),
            provider=flow_config,
        )
        source.add_flow(name, flow)
        _oauth_pending.pop(name, None)
        # The value stays in the token store; the response confirms only
        # that resolution now works.
        self._send_json({"status": "connected", "credential_name": name,
                         "expires_in": token.get("expires_in")})

    # ── GET /capabilities ────────────────────────────────────────────────────
    #
    # The capability catalog for humans and the Inspector: every registered
    # capability with risk class, origin (native vs MCP-derived), and
    # whether the chat allow-list currently grants it.

    def _handle_capabilities_get(self):
        _get_rt()  # ensure registrations have happened
        from packs.tool_gateway.catalog import catalog_entries

        entries = catalog_entries(allow_list=_current_allow_list())
        self._send_json({"count": len(entries), "capabilities": entries})

    # ── POST /mcp ────────────────────────────────────────────────────────────
    #
    # The assistant AS an MCP server (streamable-HTTP, plain-JSON responses):
    # initialize / tools/list / tools/call over JSON-RPC 2.0. Auth is a
    # bearer token from ACTIVEGRAPH_MCP_TOKENS; exposure is decided by the
    # graph-native mcp_exposure rules; every call lands in the audit trail.

    def _handle_mcp(self, body: dict):
        _get_rt()  # ensure the runtime (and _mcp_gateway) exist
        if _mcp_gateway is None:
            self._send_error("MCP gateway not initialized", 503)
            return
        token = None
        auth_header = self.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        with _runtime_lock:
            response = _mcp_gateway.handle_jsonrpc(body, token)
        if response is None:  # notification — accepted, no body
            self.send_response(202)
            self.end_headers()
            return
        self._send_json(response)

    # ── GET /trace ─────────────────────────────────────────────────────────

    def _handle_trace(self, qs: dict):
        rt = _get_rt()
        limit = int(qs.get("limit", 200))
        offset = int(qs.get("offset", 0))
        pack_filter = qs.get("pack")
        frame_filter = qs.get("frame_id")
        type_filter = qs.get("event_type")

        events = [_event_to_dict(e) for e in rt.graph.events]

        if pack_filter:
            events = [e for e in events if e.get("pack") == pack_filter]
        if frame_filter:
            events = [e for e in events if e.get("frame_id") == frame_filter]
        if type_filter:
            events = [e for e in events if type_filter in e.get("event_type", "")]

        total = len(events)
        page = events[offset : offset + limit]

        self._send_json({
            "events": page,
            "total": total,
            "offset": offset,
            "limit": limit,
        })

    # ── GET /graph ─────────────────────────────────────────────────────────

    def _handle_graph(self, qs: dict):
        rt = _get_rt()
        pack_filter = qs.get("pack")

        objects = [_object_to_dict(o) for o in rt.graph.all_objects()]
        relations = [_relation_to_dict(r) for r in rt.graph.all_relations()]

        if pack_filter:
            objects = [o for o in objects if o.get("pack") == pack_filter]
            obj_ids = {o["id"] for o in objects}
            relations = [r for r in relations
                         if r["source_id"] in obj_ids or r["target_id"] in obj_ids]

        self._send_json({
            "objects": objects,
            "relations": relations,
            "object_count": len(objects),
            "relation_count": len(relations),
        })

    # ── GET /packs ─────────────────────────────────────────────────────────

    def _handle_packs(self):
        rt = _get_rt()
        packs = [_pack_to_dict(p) for p in rt.loaded_packs()]
        self._send_json({"packs": packs, "total": len(packs)})

    # ── GET /frames ────────────────────────────────────────────────────────

    def _handle_frames(self):
        frames = list(_frames.values())
        # Sort newest-first
        frames.sort(key=lambda f: f.get("started_at") or "", reverse=True)
        self._send_json({"frames": frames, "total": len(frames)})

    # ── GET /summary ───────────────────────────────────────────────────────

    def _handle_summary(self):
        rt = _get_rt()
        objects = rt.graph.all_objects()
        relations = rt.graph.all_relations()
        events = rt.graph.events
        packs = rt.loaded_packs()

        # Count by type + pack
        type_counts: dict[str, dict] = {}
        for o in objects:
            key = f"{o.type}|{_object_to_dict(o)['pack']}"
            if key not in type_counts:
                type_counts[key] = {"type": str(o.type), "pack": _object_to_dict(o)['pack'], "count": 0}
            type_counts[key]["count"] += 1

        self._send_json({
            "object_count": len(objects),
            "relation_count": len(relations),
            "event_count": len(events),
            "pack_count": len(packs),
            "frame_count": len(_frames),
            "by_type": list(type_counts.values()),
            "runtime_ready": True,
        })

    # ── POST /chat ─────────────────────────────────────────────────────────

    def _handle_chat(self, body: dict):
        rt = _get_rt()
        content = (body.get("content") or "").strip()
        if not content:
            self._send_error("content is required", 400)
            return

        # Drive the REAL chat pack pipeline:
        #   submit_chat_input → chat_ingester (Source + CommMessage + ChatSession
        #   + ChatTurn) → chat_llm_responder (@llm_behavior, native LLM) →
        #   CommResponseCandidate → chat_responder (writes ChatTurn.assistant_message).
        from packs.chat.tools import submit_chat_input_fn

        user_ref = body.get("user_ref") or "user:inspector"
        session_id = body.get("session_id")
        frame_id = str(uuid.uuid4())

        # Serialized against the schedule tick driver (and any other writer):
        # one runtime, one writer at a time.
        with _runtime_lock:
            objects_before = set(o.id for o in rt.graph.all_objects())
            events_before = len(rt.graph.events)

            inp = submit_chat_input_fn(
                rt.graph,
                user_ref=user_ref,
                content=content,
                session_id=session_id,
                frame_id=frame_id,
            )

            rt.run_until_idle()

        objects_after = rt.graph.all_objects()
        new_obj_ids = [str(o.id) for o in objects_after if o.id not in objects_before]
        events_after = len(rt.graph.events)

        # Register this as a frame for the Inspector's frame view.
        _frames[frame_id] = {
            "id": frame_id,
            "status": "completed",
            "frame_type": "chat",
            "started_at": _ts(),
            "ended_at": _ts(),
            "event_count": events_after - events_before,
            "events": [_event_to_dict(e) for e in rt.graph.events[events_before:]],
        }

        # The assistant's reply lives on the ChatTurn produced this frame.
        turns = [
            o for o in objects_after
            if o.type == "chat_turn" and (o.data or {}).get("frame_id") == frame_id
        ]
        turns.sort(key=lambda t: (t.data or {}).get("turn_number", 0))
        turn = turns[-1] if turns else None
        reply = ((turn.data.get("assistant_message") if turn else None) or "").strip()
        if not reply:
            reply = "No assistant reply was produced for this message."

        resolved_session = None
        if turn:
            resolved_session = (turn.data or {}).get("session_id")

        self._send_json({
            "content": reply,
            "frame_id": frame_id,
            "user_message": content,
            "session_id": resolved_session or session_id,
            "turn_id": str(turn.id) if turn else None,
            "llm_mode": _chat_config.get("mode", "mock"),
            "event_count": events_after - events_before,
            "new_objects": new_obj_ids,
        })

    # ── GET/POST /approvals ─────────────────────────────────────────────────
    #
    # The graph is the single source of truth for approval state: pending ==
    # capability_call at status='policy_checking'; decisions are
    # capability_approval / capability_denial objects. These endpoints are
    # thin views over the Tool Gateway tools — no server-side bookkeeping.
    #
    # Browsers (Accept: text/html) get the review surface instead of raw
    # JSON: the index lists held calls, and evolution adoptions link to
    # /approvals/review — the one-page render of the proposal, its full
    # source diff, gates, trial, and flags (packs/evolution/review.py).
    # "The owner approved it" must mean "the owner read it"; a JSON blob
    # is not a diff-review surface. API clients still get JSON.

    def _handle_approvals_get(self):
        rt = _get_rt()
        from packs.tool_gateway.tools import pending_approvals_fn

        if self._wants_html():
            from packs.evolution.review import render_approvals_index_html
            self._send_html(render_approvals_index_html(rt.graph))
            return

        def _decision_rows(obj_type: str, ts_key: str) -> list[dict]:
            rows = []
            try:
                for o in rt.graph.objects(type=obj_type):
                    rows.append({
                        "id": str(o.id),
                        "call_id": o.data.get("call_id"),
                        "capability_name": o.data.get("capability_name", ""),
                        "provider_name": o.data.get("provider_name", ""),
                        ts_key: o.data.get(ts_key),
                        "decided_by": o.data.get("approver") or o.data.get("denier"),
                        "policy_decision": o.data.get("policy_decision"),
                        "reason": o.data.get("reason"),
                    })
            except Exception:
                pass
            rows.sort(key=lambda r: r.get(ts_key) or "", reverse=True)
            return rows[:20]

        self._send_json({
            "pending": pending_approvals_fn(rt.graph),
            "recent_approvals": _decision_rows("capability_approval", "approved_at"),
            "recent_denials": _decision_rows("capability_denial", "denied_at"),
        })

    def _handle_approvals_review_get(self, qs: dict):
        rt = _get_rt()
        from packs.evolution.review import build_review, render_review_html

        proposal_id = (qs.get("proposal_id") or "").strip()
        call_id = (qs.get("call_id") or "").strip()
        if call_id and not proposal_id:
            call = rt.graph.get_object(call_id)
            if call is not None:
                proposal_id = str((call.data.get("input_data") or {})
                                  .get("proposal_id", ""))
        if not proposal_id:
            self._send_error("proposal_id (or call_id) is required", 400)
            return
        try:
            review = build_review(rt.graph, proposal_id)
        except KeyError as exc:
            self._send_error(str(exc), 404)
            return
        if self._wants_html():
            self._send_html(render_review_html(review))
        else:
            self._send_json(review)

    def _handle_approvals_post(self, body: dict):
        rt = _get_rt()
        from packs.tool_gateway.tools import approve_capability_fn, deny_capability_fn

        call_id = (body.get("call_id") or "").strip()
        decision = (body.get("decision") or "").strip().lower()
        approver_ref = (body.get("approver_ref") or "user:inspector").strip()

        if not call_id:
            self._send_error("call_id is required", 400)
            return
        if decision not in ("approve", "deny"):
            self._send_error("decision must be 'approve' or 'deny'", 400)
            return

        with _runtime_lock:
            if decision == "approve":
                outcome = approve_capability_fn(
                    rt.graph, call_id, approver_ref=approver_ref,
                    note=body.get("note", ""),
                )
            else:
                outcome = deny_capability_fn(
                    rt.graph, call_id, approver_ref=approver_ref,
                    reason=body.get("reason", ""),
                )

            if outcome.get("ok"):
                # Let call_executor react to the approval (denials settle
                # instantly; running to idle is harmless and branch-free).
                rt.run_until_idle()

        if not outcome.get("ok"):
            self._send_json({"ok": False, "reason": outcome.get("reason")}, 409)
            return

        try:
            call = rt.graph.get_object(call_id)
            final_status = call.data.get("status") if call else None
        except Exception:
            final_status = None

        self._send_json({
            "ok": True,
            "decision": decision,
            "call_id": call_id,
            "call_status": final_status,
            "approval_id": outcome.get("approval_id"),
            "denial_id": outcome.get("denial_id"),
        })

    # ── Channel webhooks ─────────────────────────────────────────────────────
    #
    # Thin edges: normalize the wire payload via the adapter pack's submit
    # tool, settle the runtime, respond. Everything conversational is graph
    # behavior. (The Telegram long-poller — python -m packs.telegram.poller —
    # posts to /channels/telegram/update.)

    def _handle_telegram_update(self, body: dict):
        rt = _get_rt()
        from packs.telegram.tools import submit_telegram_update_fn

        with _runtime_lock:
            created = submit_telegram_update_fn(rt.graph, body)
            rt.run_until_idle()
        self._send_json({"ok": True, "ingested": bool(created)})

    def _handle_whatsapp_verify(self, qs: dict):
        """Meta's webhook setup handshake: echo hub.challenge when the
        verify token matches WHATSAPP_VERIFY_TOKEN."""
        expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        if expected and qs.get("hub.mode") == "subscribe" \
                and qs.get("hub.verify_token") == expected:
            challenge = (qs.get("hub.challenge") or "").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(challenge)))
            self.end_headers()
            self.wfile.write(challenge)
            return
        self._send_error("verification failed", 403)

    def _handle_whatsapp_webhook(self, body: dict):
        rt = _get_rt()
        from packs.whatsapp.tools import submit_whatsapp_webhook_fn

        with _runtime_lock:
            created = submit_whatsapp_webhook_fn(rt.graph, body)
            rt.run_until_idle()
        # Meta expects a fast 200; the reply (if any) goes out via the
        # gateway send capability, not this response.
        self._send_json({"ok": True, "ingested": len(created)})

    # ── GET /sessions ────────────────────────────────────────────────────────

    def _handle_sessions_get(self):
        rt = _get_rt()
        sessions = []
        try:
            for o in rt.graph.objects(type="chat_session"):
                sessions.append({
                    "session_id": str(o.id),
                    "user_ref": o.data.get("user_ref"),
                    "status": o.data.get("status"),
                    "turn_count": o.data.get("turn_count", 0),
                    "started_at": o.data.get("started_at"),
                })
        except Exception:
            pass
        sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
        self._send_json({"sessions": sessions, "count": len(sessions)})

    # ── GET/POST /chat/config ───────────────────────────────────────────────

    def _handle_chat_config_get(self):
        _get_rt()  # ensure the runtime (and _chat_config) is initialised
        self._send_json(_chat_config_payload())

    def _handle_chat_config_post(self, body: dict):
        """Select the chat provider/model. Persists only NON-SECRET prefs
        (provider id + model name) into the process env, then hot-swaps the
        live provider. Secret values are set via POST /secrets, never here.
        """
        from packs.chat.llm import SUPPORTED_PROVIDERS

        _get_rt()
        provider = body.get("provider")
        model = body.get("model")

        if provider is not None:
            provider = str(provider).strip().lower()
            if provider and provider not in SUPPORTED_PROVIDERS:
                self._send_error(
                    f"Unsupported provider '{provider}'. "
                    f"Supported: {', '.join(SUPPORTED_PROVIDERS)}.",
                    400,
                )
                return
            if provider:
                os.environ["CHAT_LLM_PROVIDER"] = provider
            else:
                os.environ.pop("CHAT_LLM_PROVIDER", None)

        if model is not None:
            model = str(model).strip()
            if model:
                os.environ["CHAT_LLM_MODEL"] = model
            else:
                os.environ.pop("CHAT_LLM_MODEL", None)

        _refresh_chat_provider()
        self._send_json(_chat_config_payload())

    # ── GET/POST /secrets ───────────────────────────────────────────────────

    def _handle_secrets_get(self):
        rt = _get_rt()
        self._send_json(_secrets_payload(rt.graph))

    def _handle_secrets_post(self, body: dict):
        """Register a secret by NAME and set its value in the process env only.

        SECURITY: the value is written to os.environ for in-process use and a
        name-only ``credential_ref`` is recorded in the graph. The value is
        NEVER written to the graph, the event log, disk, or the response.
        """
        rt = _get_rt()
        name = (body.get("name") or "").strip().upper()
        value = body.get("value")
        provider_hint = (body.get("provider_hint") or "").strip().lower() or None

        if not name:
            self._send_error("name is required", 400)
            return
        if not value:
            self._send_error("value is required", 400)
            return
        # Allow arbitrary credential names ("allows more"), but refuse to
        # overwrite system-critical variables — a stray write to PATH or
        # PYTHONPATH would break the running process.
        if name in _RESERVED_ENV_NAMES:
            self._send_error(
                f"'{name}' is a reserved system variable and cannot be set here.",
                400,
            )
            return

        # Set the value for in-process use ONLY. Never persisted.
        os.environ[name] = str(value)

        # Record a name-only reference in the graph if not already present.
        existing = [
            o for o in rt.graph.all_objects()
            if o.type == "credential_ref" and (o.data or {}).get("name") == name
        ]
        if not existing:
            rt.graph.add_object("credential_ref", {
                "name": name,
                "scope": "read",
                "provider_hint": provider_hint,
            })

        # A new key may upgrade chat from mock → live (or change provider).
        _refresh_chat_provider()

        payload = _secrets_payload(rt.graph)
        payload["chat_config"] = _chat_config_payload()
        self._send_json(payload)

    # ── GET/POST /profile ───────────────────────────────────────────────────

    def _handle_profile_get(self):
        rt = _get_rt()
        self._send_json(_profile_payload(rt.graph))

    def _profile_settle(self, rt):
        """Settle a profile mutation and keep the in-memory registry in sync.

        patch_object / remove_object do NOT fire the registry recorder
        behaviors (those only react to object.created), so after any write we
        rebuild the local profile registry from graph state — otherwise chat
        context assembly would serve stale identity after an edit or delete.
        """
        from packs.agent_profile.behaviors import rebuild_profile_registry

        rt.run_until_idle()
        rebuild_profile_registry(rt.graph)

    def _handle_profile_seed(self):
        """Create the seeded default AgentProfile when none exists (empty state)."""
        from bundles import seed_default_profile

        rt = _get_rt()
        with _runtime_lock:
            seed_default_profile(rt)
            self._profile_settle(rt)
        self._send_json(_profile_payload(rt.graph))

    def _handle_profile_post(self, body: dict):
        """Update the active AgentProfile's identity fields."""
        rt = _get_rt()
        pid = _active_profile_id(rt.graph)
        if not pid:
            self._send_error("No profile exists yet — create one first.", 404)
            return

        updates: dict = {}
        for field in ("name", "mission", "personality_description", "owner_name"):
            if field in body:
                v = body.get(field)
                if field == "owner_name":
                    updates[field] = (str(v).strip() or None) if v is not None else None
                else:
                    updates[field] = "" if v is None else str(v)

        if "name" in updates and not updates["name"].strip():
            self._send_error("name cannot be empty", 400)
            return

        with _runtime_lock:
            if updates:
                rt.graph.patch_object(pid, updates)
                self._profile_settle(rt)
        self._send_json(_profile_payload(rt.graph))

    def _handle_profile_personality_post(self, body: dict):
        """Upsert the global (unscoped) PersonalityProfile for the active profile."""
        rt = _get_rt()
        pid = _active_profile_id(rt.graph)
        if not pid:
            self._send_error("No profile exists yet — create one first.", 404)
            return

        tone = (body.get("tone") or "neutral")
        verbosity = (body.get("verbosity") or "balanced")
        formality = (body.get("formality") or "neutral")
        if tone not in _TONES:
            self._send_error(f"tone must be one of {', '.join(_TONES)}", 400)
            return
        if verbosity not in _VERBOSITIES:
            self._send_error(f"verbosity must be one of {', '.join(_VERBOSITIES)}", 400)
            return
        if formality not in _FORMALITIES:
            self._send_error(f"formality must be one of {', '.join(_FORMALITIES)}", 400)
            return

        with _runtime_lock:
            existing = _global_personality_obj(rt.graph, pid)
            if existing is not None:
                rt.graph.patch_object(
                    str(existing.id),
                    {"tone": tone, "verbosity": verbosity, "formality": formality},
                )
            else:
                rt.graph.add_object("personality_profile", {
                    "tone": tone,
                    "verbosity": verbosity,
                    "formality": formality,
                    "applies_to_channel": None,
                    "applies_to_audience_role": None,
                    "profile_id": pid,
                    "metadata": {},
                })
            self._profile_settle(rt)
        self._send_json(_profile_payload(rt.graph))

    def _handle_profile_goal_post(self, body: dict):
        """Create or update a Goal (update when an ``id`` is supplied)."""
        rt = _get_rt()
        pid = _active_profile_id(rt.graph)
        if not pid:
            self._send_error("No profile exists yet — create one first.", 404)
            return

        gid = body.get("id")
        text = (body.get("text") or "").strip()
        priority = (body.get("priority") or "medium")
        status = (body.get("status") or "active")
        domain = body.get("domain")
        domain = (str(domain).strip() or None) if domain is not None else None

        if not text:
            self._send_error("text is required", 400)
            return
        if priority not in _GOAL_PRIORITIES:
            self._send_error(f"priority must be one of {', '.join(_GOAL_PRIORITIES)}", 400)
            return
        if status not in _GOAL_STATUSES:
            self._send_error(f"status must be one of {', '.join(_GOAL_STATUSES)}", 400)
            return

        if gid and not _owned_profile_object(rt.graph, str(gid), "goal", pid):
            self._send_error("Goal not found for the active profile.", 404)
            return

        with _runtime_lock:
            if gid:
                rt.graph.patch_object(str(gid), {
                    "text": text,
                    "priority": priority,
                    "status": status,
                    "domain": domain,
                })
            else:
                rt.graph.add_object("goal", {
                    "text": text,
                    "priority": priority,
                    "status": status,
                    "domain": domain,
                    "profile_id": pid,
                    "metadata": {},
                })
            self._profile_settle(rt)
        self._send_json(_profile_payload(rt.graph))

    def _handle_profile_goal_delete(self, body: dict):
        rt = _get_rt()
        gid = body.get("id")
        if not gid:
            self._send_error("id is required", 400)
            return
        pid = _active_profile_id(rt.graph)
        if not pid or not _owned_profile_object(rt.graph, str(gid), "goal", pid):
            self._send_error("Goal not found for the active profile.", 404)
            return
        with _runtime_lock:
            rt.graph.remove_object(str(gid))
            self._profile_settle(rt)
        self._send_json(_profile_payload(rt.graph))

    def _handle_profile_instruction_post(self, body: dict):
        """Create or update a StandingInstruction (update when an ``id`` is supplied)."""
        rt = _get_rt()
        pid = _active_profile_id(rt.graph)
        if not pid:
            self._send_error("No profile exists yet — create one first.", 404)
            return

        iid = body.get("id")
        text = (body.get("text") or "").strip()
        scope = (body.get("scope") or "global").strip() or "global"
        active = body.get("active")
        active = True if active is None else bool(active)
        channel = body.get("applies_to_channel")
        channel = (str(channel).strip() or None) if channel is not None else None
        role = body.get("applies_to_audience_role")
        role = (str(role).strip() or None) if role is not None else None

        try:
            priority = int(body.get("priority", 50))
        except (TypeError, ValueError):
            self._send_error("priority must be an integer between 0 and 100", 400)
            return

        if not text:
            self._send_error("text is required", 400)
            return
        if not (0 <= priority <= 100):
            self._send_error("priority must be between 0 and 100", 400)
            return

        if iid and not _owned_profile_object(rt.graph, str(iid), "standing_instruction", pid):
            self._send_error("Instruction not found for the active profile.", 404)
            return

        with _runtime_lock:
            if iid:
                rt.graph.patch_object(str(iid), {
                    "text": text,
                    "scope": scope,
                    "priority": priority,
                    "active": active,
                    "applies_to_channel": channel,
                    "applies_to_audience_role": role,
                })
            else:
                rt.graph.add_object("standing_instruction", {
                    "text": text,
                    "scope": scope,
                    "priority": priority,
                    "active": active,
                    "applies_to_channel": channel,
                    "applies_to_audience_role": role,
                    "profile_id": pid,
                    "metadata": {},
                })
            self._profile_settle(rt)
        self._send_json(_profile_payload(rt.graph))

    def _handle_profile_instruction_delete(self, body: dict):
        rt = _get_rt()
        iid = body.get("id")
        if not iid:
            self._send_error("id is required", 400)
            return
        pid = _active_profile_id(rt.graph)
        if not pid or not _owned_profile_object(rt.graph, str(iid), "standing_instruction", pid):
            self._send_error("Instruction not found for the active profile.", 404)
            return
        with _runtime_lock:
            rt.graph.remove_object(str(iid))
            self._profile_settle(rt)
        self._send_json(_profile_payload(rt.graph))

    # ── POST /reset ────────────────────────────────────────────────────────

    def _handle_reset(self):
        failed = _reset_rt()
        if failed:
            self._send_json({
                "success": False,
                "message": "Runtime re-seeded, but some store files could not be deleted; stale data may remain.",
                "undeleted_paths": failed,
            })
        else:
            self._send_json({"success": True, "message": "Runtime reset to initial demo state."})


# ─── Schedule tick driver ────────────────────────────────────────────────────
#
# The Schedule Pack owns no clock — THIS is the edge where wall-clock time
# enters the graph, exactly the way chat input does. A daemon thread sweeps
# every SCHEDULE_TICK_SECONDS: emit_due_ticks creates schedule_tick objects
# for due schedules (idempotent), and run_until_idle lets tick_router /
# schedule_bookkeeper — and everything they cascade into — react. All
# runtime access is serialized through _runtime_lock, shared with the HTTP
# handlers.

def _tick_driver_loop(period_seconds: float):
    import time as _time
    from datetime import datetime, timezone

    from packs.schedule.tools import emit_due_ticks_fn

    def _sweep():
        rt = _get_rt()
        ticks = emit_due_ticks_fn(rt.graph, datetime.now(timezone.utc))
        if ticks:
            rt.run_until_idle()
        # Evolution phase two (when enabled): adoption/disable tickets are
        # applied here, BETWEEN frames, on the single runtime-executor
        # thread — exactly the out-of-frame guarantee the design requires.
        # sweep_evolution wraps ticket processing with the CAPPED conflict
        # retry: repeated promote conflicts park the proposal at
        # needs_owner instead of looping forever.
        if _evolution_enabled():
            from packs.evolution.chassis import sweep_evolution
            from packs.evolution.settings import EvolutionSettings
            outcomes = sweep_evolution(rt, EvolutionSettings(enabled=True))
            for outcome in outcomes:
                print(f"[demo_server] evolution: {outcome}", flush=True)
        return len(ticks)

    while True:
        _time.sleep(period_seconds)
        try:
            _EXECUTOR.run(_sweep)
        except Exception:
            traceback.print_exc()


def _start_tick_driver():
    period = float(os.environ.get("SCHEDULE_TICK_SECONDS", "10"))
    if period <= 0:
        print("[demo_server] Schedule tick driver disabled "
              "(SCHEDULE_TICK_SECONDS<=0)", flush=True)
        return
    t = threading.Thread(
        target=_tick_driver_loop, args=(period,),
        name="schedule-tick-driver", daemon=True,
    )
    t.start()
    print(f"[demo_server] Schedule tick driver sweeping every {period:g}s", flush=True)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("ACTIVEGRAPH_PORT", "7788"))
    if len(sys.argv) > 2 and sys.argv[1] == "--port":
        port = int(sys.argv[2])

    # Eagerly init so first request is fast
    print(f"[demo_server] Initialising ActiveGraph runtime...", flush=True)
    _get_rt()
    print(f"[demo_server] Runtime ready. Listening on :{port}", flush=True)

    _start_tick_driver()

    # Threaded: read endpoints no longer queue behind a slow chat turn at
    # the socket level; actual graph access stays serialized by the runtime
    # lock (the runtime is single-threaded by design).
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
