from activegraph import Graph, Runtime

from packs.activity_normalizer import pack as normalizer_pack
from packs.attention import pack as attention_pack
from packs.subject_profile import pack as subject_profile_pack
from packs.subject_profile.projection import owner_alias_set_fn
from packs.subject_profile.tools import forget_subject_fact_fn, review_subject_fact_fn


def _runtime(*, with_attention=False):
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(normalizer_pack)
    runtime.load_pack(subject_profile_pack)
    if with_attention:
        runtime.load_pack(attention_pack)
    return graph, runtime


def _evidence(graph, *, scope="owner_profile"):
    text = "Yohei builds ActiveGraph."
    digest = __import__("hashlib").sha256(text.encode()).hexdigest()
    item = graph.add_object("acquired_item", {"source_surface_id": "public_presence", "provider_item_id": "1", "dedup_key": "1", "source_ref": "https://example.test", "source_hash": digest, "provider_time": None, "replay_mode": "inline", "replay_payload_ref": text, "replay_payload_hash": digest, "media_type": "text/plain", "importer_id": "test", "importer_version": "1"})
    graph.add_object("acquired_content", {"acquired_item_id": item.id, "normalized_content": text, "normalized_metadata": {"subject_scope": scope}, "source_category": "local_knowledge", "connection_path": "pack", "is_fixture": True})


def _candidate(graph, evidence, value="ActiveGraph", attribute="project"):
    return graph.add_object("profile_candidate", {"candidate_identity": f"c-{attribute}-{value}", "text": f"Yohei builds {value}.", "confidence": .8, "evidence_id": evidence.id, "evidence_identity": evidence.data["evidence_identity"], "revision_id": evidence.data["revision_id"], "extraction_record_id": "run", "extractor_id": "test", "extractor_version": "1", "extraction_config_id": "cfg", "status": "candidate", "invalidation_reason": None, "metadata": {"annotation_id": "annotation-1"}, "attribute": attribute, "value": value})


def test_candidate_requires_explicit_verdict_and_preserves_evidence():
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    candidate = _candidate(graph, evidence)
    runtime.run_until_idle()
    assert not graph.objects(type="subject_fact")
    result = review_subject_fact_fn(graph, candidate.id, "confirm")
    runtime.run_until_idle()
    [fact] = graph.objects(type="subject_fact")
    assert fact.data["evidence_id"] == evidence.id
    assert fact.data["source_surface_id"] == "public_presence"
    assert graph.get_object(result["verdict_id"]).data["status"] == "applied"


def test_connector_content_cannot_become_owner_fact_without_owner_scope():
    graph, runtime = _runtime(); _evidence(graph, scope=None); runtime.run_until_idle()
    candidate = _candidate(graph, graph.objects(type="activity_evidence")[0])
    result = review_subject_fact_fn(graph, candidate.id, "confirm")
    runtime.run_until_idle()
    assert graph.get_object(result["verdict_id"]).data["status"] == "failed"
    assert not graph.objects(type="subject_fact")


def test_owner_alias_set_is_deterministic_and_follows_supersession():
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    for attribute, value in (
        ("email", "Yohei@Legacy-Mail.com"),
        ("handle", "@yoheinakajima"),
        ("url", "https://www.untapped.vc/about"),
        ("project", "ActiveGraph"),  # never an identity alias
    ):
        review_subject_fact_fn(
            graph, _candidate(graph, evidence, value, attribute).id, "confirm"
        )
        runtime.run_until_idle()
    aliases = owner_alias_set_fn(graph, account_refs=["Owner@Example.com"])
    assert aliases["addresses"] == ["owner@example.com", "yohei@legacy-mail.com"]
    assert aliases["handles"] == ["yoheinakajima"]
    assert aliases["domains"] == ["untapped.vc"]
    assert aliases["basis"] == "confirmed_subject_facts"
    assert len(aliases["fact_refs"]) == 3
    # Deterministic and horizon-stable: the same reader state projects the
    # same value, byte for byte.
    assert owner_alias_set_fn(graph, account_refs=["Owner@Example.com"]) == aliases

    email_fact = next(
        fact for fact in graph.objects(type="subject_fact")
        if fact.data["attribute"] == "email" and fact.data["status"] == "promoted"
    )
    forget_subject_fact_fn(graph, email_fact.id); runtime.run_until_idle()
    after = owner_alias_set_fn(graph)
    assert after["addresses"] == []
    assert after["handles"] == ["yoheinakajima"]


