"""The adoption decision surface (design §3 stage 4).

"The owner approved it" has to mean "the owner read it", so this module
turns one mod_proposal into a single reviewable page: who authored it,
which gap it claims to fix, the FULL source diff (small by gate 7, so
actually readable), the declared surface including `consumes` (the
pack's outbound reach), every gate verdict, the trial summary with
failures and eval numbers, the promote diff counts, the fork run id,
and any injection flags, loudly.

Everything comes from graph state alone: `build_review` is a pure read
over the objects the pipeline already wrote, and `render_review_html`
is a deterministic stdlib renderer over that dict. No candidate code is
imported, executed, or even written to disk here; agent-authored text
is escaped everywhere it appears.
"""

from __future__ import annotations

import difflib
import html
import tomllib
from typing import Any, Optional

from .materialize import proposal_files

# Statuses where an approve/deny decision is still meaningful.
_DECIDABLE = ("drafted", "gated", "trialed", "pending_approval")


# ------------------------------------------------------------- the model


def _gap_view(graph, gap_id: str) -> Optional[dict[str, Any]]:
    if not gap_id:
        return None
    gap = graph.get_object(gap_id)
    if gap is None or gap.type != "capability_gap":
        return None
    data = gap.data or {}
    return {
        "id": str(gap.id),
        "kind": data.get("kind", ""),
        "description": data.get("description", ""),
        "status": data.get("status", ""),
        "evidence_refs": list(data.get("evidence_refs") or []),
        "injection_flags": list(data.get("injection_flags") or []),
    }


def _baseline_files(graph, pack_name: str, proposal_id: str) -> tuple[dict, str]:
    """The diff baseline: the artifacts behind the newest non-disabled
    promotion of the same pack name (what is running today). A first
    adoption has no baseline; the whole source renders as added."""
    promotions = [p for p in graph.objects(type="mod_promotion")
                  if p.data.get("pack_name") == pack_name
                  and p.data.get("status") in ("active", "loading")
                  and p.data.get("proposal_id") != proposal_id]
    if not promotions:
        return {}, "new pack (no adopted version to diff against)"
    current = promotions[-1]
    base_proposal = graph.get_object(current.data.get("proposal_id", ""))
    if base_proposal is None:
        return {}, "new pack (baseline proposal missing)"
    try:
        files = proposal_files(graph, base_proposal)
    except RuntimeError:
        return {}, "new pack (baseline artifacts missing)"
    return files, (f"adopted version (promotion {current.id}, "
                   f"bundle {current.data.get('bundle_hash', '')[:19]})")


def _unified_diff(baseline: dict[str, str], files: dict[str, str]) -> list[dict]:
    """Per-file unified diffs, every path from either side, sorted."""
    out = []
    for path in sorted(set(baseline) | set(files)):
        old = baseline.get(path, "")
        new = files.get(path, "")
        lines = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"adopted/{path}", tofile=f"proposed/{path}",
            lineterm=""))
        status = ("unchanged" if old == new
                  else "added" if not old
                  else "removed" if not new
                  else "modified")
        out.append({"path": path, "status": status, "lines": lines,
                    "size_bytes": len(new.encode())})
    return out


def _manifest_view(files: dict[str, str]) -> dict[str, Any]:
    """Parse the proposal's manifest.toml text (never trusted, only
    displayed; the gates verified it against the source already)."""
    text = files.get("manifest.toml", "")
    try:
        data = tomllib.loads(text)
    except Exception as exc:
        return {"error": f"manifest.toml does not parse: {exc}"}
    pack = data.get("pack", {}) or {}
    surface = data.get("surface", {}) or {}
    return {
        "name": pack.get("name", ""),
        "version": pack.get("version", ""),
        "description": pack.get("description", ""),
        "authored_by": (pack.get("provenance", {}) or {}).get("authored_by", ""),
        "behaviors": list(surface.get("behaviors") or []),
        "tools": list(surface.get("tools") or []),
        "object_types": list(surface.get("object_types") or []),
        "relation_types": list(surface.get("relation_types") or []),
        "capabilities": [
            {"key": f"{c.get('provider', '?')}.{c.get('capability', '?')}",
             "risk_class": c.get("risk_class", "?")}
            for c in (surface.get("capabilities") or [])
        ],
        "consumes": list(surface.get("consumes") or []),
    }


