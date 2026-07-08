# Identity/Auth Pack — v0.1

Answers **"who is speaking, how confident are we, what can they do?"**

Enables differentiated behavior for owner vs. external contact vs. unknown. Every source is resolved to a Principal with a trust role and confidence score. An AuthContext scopes the session. Unsafe actions are rejected before execution.

---

## Object Types

| Type | Description |
|------|-------------|
| `principal` | Recognized identity with role + auth_confidence. Helper methods: `is_owner()`, `is_external()`, `can(action)` |
| `auth_context` | Session-scoped auth state: snapshots principal_role + channel at resolution time |
| `role` | Named role with a capability list (optional override of built-in hierarchy) |
| `permission` | Explicit action/resource grant scoped to a role |
| `delegation` | Temporary scope transfer from one principal to another |

### Role Hierarchy (additive)

```
owner       → all actions ("*")
admin       → manage_settings, manage_collaborators, read, write, execute, comment
collaborator→ read, write, comment
external    → read_public
customer    → read_own
unknown     → none
blocked     → none (all actions rejected)
```

---

## Behavior Map

```
source.created
  → principal_resolver
      If sender_ref ∈ owner_identifiers → role="owner", confidence=owner_auth_confidence
      Else → role=default_external_role, confidence=default_auth_confidence
      Creates: principal
      Creates: resolves_to(source → principal)

principal.created
  → auth_context_builder (if settings.create_auth_context)
      Creates: auth_context (snapshots role + channel)
      Creates: authenticated_by(principal → auth_context)

action.created (status=proposed, has principal_id in metadata)
  → permission_checker (if settings.check_permissions_on_proposed_actions)
      blocked role → patches status="rejected", adds rejection_reason
      high/critical risk_class + non-owner/admin → patches status="rejected"
      execute-type + external/unknown → patches status="rejected"
      otherwise → patches metadata.permission_checked=True
```

---

## Settings

```python
IdentitySettings(
    owner_identifiers=["alice@example.com"],  # → role="owner"
    default_external_role="external",         # unknown/external/customer
    owner_auth_confidence=0.9,
    default_auth_confidence=0.4,
    create_auth_context=True,
    check_permissions_on_proposed_actions=True,
)
```

---

## Quick Start

```python
from activegraph import Runtime, Graph
from packs.core import pack as core_pack
from packs.identity_auth import pack as identity_pack, IdentitySettings

rt = Runtime(Graph())
rt.load_pack(core_pack)
rt.load_pack(identity_pack, settings=IdentitySettings(
    owner_identifiers=["alice@example.com"],
))

graph.add_object("source", {
    "kind": "chat_message",
    "content": "Draft a proposal",
    "sender_ref": "alice@example.com",
    "channel": "chat",
})
rt.run_until_idle()
# → principal(role="owner") + auth_context in graph
```

---

## Composes With

- **Core Pack** — `principal_resolver` fires on `source.created`
- **Entity Pack** — `Principal.entity_id` links to a canonical `Entity`
- **Agent Profile Pack** — `audience_role` from Principal shapes profile context slices
- **Communication Packs** — `comm_message.created` can provide richer sender context (v0.2)

---

## Relation Types

| Relation | From → To | Meaning |
|----------|-----------|---------|
| `resolves_to` | source → principal | Source resolved to a principal |
| `authenticated_by` | principal → auth_context | Session auth state |
| `granted_by` | delegation → principal | Who delegated |
| `granted_to` | delegation → principal | Who received delegation |
| `linked_to_entity` | principal → entity | Links to Entity pack entity |

---

## Known Limitations (v0.1)

- `principal_resolver` always creates a new Principal per source (no dedup across messages from the same sender — Entity Pack handles dedup via `entity_id` link)
- OAuth/OIDC auth flows are out of scope — Identity Pack handles role assignment, not authentication infrastructure
- `permission_checker` only fires on actions carrying `principal_id` in metadata

## Explicit registration & adapter lookup (v0.2)

- `register_principal(graph, sender_ref, role, name=None, channel=...)` —
  create or promote a Principal with an explicit role. Idempotent by
  normalized ref; keeps the dedup registry in sync. Use it to seed the owner
  (`bundles.seed_owner_principals`) or promote a contact to collaborator.
- `resolve_known_principal(graph, sender_ref)` — behavior-safe lookup
  (registry + get_object). Channel adapters use it for reply gating and
  audience-aware profile context (`packs/communication/gating.py`).