def test_confirmed_relationship_fact_seeds_importance_and_provably_no_trust():
    graph, runtime = _runtime(with_attention=True)
    _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    review_subject_fact_fn(
        graph,
        _candidate(graph, evidence, "jane@founderco.com", "relationship").id,
        "confirm",
    )
    runtime.run_until_idle()
    [observation] = graph.objects(type="attention_observation")
    assert observation.data["signal_type"] == "explicit_important"
    assert observation.data["explicit"] is True
    assert observation.data["source"] == "user"
    assert observation.data["subject_kind"] == "person"
    assert observation.data["metadata"]["derivation"] == "confirmed_subject_fact"
    vectors = graph.objects(type="importance_vector")
    assert any(
        vector.data["subject_ref"] == observation.data["subject_ref"]
        and vector.data.get("features", {}).get("explicit")
        for vector in vectors
    )
    # Trust is strictly outcome-only: knowing who someone is never raises how
    # much their content is believed. No trust vector may exist or change.
    assert not graph.objects(type="source_trust_vector")

    # Identity aliases anchor interpretation; they never seed importance.
    review_subject_fact_fn(
        graph, _candidate(graph, evidence, "yohei@old.com", "email").id, "confirm"
    )
    runtime.run_until_idle()
    assert len(graph.objects(type="attention_observation")) == 1
    assert not graph.objects(type="source_trust_vector")


def test_conflict_and_forget_are_append_only_lifecycles():
    # "name" is single-valued by default: a differing confirmed value is a
    # real conflict, surfaced as an open contradiction — never a silent swap.
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    first = _candidate(graph, evidence, "Yohei Nakajima", "name")
    review_subject_fact_fn(graph, first.id, "confirm"); runtime.run_until_idle()
    second = _candidate(graph, evidence, "Yohei N.", "name")
    review_subject_fact_fn(graph, second.id, "confirm"); runtime.run_until_idle()
    assert graph.objects(type="subject_contradiction")
    facts = graph.objects(type="subject_fact")
    current = [fact for fact in facts if fact.data["status"] == "promoted"][0]
    result = forget_subject_fact_fn(graph, current.id)
    tombstone = graph.get_object(result["tombstone_fact_id"])
    assert tombstone.data["status"] == "forgotten"
    assert graph.get_object(current.id).data["status"] == "superseded"


def test_multi_valued_attributes_accumulate_without_contradiction():
    # A second handle (or project, url, company…) is more identity, not a
    # conflict — the alias set is DESIGNED to hold several. Only declared
    # single-valued attributes contradict.
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    for value in ("@yoheinakajima", "@babyagi_"):
        review_subject_fact_fn(
            graph, _candidate(graph, evidence, value, "handle").id, "confirm"
        )
        runtime.run_until_idle()
    promoted = [
        fact for fact in graph.objects(type="subject_fact")
        if fact.data["status"] == "promoted"
    ]
    assert len(promoted) == 2
    assert not graph.objects(type="subject_contradiction")
    aliases = owner_alias_set_fn(graph)
    assert aliases["handles"] == ["babyagi_", "yoheinakajima"]