def _pending_call(graph, proposal_id: str) -> Optional[dict[str, Any]]:
    for call in graph.objects(type="capability_call"):
        data = call.data or {}
        if (data.get("provider_name") == "evolution"
                and data.get("capability_name") == "adopt_proposal"
                and (data.get("input_data") or {}).get("proposal_id") == proposal_id
                and data.get("status") == "policy_checking"):
            return {"call_id": str(call.id),
                    "proposed_by": data.get("proposed_by", ""),
                    "proposed_at": data.get("proposed_at", ""),
                    "risk_class": data.get("risk_class", "")}
    return None


def build_review(graph, proposal_id: str) -> dict[str, Any]:
    """Assemble the full review model for one proposal, from graph state.

    Raises KeyError when the proposal does not exist; everything else
    degrades to explicit placeholders (a review page must render even
    for a half-broken proposal, because that is exactly the proposal an
    owner most needs to see)."""
    proposal = graph.get_object(proposal_id)
    if proposal is None or proposal.type != "mod_proposal":
        raise KeyError(f"no mod_proposal {proposal_id!r}")
    data = proposal.data or {}

    try:
        files = proposal_files(graph, proposal)
        files_error = ""
    except RuntimeError as exc:
        files, files_error = {}, str(exc)

    gap = _gap_view(graph, data.get("gap_id", ""))
    baseline, baseline_label = _baseline_files(
        graph, data.get("pack_name", ""), proposal_id)

    gates = [
        {"gate": g.data.get("gate", ""), "verdict": g.data.get("verdict", ""),
         "details": g.data.get("details", ""), "at": g.data.get("at", "")}
        for g in graph.objects(type="gate_result")
        if g.data.get("proposal_id") == proposal_id
    ]
    trials = [
        {"id": str(t.id), "fork_run_id": t.data.get("fork_run_id", ""),
         "forked_at_event": t.data.get("forked_at_event", ""),
         "verdict": t.data.get("verdict", ""),
         "eval_summary": dict(t.data.get("eval_summary") or {}),
         "diff_summary": dict(t.data.get("diff_summary") or {}),
         "failures": list(t.data.get("failures") or []),
         "at": t.data.get("at", "")}
        for t in graph.objects(type="mod_trial")
        if t.data.get("proposal_id") == proposal_id
    ]
    promotions = [
        {"id": str(p.id), "status": p.data.get("status", ""),
         "fork_run_id": p.data.get("fork_run_id", ""),
         "bundle_hash": p.data.get("bundle_hash", ""), "at": p.data.get("at", "")}
        for p in graph.objects(type="mod_promotion")
        if p.data.get("proposal_id") == proposal_id
    ]

    flags = sorted(set(data.get("injection_flags") or [])
                   | set((gap or {}).get("injection_flags") or []))

    return {
        "proposal": {
            "id": str(proposal.id),
            "pack_name": data.get("pack_name", ""),
            "pack_version": data.get("pack_version", ""),
            "status": data.get("status", ""),
            "status_note": data.get("status_note", ""),
            "authored_by": data.get("authored_by", ""),
            "rationale": data.get("rationale", ""),
            "bundle_hash": data.get("bundle_hash", ""),
            "auto_retries": int((data.get("metadata") or {}).get("auto_retries", 0)),
        },
        "gap": gap,
        "files_error": files_error,
        "baseline_label": baseline_label,
        "diff": _unified_diff(baseline, files),
        "manifest": _manifest_view(files),
        "gates": gates,
        "trial": trials[-1] if trials else None,
        "trial_count": len(trials),
        "promotions": promotions,
        "injection_flags": flags,
        "pending_call": _pending_call(graph, proposal_id),
        "decidable": data.get("status", "") in _DECIDABLE,
    }


# ------------------------------------------------------------- the page

