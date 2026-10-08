"""Independent P1–P4 regressions found during implementation review; no model runs."""
import json

import pytest

from denso_a3.bt6.planner import plan_query
from denso_a3.bt6.question_analysis import analyze_query
from denso_a3.bt6.evidence_assessment import assess_evidence
from denso_a3.contracts import AnswerRecord, EvidenceAssessment, RagConfig, RetrievedEvidence
from denso_a3.pipeline import rag_runner


def coverage(source, quote):
    return [{"requirement_id": "R1", "state": "supported",
        "support_refs": [{"source_id": source, "quote": quote}]}]


def test_coverage_entity_identifier_requires_token_boundary():
    question = "M1 voltage?"
    analysis = analyze_query(question, plan_query(question, {"documents": {}}))
    quote = "M10 voltage: 5 V"
    evidence = [RetrievedEvidence({"chunk_id": "a", "kind": "text", "text": quote}, 1, evidence_id="E1")]
    assessment = assess_evidence(analysis, evidence, generated_coverage=coverage("E1", quote))
    assert assessment.state == "missing", "M10 is a different entity from requested M1"
    assert assessment.requirement_ids == ["R1"]


def test_table_coverage_cannot_borrow_attribute_from_another_row():
    question = "M1 weight?"
    analysis = analyze_query(question, plan_query(question, {"documents": {}}))
    quote = "M1 voltage: 5 V"
    chunk = {"chunk_id": "a", "kind": "table", "text": quote + "\nM2 weight: 7 kg",
        "table": {"cells": [{"row_index": 0, "column_index": 0, "text_raw": quote},
            {"row_index": 1, "column_index": 0, "text_raw": "M2 weight: 7 kg"}]}}
    evidence = [RetrievedEvidence(chunk, 1, evidence_id="E1")]
    assessment = assess_evidence(analysis, evidence, generated_coverage=coverage("E1", quote))
    assert assessment.state == "missing", "A weight label in M2's row cannot support M1's voltage-only quote"


def test_broad_table_quote_cannot_pair_entity_with_another_rows_attribute():
    question = "M1 weight?"
    analysis = analyze_query(question, plan_query(question, {"documents": {}}))
    quote = "M1 voltage: 5 V\nM2 weight: 7 kg"
    chunk = {"chunk_id": "a", "kind": "table", "text": quote,
        "table": {"cells": [{"row_index": 0, "column_index": 0, "text_raw": "M1"},
            {"row_index": 0, "column_index": 1, "text_raw": "voltage: 5 V"},
            {"row_index": 1, "column_index": 0, "text_raw": "M2"},
            {"row_index": 1, "column_index": 1, "text_raw": "weight: 7 kg"}]}}
    evidence = [RetrievedEvidence(chunk, 1, evidence_id="E1")]
    assert assess_evidence(analysis, evidence, generated_coverage=coverage("E1", quote)).state == "missing"


def test_direct_named_entity_is_retained_and_wrong_named_source_rejected():
    question = "Orion voltage?"
    analysis = analyze_query(question, plan_query(question, {"documents": {}}))
    assert analysis.requirements[0].entity == "Orion"
    quote = "Vega voltage: 5 V"
    evidence = [RetrievedEvidence({"chunk_id": "a", "kind": "text", "text": quote}, 1, evidence_id="E1")]
    assert assess_evidence(analysis, evidence, generated_coverage=coverage("E1", quote)).state == "missing"


def test_explicit_revision_conflict_needs_clarification_without_overriding_filter():
    registry = {"documents": {rid: {"source_relative": rid + ".pdf", "fields": {
        "revision_id": {"value": rid, "review_status": "source_verified"}}} for rid in ("R1", "R2")}}
    plan = plan_query("M1 voltage revision R2?", registry, filters={"revision_id": "R1"})
    assert plan.hard_filters["revision_id"] == "R1"
    assert plan.clarification, "Requested R2 and explicit R1 scope cannot silently answer from R1"


@pytest.fixture
def minimal_index(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({"state": "ready", "schema_version": "1.1",
        "hierarchy_version": "1.0", "model_manifest": {}}), encoding="utf-8")
    (tmp_path / "registry.json").write_text('{"documents":{}}', encoding="utf-8")
    class Local:
        def close(self):
            pass
    monkeypatch.setattr(rag_runner, "Generator", lambda *args: Local())
    return tmp_path


def test_forced_baseline_ambiguous_wording_keeps_legacy_small_context(minimal_index, monkeypatch):
    called = []
    def baseline(*args):
        called.append(True)
        return AnswerRecord(args[1], "Maintenance is scheduled", "answered", "en"), EvidenceAssessment("sufficient")
    monkeypatch.setattr("denso_a3.pipeline.baseline_runner.run_baseline", baseline)
    # This test isolates routing with a source-free scripted AnswerRecord.
    config = RagConfig(orchestration_mode="baseline", context_tokens=2048, verify_answers=False)
    result = rag_runner.ask(index=minimal_index, question="Why does Orion need maintenance?", config=config)
    assert called and result["status"] == "answered"
    assert result["trace"]["routing"]["method"] == "manual_debug"


def test_exhausted_retrieval_budget_does_not_start_new_research_stage(minimal_index, monkeypatch):
    def baseline(*args):
        args[7].consume_retrieval()
        return AnswerRecord(args[1], "Missing weight", "abstain", "en"), EvidenceAssessment("missing", ["R2"])
    def research(*args, **kwargs):
        pytest.fail("Research/planner must not start after the last retrieval call was spent")
    monkeypatch.setattr("denso_a3.pipeline.baseline_runner.run_baseline", baseline)
    monkeypatch.setattr("denso_a3.pipeline.research_runner.run_research", research)
    result = rag_runner.ask(index=minimal_index, question="M1 voltage and weight?",
        config=RagConfig(query_max_retrieval_calls=1))
    assert result["status"] == "abstain"
    assert result["trace"]["budget"]["retrieval_calls"] == 1
    assert len(result["trace"]["attempts"]) == 1


def test_current_scope_keeps_document_revision_and_page_intersection(monkeypatch):
    def verified(value):
        return {"value": value, "review_status": "source_verified"}
    rows = {"a": {"document_id": "A", "page_number": 1, "metadata": {"revision_id": verified("R2")}},
        "b": {"document_id": "B", "page_number": 1, "metadata": {"revision_id": verified("R2")}},
        "c": {"document_id": "A", "page_number": 2, "metadata": {"revision_id": verified("R1")}}}
    class Store:
        def __enter__(self):
            self.rows = rows
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(rag_runner, "RetrievalStore", lambda _: Store())
    registry = {"documents": {did: {"fields": {"effective_from": verified("2020-01-01"),
        "effective_to": verified("2099-12-31")}} for did in ("A", "B")}}
    filters = {"document_id": "A", "revision_id": "R2", "page_number": 1}
    plan = plan_query("Current M1 voltage?", registry, filters=filters)
    resolved = rag_runner.resolve_current_scope("unused", plan, registry)
    assert resolved.hard_filters == {"document_id": ["A"], "revision_id": "R2", "page_number": 1}
    assert plan.hard_filters == filters
