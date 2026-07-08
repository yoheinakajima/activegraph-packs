# Tool Gateway Pack Changelog

## v0.2.0 — Approval resolution: closing the trust loop (2026-07-07)

### Added
- `capability_denial` object type + `denied_by` relation — refusals of held
  calls are first-class audit objects (who refused, why, when), not status flips.
- `approve_capability` / `approve_capability_fn` — resolves a call held at
  `status='policy_checking'` by creating the **same** `capability_approval`
  object auto-approval creates, so `call_executor` remains the single
  execution trigger. Before this, a held call could never be advanced by
  anything.
- `deny_capability` / `deny_capability_fn` — patches the held call to
  `rejected` and records a `capability_denial`.
- `pending_approvals` / `pending_approvals_fn` — lists held calls, oldest
  first. The graph is the single source of truth for approval state; there is
  no in-memory queue to lose on restart.
- Approver verification: when Identity/Auth is loaded and principals are
  registered, `approver_ref` must resolve to a principal with a role in
  `ToolGatewaySettings.approver_roles` (new setting, default
  `["owner", "admin"]`); unknown refs are refused. Without Identity/Auth the
  gateway degrades gracefully and stamps decisions
  `verification='identity_unverified'`.
- Fixtures: `manual_approval_flow` (held → approve → execute → source) and
  `manual_denial_flow` (held → deny → rejected, no execution).

### Fixed
- **`add_relation` argument order.** All four relation writes passed
  `(type, source, target)` but the API is `(source, target, type)` — the
  same bug the Chat Pack fixed in its v0.2.0. The gateway's audit edges
  (`calls`, `approved_by`, `produces_result`, `sourced_as`) were being
  written as garbage relations (type string as the source id) since v0.1.0.
  Also corrected the fixture assertion that had been written against the
  broken shape (`r.source` where it meant `r.type`).

## v0.1.0 — Initial release (2026-06-03)

### Added
- 3 object types: `capability_provider`, `capability_call`, `capability_result`
- 3 relation types: `calls`, `produces_result`, `sourced_as`
- 3 behaviors:
  - `call_recorder` — creates calls relation on capability_call.created
  - `policy_enforcer` — checks risk_class against auto_approve_risk_classes
  - `result_sourcer` — maps capability_result to Core source object
- `execute_capability` tool with local function registry
- `register_local_capability` for registering Python functions as capabilities
- `ToolGatewaySettings` with configurable risk policy
- Fixture: tool_call_flow (provider → call → result → source)
- Full README with behavior map

### Design decisions
- credential_ref_name stores names only — actual secrets resolved by Secrets Pack
- output_data is truncated to max_output_chars to avoid giant graph objects
- result_sourcer is the cross-pack bridge: Tool Gateway → Core → observations