_CSS = """
body { font-family: -apple-system, 'Segoe UI', sans-serif; margin: 0;
       background: #f5f5f4; color: #1c1917; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 22px; } h2 { font-size: 16px; margin-top: 32px;
     border-bottom: 1px solid #d6d3d1; padding-bottom: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 5px 10px; border-bottom: 1px solid #e7e5e4;
         vertical-align: top; }
code, pre { font-family: ui-monospace, 'SF Mono', Menlo, monospace;
            font-size: 12px; }
pre { background: #fff; border: 1px solid #d6d3d1; border-radius: 6px;
      padding: 10px; overflow-x: auto; line-height: 1.45; }
.banner { border-radius: 8px; padding: 12px 16px; margin: 12px 0;
          font-weight: 600; }
.banner.agent { background: #fef3c7; border: 2px solid #d97706; color: #92400e; }
.banner.human { background: #ecfdf5; border: 2px solid #059669; color: #065f46; }
.banner.taint { background: #fee2e2; border: 2px solid #dc2626; color: #991b1b; }
.banner.clean { background: #f0fdf4; border: 1px solid #86efac; color: #166534;
                font-weight: 400; }
.verdict-pass { color: #059669; font-weight: 600; }
.verdict-fail { color: #dc2626; font-weight: 600; }
.verdict-suspended { color: #d97706; font-weight: 600; }
.diff-add { color: #059669; display: block; background: #f0fdf4; }
.diff-del { color: #dc2626; display: block; background: #fef2f2; }
.diff-hunk { color: #7c3aed; display: block; }
.muted { color: #78716c; font-size: 12px; }
.decision { background: #fff; border: 2px solid #1c1917; border-radius: 8px;
            padding: 16px; margin-top: 24px; }
.decision button { font-size: 14px; padding: 8px 22px; border-radius: 6px;
                   border: 1px solid #1c1917; cursor: pointer; margin-right: 8px; }
.decision .approve { background: #059669; color: #fff; border-color: #047857; }
.decision .deny { background: #fff; color: #991b1b; border-color: #991b1b; }
"""

