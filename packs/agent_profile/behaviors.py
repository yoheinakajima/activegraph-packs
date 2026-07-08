"""Agent Profile Pack behaviors — v0.1.

Seven behaviors covering profile registry maintenance and context assembly:

1-5. Registry recorders — on each profile object type created, index in the
     local _PROFILE_REGISTRY for fast access without graph.objects().

6.   profile_context_provider — on profile_context_request.created, assembles
     a structured ProfileContextView from the referenced AgentProfile, Goals,
     StandingInstructions, PersonalityProfiles, and OwnerPreferences.

7.   profile_context_trigger — on auth_context.created (Identity Pack), auto-
     creates a ProfileContextRequest using the auth_context's principal_role
     and channel. This auto-wires Identity → Agent Profile without the caller
     needing to manually create a request.

8.   frame_context_trigger — on frame.created (Core Pack), auto-creates a
     ProfileContextRequest using the frame's channel and default audience_role.

Design rules:
- graph.objects() is UNSAFE in behaviors — use get_object(id) instead
- ProfileContextRequest must carry profile_id for context assembly
- The behavior fetches the profile via graph.get_object(profile_id)
- Related objects (goals, instructions, preferences, personality) are
  stored in the local profile registry, updated by registration behaviors
- Context is filtered by channel and audience_role from the request
- External-facing views suppress mission (unless expose_mission_to_external)
- Instructions are sorted by priority (highest first), limited by settings

Registry pattern (same as Tool Gateway local registry):
  _PROFILE_REGISTRY[profile_id] = {
    "profile": {...},
    "goals": [...],
    "instructions": [...],
    "personality": [...],
    "preferences": [...],
  }

Populated by profile_registry_recorder (fires on all profile object types).
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.packs import behavior

from .object_types import ProfileContextRequest, ProfileContextView
from .settings import AgentProfileSettings


# ------------------------------------------------------------------ local registry
# Holds all profile-related objects indexed by profile_id.
# Behaviors read from this to assemble context without graph.objects().

_PROFILE_REGISTRY: dict[str, dict[str, Any]] = {}


def _get_or_create_profile_entry(profile_id: str) -> dict[str, Any]:
    if profile_id not in _PROFILE_REGISTRY:
        _PROFILE_REGISTRY[profile_id] = {
            "profile": {},
            "goals": [],
            "instructions": [],
            "personality": [],
            "preferences": [],
        }
    return _PROFILE_REGISTRY[profile_id]


def clear_profile_registry() -> None:
    """Clear the local profile registry. Used in fixture teardown."""
    _PROFILE_REGISTRY.clear()


def _get_default_profile_id(settings: AgentProfileSettings) -> Optional[str]:
    """Return the profile_id to use for auto-triggered requests.

    If settings.default_profile_id is set, use it.
    Otherwise fall back to the first profile registered in the local registry.
    Returns None if no profile has been registered yet.
    """
    if settings.default_profile_id:
        return settings.default_profile_id
    if _PROFILE_REGISTRY:
        return next(iter(_PROFILE_REGISTRY))
    return None


def rebuild_profile_registry(graph) -> int:
    """Repopulate the local profile registry from objects already in `graph`.

    The registry is module-level in-memory state. When a runtime is resumed
    from a persisted event log via ``Runtime.load``, events are replayed to
    rebuild graph objects WITHOUT firing behaviors, so the recorder behaviors
    never run and this registry stays empty. Context assembly (which resolves
    the default profile from the registry) would then find nothing after a
    restart. Call this once after a resume to rebuild the index from the
    replayed profile objects — mirrors identity_auth.rebuild_principal_registry.

    Returns the number of profile objects indexed.
    """
    _PROFILE_REGISTRY.clear()
    type_to_bucket = {
        "goal": "goals",
        "standing_instruction": "instructions",
        "personality_profile": "personality",
        "owner_preference": "preferences",
    }
    count = 0
    for obj in graph.all_objects():
        otype = str(getattr(obj, "type", ""))
        data = obj.data or {}
        if otype == "agent_profile":
            entry = _get_or_create_profile_entry(obj.id)
            entry["profile"] = {**data, "id": obj.id}
            count += 1
        elif otype in type_to_bucket:
            profile_id = data.get("profile_id")
            if not profile_id:
                continue
            entry = _get_or_create_profile_entry(profile_id)
            entry[type_to_bucket[otype]].append({**data, "id": obj.id})
    return count


def _build_profile_context_view(
    *,
    profile_id: str,
    profile_data: dict,
    registry_entry: dict,
    settings: AgentProfileSettings,
    channel: Optional[str],
    audience_role: Optional[str],
    include_goals: bool,
    include_preferences: bool,
    frame_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> ProfileContextView:
    """Assemble a ProfileContextView from already-fetched profile inputs.

    Pure function — does no graph I/O. Shared by ``profile_context_provider``
    (the event-driven request → view path) and ``assemble_profile_view`` (the
    direct call path other packs use, e.g. the Chat Pack injecting identity
    into a chat turn). Keeping the filtering logic here means there is exactly
    one definition of "what a profile context view contains", reused rather
    than duplicated.
    """
    is_external = audience_role in ("external", "customer", "unknown", None)

    agent_name = profile_data.get("name") or settings.default_agent_name
    mission = profile_data.get("mission") or settings.default_mission

    # Suppress mission from external audiences unless configured to expose
    if is_external and not settings.expose_mission_to_external:
        mission = ""

    # --- Personality (filter by channel and audience_role) ---
    personality_list = registry_entry.get("personality", [])
    personality: dict = {
        "tone": settings.default_tone,
        "verbosity": settings.default_verbosity,
        "formality": settings.default_formality,
    }
    for p in personality_list:
        p_channel = p.get("applies_to_channel")
        p_role = p.get("applies_to_audience_role")
        channel_ok = (p_channel is None) or (p_channel == channel)
        role_ok = (p_role is None) or (p_role == audience_role)
        if channel_ok and role_ok:
            personality = {
                "tone": p.get("tone", settings.default_tone),
                "verbosity": p.get("verbosity", settings.default_verbosity),
                "formality": p.get("formality", settings.default_formality),
            }
            break

    # --- Goals (filter by status=active, sort by priority) ---
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    active_goals: list[dict] = []
    if include_goals:
        all_goals = registry_entry.get("goals", [])
        active = [g for g in all_goals if g.get("status") == "active"]
        active.sort(key=lambda g: priority_order.get(g.get("priority", "medium"), 2))
        active_goals = active[: settings.max_active_goals]

    # --- Standing instructions (filter by channel + audience_role) ---
    all_instrs = registry_entry.get("instructions", [])
    matched_instrs = []
    for instr in all_instrs:
        if not instr.get("active", True):
            continue
        i_channel = instr.get("applies_to_channel")
        i_role = instr.get("applies_to_audience_role")
        channel_ok = (i_channel is None) or (i_channel == channel)
        role_ok = (i_role is None) or (i_role == audience_role)
        if channel_ok and role_ok:
            matched_instrs.append(instr)
    # Sort by priority (higher = more important, assembled first)
    matched_instrs.sort(key=lambda i: i.get("priority", 50), reverse=True)
    instruction_texts = [
        i.get("text", "") for i in matched_instrs[: settings.max_standing_instructions]
    ]

    # --- Owner preferences (filter by channel + domain) ---
    owner_prefs: dict[str, str] = {}
    if include_preferences:
        all_prefs = registry_entry.get("preferences", [])
        for pref in all_prefs:
            p_channel = pref.get("channel")
            channel_ok = (p_channel is None) or (p_channel == channel)
            if channel_ok:
                owner_prefs[pref.get("key", "")] = pref.get("value", "")

    return ProfileContextView(
        profile_id=profile_id,
        channel=channel,
        audience_role=audience_role,
        agent_name=agent_name,
        mission=mission,
        personality=personality,
        active_goals=active_goals,
        standing_instructions=instruction_texts,
        owner_preferences=owner_prefs,
        request_id=request_id,
        frame_id=frame_id,
        metadata={"assembled_by": "profile_context_provider"},
    )


def assemble_profile_view(
    graph,
    *,
    settings: Optional[AgentProfileSettings] = None,
    profile_id: Optional[str] = None,
    channel: Optional[str] = None,
    audience_role: Optional[str] = None,
    include_goals: bool = True,
    include_preferences: bool = True,
    frame_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Optional[ProfileContextView]:
    """Assemble a ProfileContextView directly, WITHOUT writing it to the graph.

    A convenience for other packs that need profile context inline (rather than
    via the asynchronous profile_context_request → profile_context_view cascade).
    The Chat Pack uses this to fold the assistant's identity into a chat turn:
    the event-driven request→view path can't help there because the resulting
    view is created in a *later* event cascade — too late for the responder that
    fires in the same batch as the inbound message. See the comment on the Chat
    Pack's ``chat_profile_context`` behavior for that trade-off.

    Resolves the profile via ``graph.get_object`` (safe inside behaviors) with a
    local-registry fallback, then defers to ``_build_profile_context_view`` for
    the actual filtering. Returns ``None`` when no profile is available, so the
    caller can simply skip injecting context.
    """
    settings = settings or AgentProfileSettings()
    pid = profile_id or _get_default_profile_id(settings)
    if not pid:
        return None

    profile_data: dict = {}
    try:
        profile_obj = graph.get_object(pid)
        if profile_obj:
            profile_data = profile_obj.data
    except Exception:
        pass

    registry_entry = _PROFILE_REGISTRY.get(pid, {})
    if not profile_data:
        profile_data = registry_entry.get("profile", {})

    # Nothing to assemble — neither the graph nor the registry knows this profile.
    if not profile_data and not registry_entry:
        return None

    return _build_profile_context_view(
        profile_id=pid,
        profile_data=profile_data,
        registry_entry=registry_entry,
        settings=settings,
        channel=channel,
        audience_role=audience_role,
        include_goals=include_goals,
        include_preferences=include_preferences,
        frame_id=frame_id,
        request_id=request_id,
    )


# ------------------------------------------------------------------ behaviors


@behavior(
    name="profile_registry_recorder",
    on=["object.created"],
    where={"object.type": "agent_profile"},
    creates=[],
)
def profile_registry_recorder(event, graph, ctx, *, settings: AgentProfileSettings):
    """Index an AgentProfile in the local registry for fast lookup.

    On: object.created (agent_profile)
    Creates: nothing — updates local _PROFILE_REGISTRY

    This behavior runs whenever an AgentProfile is added to the graph.
    All related objects reference profile_id, so when profile_context_provider
    needs to assemble a view, it can find everything via the registry.
    """
    obj = event.payload.get("object", {})
    profile_id = obj.get("id")
    profile_data = obj.get("data", {})

    if not profile_id:
        return

    entry = _get_or_create_profile_entry(profile_id)
    entry["profile"] = {**profile_data, "id": profile_id}


@behavior(
    name="goal_registry_recorder",
    on=["object.created"],
    where={"object.type": "goal"},
    creates=[],
)
def goal_registry_recorder(event, graph, ctx, *, settings: AgentProfileSettings):
    """Index a Goal in the profile registry.

    On: object.created (goal)
    Creates: nothing — updates local _PROFILE_REGISTRY
    """
    obj = event.payload.get("object", {})
    goal_id = obj.get("id")
    goal_data = obj.get("data", {})

    profile_id = goal_data.get("profile_id", "")
    if not profile_id:
        return

    entry = _get_or_create_profile_entry(profile_id)
    entry["goals"].append({**goal_data, "id": goal_id})


@behavior(
    name="instruction_registry_recorder",
    on=["object.created"],
    where={"object.type": "standing_instruction"},
    creates=[],
)
def instruction_registry_recorder(event, graph, ctx, *, settings: AgentProfileSettings):
    """Index a StandingInstruction in the profile registry.

    On: object.created (standing_instruction)
    Creates: nothing — updates local _PROFILE_REGISTRY
    """
    obj = event.payload.get("object", {})
    instr_id = obj.get("id")
    instr_data = obj.get("data", {})

    profile_id = instr_data.get("profile_id", "")
    if not profile_id:
        return

    entry = _get_or_create_profile_entry(profile_id)
    entry["instructions"].append({**instr_data, "id": instr_id})


@behavior(
    name="personality_registry_recorder",
    on=["object.created"],
    where={"object.type": "personality_profile"},
    creates=[],
)
def personality_registry_recorder(event, graph, ctx, *, settings: AgentProfileSettings):
    """Index a PersonalityProfile in the profile registry.

    On: object.created (personality_profile)
    Creates: nothing — updates local _PROFILE_REGISTRY
    """
    obj = event.payload.get("object", {})
    obj_id = obj.get("id")
    obj_data = obj.get("data", {})

    profile_id = obj_data.get("profile_id", "")
    if not profile_id:
        return

    entry = _get_or_create_profile_entry(profile_id)
    entry["personality"].append({**obj_data, "id": obj_id})


@behavior(
    name="preference_registry_recorder",
    on=["object.created"],
    where={"object.type": "owner_preference"},
    creates=[],
)
def preference_registry_recorder(event, graph, ctx, *, settings: AgentProfileSettings):
    """Index an OwnerPreference in the profile registry.

    On: object.created (owner_preference)
    Creates: nothing — updates local _PROFILE_REGISTRY
    """
    obj = event.payload.get("object", {})
    obj_id = obj.get("id")
    obj_data = obj.get("data", {})

    profile_id = obj_data.get("profile_id", "")
    if not profile_id:
        return

    entry = _get_or_create_profile_entry(profile_id)
    entry["preferences"].append({**obj_data, "id": obj_id})


@behavior(
    name="profile_context_trigger",
    on=["object.created"],
    where={"object.type": "auth_context"},
    creates=["profile_context_request"],
)
def profile_context_trigger(event, graph, ctx, *, settings: AgentProfileSettings):
    """Auto-create a ProfileContextRequest when an AuthContext arrives.

    On: object.created (auth_context) — Identity Pack
    Creates: profile_context_request (if auto_trigger_on_auth_context is True)

    Derives audience_role from auth_context.principal_role and channel from
    auth_context.channel. Uses settings.default_profile_id or the first
    registered profile in _PROFILE_REGISTRY.

    This is the primary auto-wiring point between Identity Pack and Agent
    Profile Pack — context is assembled without the caller needing to
    explicitly create a ProfileContextRequest.
    """
    if not settings.auto_trigger_on_auth_context:
        return

    profile_id = _get_default_profile_id(settings)
    if not profile_id:
        return  # No profile registered yet — skip (will trigger when profile arrives)

    obj = event.payload.get("object", {})
    auth_ctx_id = obj.get("id")
    auth_ctx_data = obj.get("data", {})

    channel = auth_ctx_data.get("channel") or "unknown"
    # Map principal_role → audience_role (same vocabulary, different semantic layer)
    principal_role = auth_ctx_data.get("principal_role", "unknown")
    frame_id = auth_ctx_data.get("frame_id")

    # Suppress noisy dedup: only one request per (profile_id, channel, role) per frame.
    # We use a simple deterministic key stored in metadata to allow callers to detect it.
    request = graph.add_object(
        "profile_context_request",
        ProfileContextRequest(
            profile_id=profile_id,
            channel=channel,
            audience_role=principal_role,
            include_goals=True,
            include_preferences=(principal_role in ("owner", "admin")),
            frame_id=frame_id,
            metadata={
                "triggered_by": "auth_context",
                "auth_context_id": auth_ctx_id,
            },
        ).model_dump(),
    )

    # Relation: auth_context → profile_context_request
    try:
        graph.add_relation(auth_ctx_id, request.id, "triggers_context_for")
    except Exception:
        pass


@behavior(
    name="frame_context_trigger",
    on=["object.created"],
    where={"object.type": "frame"},
    creates=["profile_context_request"],
)
def frame_context_trigger(event, graph, ctx, *, settings: AgentProfileSettings):
    """Auto-create a ProfileContextRequest when a Frame is opened.

    On: object.created (frame) — Core Pack
    Creates: profile_context_request (if auto_trigger_on_frame is True)

    Frames carry the interaction's channel. Audience role defaults to 'owner'
    for frame-triggered assembly (frames are typically opened by the owner's
    runtime session). Override by creating an explicit ProfileContextRequest.
    """
    if not settings.auto_trigger_on_frame:
        return

    profile_id = _get_default_profile_id(settings)
    if not profile_id:
        return

    obj = event.payload.get("object", {})
    frame_id = obj.get("id")
    frame_data = obj.get("data", {})

    channel = frame_data.get("channel") or "unknown"
    # Frames are typically opened by the operator/owner session
    audience_role = frame_data.get("audience_role") or "owner"

    request = graph.add_object(
        "profile_context_request",
        ProfileContextRequest(
            profile_id=profile_id,
            channel=channel,
            audience_role=audience_role,
            include_goals=True,
            include_preferences=True,
            frame_id=frame_id,
            metadata={
                "triggered_by": "frame",
                "frame_id": frame_id,
            },
        ).model_dump(),
    )

    # Relation: frame → profile_context_request
    try:
        graph.add_relation(frame_id, request.id, "triggers_context_for")
    except Exception:
        pass


@behavior(
    name="profile_context_provider",
    on=["object.created"],
    where={"object.type": "profile_context_request"},
    creates=["profile_context_view"],
)
def profile_context_provider(event, graph, ctx, *, settings: AgentProfileSettings):
    """Assemble a scoped ProfileContextView from a ProfileContextRequest.

    On: object.created (profile_context_request)
    Creates: profile_context_view with filtered context
    Creates: fulfilled_by_profile(request → view) relation

    Fetches the AgentProfile via graph.get_object(profile_id).
    Uses the local registry for goals, instructions, preferences, and personality.
    Filters all context by channel and audience_role from the request.

    Owner-facing contexts include mission (unless expose_mission_to_external=False).
    External-facing contexts suppress mission by default.

    Fires on:
    - Manually created ProfileContextRequest objects
    - Auto-created requests from profile_context_trigger (auth_context.created)
    - Auto-created requests from frame_context_trigger (frame.created)
    """
    obj = event.payload.get("object", {})
    request_id = obj.get("id")
    request_data = obj.get("data", {})

    profile_id = request_data.get("profile_id", "")
    if not profile_id:
        return

    channel = request_data.get("channel")
    audience_role = request_data.get("audience_role")
    include_goals = request_data.get("include_goals", True)
    include_preferences = request_data.get("include_preferences", True)
    frame_id = request_data.get("frame_id")

    # --- Fetch AgentProfile via get_object (safe in behaviors) ---
    profile_data: dict = {}
    try:
        profile_obj = graph.get_object(profile_id)
        if profile_obj:
            profile_data = profile_obj.data
    except Exception:
        pass

    # Fall back to registry if graph lookup fails
    registry_entry = _PROFILE_REGISTRY.get(profile_id, {})
    if not profile_data:
        profile_data = registry_entry.get("profile", {})

    # Assemble via the shared builder so the request→view path and the direct
    # assemble_profile_view() path produce identical context.
    view_model = _build_profile_context_view(
        profile_id=profile_id,
        profile_data=profile_data,
        registry_entry=registry_entry,
        settings=settings,
        channel=channel,
        audience_role=audience_role,
        include_goals=include_goals,
        include_preferences=include_preferences,
        frame_id=frame_id,
        request_id=request_id,
    )

    # --- Store ProfileContextView ---
    view = graph.add_object("profile_context_view", view_model.model_dump())

    # Create fulfilled_by_profile relation
    try:
        graph.add_relation(request_id, view.id, "fulfilled_by_profile")
    except Exception:
        pass


BEHAVIORS = [
    profile_registry_recorder,
    goal_registry_recorder,
    instruction_registry_recorder,
    personality_registry_recorder,
    preference_registry_recorder,
    profile_context_trigger,
    frame_context_trigger,
    profile_context_provider,
]
