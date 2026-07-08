"""Boot-time persistence for adopted packs (design §4).

`load_pack` is a runtime call, so adopted packs vanish on restart. The
graph is the durable registry: the chassis calls
`reload_adopted_packs(rt)` at boot, which re-materializes every ACTIVE
mod_promotion from its artifacts, re-verifies the bundle-hash pin, and
loads. Disabled and loading-state promotions stay down; a hash mismatch
disables the promotion loudly and opens a capability_gap so the
condition is impossible to miss.
"""

from __future__ import annotations

from activegraph.packs.manifest import PackManifestError

from .materialize import materialize_verified


def reload_adopted_packs(rt) -> dict[str, str]:
    """Returns {pack_name: outcome} for every adoption record seen."""
    graph = rt.graph
    outcomes: dict[str, str] = {}
    for promotion in list(graph.objects(type="mod_promotion")):
        data = promotion.data or {}
        name = data.get("pack_name", "?")
        status = data.get("status")
        if status == "disabled":
            outcomes[name] = "disabled (stays down)"
            continue
        if status != "active":
            outcomes[name] = f"skipped (status={status})"
            continue
        proposal = graph.get_object(data.get("proposal_id", ""))
        if proposal is None:
            outcomes[name] = "skipped (proposal missing)"
            continue
        try:
            _, _, pack = materialize_verified(graph, proposal)
        except PackManifestError as exc:
            graph.patch_object(promotion.id, {"status": "disabled"})
            graph.add_object("capability_gap", {
                "kind": "reflection",
                "description": (f"adopted pack {name!r} failed its bundle-hash "
                                f"pin at boot and was disabled: {exc}"),
                "status": "open",
                "metadata": {"promotion_id": str(promotion.id)},
            })
            print(f"[evolution] BOOT HASH MISMATCH: {name} disabled", flush=True)
            outcomes[name] = "disabled (hash mismatch)"
            continue
        rt.load_pack(pack)
        outcomes[name] = "loaded"
    return outcomes