_DECISION_JS = """
async function decide(decision) {
  const call_id = document.getElementById('call-id').value;
  const approver = document.getElementById('approver-ref').value;
  const token = document.getElementById('approval-token').value;
  const body = {call_id: call_id, decision: decision, approver_ref: approver};
  if (decision === 'deny')
    body.reason = document.getElementById('decision-note').value;
  else
    body.note = document.getElementById('decision-note').value;
  const headers = {'Content-Type': 'application/json'};
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const res = await fetch('/approvals', {method: 'POST',
    headers: headers,
    body: JSON.stringify(body)});
  const out = await res.json();
  document.getElementById('decision-result').textContent =
    JSON.stringify(out, null, 2);
  if (out.ok) setTimeout(() => location.reload(), 800);
}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _kv_rows(pairs: list[tuple[str, Any]]) -> str:
    return "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>"
                   for k, v in pairs)


def _diff_html(diff: list[dict]) -> str:
    parts = []
    for entry in diff:
        parts.append(f"<h3><code>{_esc(entry['path'])}</code> "
                     f"<span class='muted'>({_esc(entry['status'])}, "
                     f"{entry['size_bytes']} bytes)</span></h3>")
        if not entry["lines"]:
            parts.append("<p class='muted'>unchanged</p>")
            continue
        rendered = []
        for line in entry["lines"]:
            esc = _esc(line)
            if line.startswith("+") and not line.startswith("+++"):
                rendered.append(f"<span class='diff-add'>{esc}</span>")
            elif line.startswith("-") and not line.startswith("---"):
                rendered.append(f"<span class='diff-del'>{esc}</span>")
            elif line.startswith("@@"):
                rendered.append(f"<span class='diff-hunk'>{esc}</span>")
            else:
                rendered.append(f"<span>{esc}</span>")
        parts.append("<pre>" + "\n".join(rendered) + "</pre>")
    return "".join(parts)


def render_review_html(review: dict[str, Any]) -> str:
    """The one-page decision surface. Deterministic over the model."""
    p = review["proposal"]
    parts: list[str] = []
    parts.append(f"<main><h1>Adoption review: <code>{_esc(p['pack_name'])}"
                 f"</code> v{_esc(p['pack_version'])}</h1>")

    # Who wrote this, loudly, before anything else.
    author = p["authored_by"] or "unknown"
    author_class = "human" if author in ("human", "owner") else "agent"
    parts.append(f"<div class='banner {author_class}'>AUTHORED BY: "
                 f"{_esc(author.upper())}"
                 + ("" if author_class == "human" else
                    " &mdash; this code was written by the assistant. "
                    "Read the diff before approving.")
                 + "</div>")

    flags = review["injection_flags"]
    if flags:
        parts.append("<div class='banner taint'>INJECTION FLAGS ON THIS "
                     "LINEAGE: " + ", ".join(_esc(f) for f in flags)
                     + "</div>")
    else:
        parts.append("<div class='banner clean'>No injection flags on this "
                     "proposal or its gap lineage.</div>")

    parts.append("<h2>Proposal</h2><table>")
    parts.append(_kv_rows([
        ("proposal id", p["id"]),
        ("status", p["status"] + (f" ({p['status_note']})" if p["status_note"] else "")),
        ("rationale", p["rationale"] or "(none given)"),
        ("bundle hash (the approval pin)", p["bundle_hash"]),
        ("automatic conflict retries so far", p["auto_retries"]),
    ]))
    parts.append("</table>")

    gap = review["gap"]
    parts.append("<h2>Gap this claims to fix</h2>")
    if gap:
        parts.append("<table>" + _kv_rows([
            ("gap id", gap["id"]), ("kind", gap["kind"]),
            ("description", gap["description"]),
            ("gap injection flags", ", ".join(gap["injection_flags"]) or "none"),
            ("evidence refs", ", ".join(gap["evidence_refs"]) or "none"),
        ]) + "</table>")
    else:
        parts.append("<p class='muted'>No linked capability_gap (direct "
                     "submission).</p>")

    m = review["manifest"]
    parts.append("<h2>Declared surface (from manifest.toml)</h2>")
    if m.get("error"):
        parts.append(f"<p class='verdict-fail'>{_esc(m['error'])}</p>")
    else:
        caps = ", ".join(f"{c['key']} ({c['risk_class']})"
                         for c in m["capabilities"]) or "none"
        parts.append("<table>" + _kv_rows([
            ("manifest authored_by", m["authored_by"] or "(missing)"),
            ("behaviors", ", ".join(m["behaviors"]) or "none"),
            ("tools", ", ".join(m["tools"]) or "none"),
            ("object types", ", ".join(m["object_types"]) or "none"),
            ("relation types", ", ".join(m["relation_types"]) or "none"),
            ("capabilities it registers", caps),
            ("consumes (outbound reach)", ", ".join(m["consumes"])
             or "none declared"),
        ]) + "</table>")

    parts.append("<h2>Gate verdicts</h2>")
    if review["gates"]:
        rows = "".join(
            f"<tr><td><code>{_esc(g['gate'])}</code></td>"
            f"<td class='verdict-{_esc(g['verdict'])}'>{_esc(g['verdict'])}</td>"
            f"<td>{_esc(g['details'])}</td>"
            f"<td class='muted'>{_esc(g['at'])}</td></tr>"
            for g in review["gates"])
        parts.append("<table><tr><th>gate</th><th>verdict</th><th>details"
                     "</th><th>at</th></tr>" + rows + "</table>")
    else:
        parts.append("<p class='verdict-fail'>No gate results recorded. Do "
                     "not approve an ungated proposal.</p>")

    trial = review["trial"]
    parts.append("<h2>Fork trial</h2>")
    if trial:
        eval_rows = [(k, v) for k, v in sorted(trial["eval_summary"].items())]
        diff_rows = [(k, v) for k, v in sorted(trial["diff_summary"].items())]
        parts.append("<table>" + _kv_rows(
            [("verdict", trial["verdict"]),
             ("fork run id", trial["fork_run_id"]),
             ("forked at event", trial["forked_at_event"]),
             ("trials run", review["trial_count"]), ("at", trial["at"])]
            + eval_rows
            + [(f"promote delta: {k}", v) for k, v in diff_rows]) + "</table>")
        if trial["failures"]:
            parts.append("<h3 class='verdict-fail'>Trial failures</h3><pre>"
                         + _esc("\n\n".join(
                             "\n".join(f"{k}: {v}" for k, v in f.items())
                             for f in trial["failures"])) + "</pre>")
        else:
            parts.append("<p class='verdict-pass'>Zero candidate failures "
                         "in the trial.</p>")
    else:
        parts.append("<p class='verdict-fail'>No trial recorded. Do not "
                     "approve an untrialed proposal.</p>")

    if review["promotions"]:
        parts.append("<h2>Prior promotion records for this proposal</h2>"
                     "<table><tr><th>id</th><th>status</th><th>fork run"
                     "</th><th>at</th></tr>")
        parts.append("".join(
            f"<tr><td>{_esc(pr['id'])}</td><td>{_esc(pr['status'])}</td>"
            f"<td>{_esc(pr['fork_run_id'])}</td><td>{_esc(pr['at'])}</td></tr>"
            for pr in review["promotions"]) + "</table>")

    parts.append(f"<h2>Full source diff</h2><p class='muted'>Baseline: "
                 f"{_esc(review['baseline_label'])}</p>")
    if review["files_error"]:
        parts.append(f"<p class='verdict-fail'>{_esc(review['files_error'])}"
                     "</p>")
    parts.append(_diff_html(review["diff"]))

    pending = review["pending_call"]
    if pending:
        parts.append(
            "<div class='decision'><h2 style='margin-top:0;border:0'>"
            "Decision</h2>"
            f"<p>Held call <code>{_esc(pending['call_id'])}</code> "
            f"(risk <b>{_esc(pending['risk_class'])}</b>), proposed by "
            f"<code>{_esc(pending['proposed_by'])}</code> at "
            f"{_esc(pending['proposed_at'])}.</p>"
            f"<input type='hidden' id='call-id' "
            f"value='{_esc(pending['call_id'])}'>"
            "<p><label>Approver ref: <input id='approver-ref' size='28' "
            "placeholder='owner@example.com'></label></p>"
            "<p><label>Approval token: <input id='approval-token' "
            "type='password' size='28' autocomplete='off'></label> "
            "<span class='muted'>authenticates the channel; the approver "
            "ref is still verified against principals</span></p>"
            "<p><label>Note / denial reason: <input id='decision-note' "
            "size='48'></label></p>"
            "<button class='approve' onclick=\"decide('approve')\">"
            "Approve adoption</button>"
            "<button class='deny' onclick=\"decide('deny')\">Deny</button>"
            "<pre id='decision-result' class='muted'></pre></div>")
    elif review["decidable"]:
        parts.append("<div class='decision'><p>No held approval call yet: "
                     "adoption has not been requested for this proposal "
                     "(<code>request_adoption_fn</code> creates the held "
                     "call).</p></div>")
    else:
        parts.append(f"<div class='decision'><p>Status is "
                     f"<b>{_esc(p['status'])}</b>; there is nothing to "
                     "decide here.</p></div>")

    parts.append("</main>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>Adoption review: {_esc(p['pack_name'])}</title>"
            f"<style>{_CSS}</style><script>{_DECISION_JS}</script></head>"
            "<body>" + "".join(parts) + "</body></html>")


def render_approvals_index_html(graph) -> str:
    """The /approvals landing page: every held call, with evolution
    adoptions linking to their full review page instead of raw JSON."""
    from packs.tool_gateway.tools import pending_approvals_fn

    rows = []
    for call in pending_approvals_fn(graph):
        key = f"{call['provider_name']}.{call['capability_name']}"
        proposal_id = ""
        if key == "evolution.adopt_proposal":
            proposal_id = str((call.get("input_data") or {})
                              .get("proposal_id", ""))
        action = (f"<a href='/approvals/review?proposal_id="
                  f"{_esc(proposal_id)}'>full review</a>"
                  if proposal_id else "<span class='muted'>JSON only</span>")
        rows.append(
            f"<tr><td><code>{_esc(call['call_id'])}</code></td>"
            f"<td><code>{_esc(key)}</code></td>"
            f"<td>{_esc(call['risk_class'])}</td>"
            f"<td>{_esc(call.get('proposed_by') or '')}</td>"
            f"<td>{_esc(call.get('proposed_at') or '')}</td>"
            f"<td>{action}</td></tr>")
    table = ("<table><tr><th>call</th><th>capability</th><th>risk</th>"
             "<th>proposed by</th><th>at</th><th></th></tr>"
             + "".join(rows) + "</table>" if rows
             else "<p class='muted'>Nothing is waiting for approval.</p>")

    try:
        needs_owner = [
            o for o in graph.objects(type="mod_proposal")
            if o.data.get("status") == "needs_owner"
        ]
    except Exception:  # evolution types not registered on this runtime
        needs_owner = []
    stuck = ""
    if needs_owner:
        stuck = ("<h2>Proposals needing owner action</h2><table>"
                 "<tr><th>proposal</th><th>pack</th><th>note</th><th></th></tr>"
                 + "".join(
                     f"<tr><td><code>{_esc(o.id)}</code></td>"
                     f"<td>{_esc(o.data.get('pack_name', ''))}</td>"
                     f"<td>{_esc(o.data.get('status_note', ''))}</td>"
                     f"<td><a href='/approvals/review?proposal_id={_esc(o.id)}'>"
                     "review</a></td></tr>"
                     for o in needs_owner) + "</table>")

    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Approvals</title>"
            f"<style>{_CSS}</style></head><body><main>"
            "<h1>Held approvals</h1>"
            "<p class='muted'>The graph is the source of truth: a pending "
            "approval IS a capability_call at status policy_checking. "
            "Evolution adoptions get a full review page; approve nothing "
            "you have not read.</p>"
            + table + stuck + "</main></body></html>")