def test_promotion_is_idempotent_by_value():
    # Re-confirming a value the subject already holds resolves the verdict
    # to the existing fact: richer evidence never mints near-duplicates.
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    first = review_subject_fact_fn(
        graph, _candidate(graph, evidence, "@yoheinakajima", "handle").id, "confirm"
    )
    runtime.run_until_idle()
    # A distinct candidate (different identity) carrying the same value.
    again = _candidate(graph, evidence, "@yoheinakajima", "handle")
    graph.patch_object(again.id, {"candidate_identity": "c-handle-dup"})
    second = review_subject_fact_fn(graph, again.id, "confirm")
    runtime.run_until_idle()
    facts = [
        fact for fact in graph.objects(type="subject_fact")
        if fact.data["status"] == "promoted"
    ]
    assert len(facts) == 1
    first_verdict = graph.get_object(first["verdict_id"]).data
    second_verdict = graph.get_object(second["verdict_id"]).data
    assert first_verdict["status"] == second_verdict["status"] == "applied"
    assert second_verdict["result_fact_id"] == facts[0].id


def test_verdict_metadata_lands_on_the_fact():
    # Hosts annotate promotions (e.g. self-declared seeds) through the
    # verdict; the applied fact carries the annotation with provenance.
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    review_subject_fact_fn(
        graph,
        _candidate(graph, evidence, "Yohei Nakajima", "name").id,
        "confirm",
        decided_by="owner:client",
        metadata={"self_declared": True},
    )
    runtime.run_until_idle()
    [fact] = graph.objects(type="subject_fact")
    assert fact.data["metadata"]["self_declared"] is True
    assert fact.data["metadata"]["decided_by"] == "owner:client"
    assert fact.data["metadata"]["decision"] == "confirm"


def test_self_declaration_supersedes_same_scope_but_never_independent_facts():
    """Editing a self-declared handle replaces the prior declaration for that
    attribute/platform (ADR 0020 applied to identity), while an independently
    sourced fact with the same attribute stays promoted untouched."""
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]

    # An independently sourced handle (no declaration scope) — must survive.
    review_subject_fact_fn(
        graph, _candidate(graph, evidence, "found-by-research", "handle").id,
        "confirm",
    )
    runtime.run_until_idle()

    # The owner's own declared github handle, then their correction of it.
    review_subject_fact_fn(
        graph, _candidate(graph, evidence, "old-handle", "handle").id,
        "confirm", metadata={"declaration_scope": "self:handle:github"},
    )
    runtime.run_until_idle()
    review_subject_fact_fn(
        graph, _candidate(graph, evidence, "new-handle", "handle").id,
        "confirm", metadata={"declaration_scope": "self:handle:github"},
    )
    runtime.run_until_idle()

    facts = {f.data["value"]: f for f in graph.objects(type="subject_fact")}
    assert facts["old-handle"].data["status"] == "superseded"
    assert facts["new-handle"].data["status"] == "promoted"
    assert facts["found-by-research"].data["status"] == "promoted"
    # History and provenance preserved through the supersession link.
    assert facts["new-handle"].data["supersedes_fact_id"] == facts["old-handle"].id
    # No contradiction was opened: a correction is not a conflict.
    assert not graph.objects(type="subject_contradiction")
    # Consumers see only the current declaration.
    aliases = owner_alias_set_fn(graph)
    assert "old-handle" not in aliases["handles"]
    assert set(aliases["handles"]) == {"found-by-research", "new-handle"}


def test_self_declared_name_correction_supersedes_without_contradiction():
    graph, runtime = _runtime(); _evidence(graph); runtime.run_until_idle()
    evidence = graph.objects(type="activity_evidence")[0]
    review_subject_fact_fn(
        graph, _candidate(graph, evidence, "Yohei N.", "name").id,
        "confirm", metadata={"declaration_scope": "self:name"},
    )
    runtime.run_until_idle()
    review_subject_fact_fn(
        graph, _candidate(graph, evidence, "Yohei Nakajima", "name").id,
        "confirm", metadata={"declaration_scope": "self:name"},
    )
    runtime.run_until_idle()
    facts = {f.data["value"]: f for f in graph.objects(type="subject_fact")}
    assert facts["Yohei N."].data["status"] == "superseded"
    assert facts["Yohei Nakajima"].data["status"] == "promoted"
    assert not graph.objects(type="subject_contradiction")
