"""Contract and dispatcher regressions; model judgments below are scripted, not accuracy measurements."""
import json
from copy import deepcopy
from dataclasses import replace
import pytest

from denso_a3.bt7.verification import enforce_verification, prepare_verification, validate_verdicts
from denso_a3.contracts import AnswerRecord, RagConfig, EvidenceAssessment
from denso_a3.pipeline.query_budget import QueryBudget
from denso_a3.pipeline import rag_runner
from denso_a3.bt7.memory import ConversationStore


def record(text="BAT actual revenue is 120 billion [E1]", source="BAT revenue target for 2025 is 120 billion"):
    chunk = {"chunk_id": "a", "kind": "text", "text": source, "document_id": "BAT",
             "page_id": "p1", "page_number": 1, "source_sha256": "sha", "image_sha256": "image"}
    return AnswerRecord("BAT revenue?", text, "answered", "en",
        citations=[{"evidence_id": "E1", "chunk_id": "a"}],
        evidence=[{"chunk": chunk, "score": 1, "scores": {}, "evidence_id": "E1"}])


class Verifier:
    def __init__(self, verdict="supported", question_supported=True, invalid=False):
        self.verdict, self.question_supported, self.invalid = verdict, question_supported, invalid
        self.packets = []
    def count_tokens(self, *args):
        return 100
    def close(self):
        pass
    def generate_json(self, system, prompt, **kwargs):
        assert "independent answer verifier" in system
        packet = json.loads(prompt.split("\nPrevious")[0])
        self.packets.append(packet)
        if self.invalid:
            return {"question_supported": True, "reason": "Invalid", "claims": []}, {}
        sources = {s["source_id"]: s for s in packet["sources"]}
        return {"question_supported": self.question_supported, "reason": "Question checked",
            "claims": [{"claim_id": c["claim_id"], "verdict": self.verdict, "reason": "Source semantics checked",
                "support_refs": [{"source_id": c["allowed_source_ids"][-1],
                                  "quote": sources[c["allowed_source_ids"][-1]]["text"]}]} for c in packet["claims"]]}, {"total_tokens": 12}


@pytest.mark.parametrize("verdict", ["contradicted", "insufficient"])
def test_existing_quote_cannot_save_rejected_semantic_claim(verdict):
    answer, config = record(), RagConfig()
    model, budget = Verifier(verdict), QueryBudget(config)
    enforce_verification(answer, answer.question, model, config, budget)
    assert answer.status == "abstain" and answer.citations == []
    assert "actual revenue" not in answer.answer
    assert answer.verification["state"] == "rejected"
    assert answer.verification["claims"][0]["verdict"] == verdict
    assert budget.verification_calls == 1 and answer.evidence


def test_supported_answer_and_all_lines_are_checked():
    answer = record("BAT revenue target is 120 billion [E1]\nBAT target year is 2025 [E1]")
    config, model = RagConfig(), Verifier()
    enforce_verification(answer, answer.question, model, config, QueryBudget(config))
    assert answer.status == "answered" and len(answer.verification["claims"]) == 2
    assert len(model.packets[0]["claims"]) == 2
    assert answer.usage["total_tokens"] == 12


def test_true_claims_cannot_hide_missing_question_requirement():
    answer, config = record(), RagConfig()
    enforce_verification(answer, "Target and actual revenue?", Verifier(question_supported=False), config, QueryBudget(config))
    assert answer.status == "abstain"
    assert "question_not_fully_supported" in answer.verification["reason_codes"]


@pytest.mark.parametrize("mutation", ["omitted", "duplicate", "foreign_source", "fake_quote", "non_object"])
def test_verifier_ledger_cannot_omit_or_rebind_claims(mutation):
    answer = record()
    claims, sources = prepare_verification(answer)
    value = {"question_supported": True, "reason": "ok", "claims": [{"claim_id": "K1", "verdict": "supported",
        "reason": "ok", "support_refs": [{"source_id": "E1", "quote": sources["E1"]["text"]}]}]}
    if mutation == "omitted": value["claims"] = []
    if mutation == "duplicate": value["claims"] *= 2
    if mutation == "foreign_source": value["claims"][0]["support_refs"][0]["source_id"] = "E2"
    if mutation == "fake_quote": value["claims"][0]["support_refs"][0]["quote"] = "Invented source"
    if mutation == "non_object": value["claims"] = [42]
    with pytest.raises(ValueError): validate_verdicts(value, claims, sources)


def test_invalid_verifier_output_is_bounded_and_fails_closed():
    answer, config, model = record(), RagConfig(), Verifier(invalid=True)
    budget = QueryBudget(config)
    enforce_verification(answer, answer.question, model, config, budget)
    assert answer.status == "abstain" and answer.verification["state"] == "error"
    assert budget.verification_calls == len(model.packets) == 2


@pytest.mark.parametrize("failure", ["deadline", "context", "runtime"])
def test_verification_failures_never_return_answered(failure):
    answer, config, model = record(), RagConfig(), Verifier()
    budget = QueryBudget(config)
    if failure == "deadline": budget.deadline = 0
    if failure == "context": model.count_tokens = lambda *a: 99999
    if failure == "runtime":
        def fail(*a, **kw): raise RuntimeError("Local runtime unavailable")
        model.generate_json = fail
    enforce_verification(answer, answer.question, model, config, budget)
    assert answer.status == "abstain" and answer.verification["state"] == "error"


def test_caption_cannot_be_verification_evidence():
    answer = record()
    answer.evidence[0]["chunk"]["kind"] = "page_visual"
    with pytest.raises(ValueError, match="unvalidated"): prepare_verification(answer)


def test_visual_observation_must_match_retrieved_source():
    from denso_a3.bt7.visual_evidence import PROMPT_VERSION
    answer = record("M1 voltage is 24 V [V1]")
    answer.evidence[0]["chunk"].update(kind="page_visual", source_relative="m1.png")
    answer.citations = [{"evidence_id": "V1"}]
    proof = {"validator_version": PROMPT_VERSION, "chunk_ids": ["a"], "image_sha256": "image",
             "document_id": "BAT", "page_id": "p1", "source_relative": "m1.png"}
    answer.trace["visual_phase"] = {"observations": [{"observation_id": "V1", "state": "observed",
                                      "observed_text": "M1 voltage: 24 V", "provenance": proof}]}
    assert prepare_verification(answer)[1]["V1"]["kind"] == "image_observation"
    proof["document_id"] = "another_document"
    with pytest.raises(ValueError, match="unvalidated"): prepare_verification(answer)


def test_table_context_retains_header_and_cited_row_without_engine_geometry():
    from denso_a3.bt7.verification import table_context
    chunk = {"text": "Row 1: Header\nRow 3: Alpha | GBP100", "table": {"cells": [
        {"row_index": 0, "column_index": 1, "column_header": True, "text_raw": "Latest Estimate", "native_raw": "x" * 50000},
        {"row_index": 1, "column_index": 1, "text_raw": "GBP999"},
        {"row_index": 2, "column_index": 1, "text_raw": "GBP100", "content_status": "observed"}],
        "unknown_grid_positions": [{"row_index": 1}, {"row_index": 2}]}}
    context = table_context(chunk)
    assert [c["text_raw"] for c in context["cells"]] == ["Latest Estimate", "GBP100"]
    assert "native_raw" not in context["cells"][0]
    assert context["unknown_grid_positions"] == [{"row_index": 2}]


def test_uncited_extra_prose_cannot_escape_verification():
    answer = record("Target is 120 billion [E1]\nThe CEO is Alice")
    with pytest.raises(ValueError, match="no cited"): prepare_verification(answer)


@pytest.mark.parametrize("route", ["baseline", "hierarchical"])
def test_dispatcher_verifies_before_single_memory_write(tmp_path, monkeypatch, route):
    (tmp_path / "manifest.json").write_text(json.dumps({"state": "ready", "schema_version": "1.1",
        "hierarchy_version": "1.0", "model_manifest": {}}))
    (tmp_path / "registry.json").write_text('{"documents":{}}')
    model = Verifier("insufficient")
    monkeypatch.setattr(rag_runner, "Generator", lambda *a: model)
    draft = record()
    monkeypatch.setattr("denso_a3.pipeline.baseline_runner.run_baseline", lambda *a: (deepcopy(draft), EvidenceAssessment("sufficient")))
    monkeypatch.setattr("denso_a3.pipeline.research_runner.run_research", lambda *a, **kw: deepcopy(draft))
    memory_path = tmp_path / "memory.sqlite"
    memory = ConversationStore(memory_path)
    cid = memory.create()
    memory.close()
    result = rag_runner.ask(tmp_path, "What is BAT actual revenue?", RagConfig(orchestration_mode=route),
                            conversation_id=cid, memory_path=memory_path)
    assert result["status"] == "abstain" and result["verification"]["state"] == "rejected"
    memory = ConversationStore(memory_path)
    try:
        history = memory.history(cid)
        assert len(history) == 1 and history[0]["answer"]["status"] == "abstain"
        assert "actual revenue is 120" not in history[0]["answer"]["answer"]
    finally:
        memory.close()
    assert result["trace"]["budget"]["verification_calls"] == 1
    assert "verification" in result["timings_ms"]


def test_calculation_verification_replays_and_checks_full_renderer():
    from denso_a3.bt7.calculation import calculate
    from denso_a3.bt7.calculation_render import render_calculations
    from denso_a3.contracts import RetrievedEvidence
    answer = record(source="BAT revenue: GBP120")
    fact = {"task_id": "T1", "value": "120", "raw_value": "GBP120", "dimensions": {"GBP": 1},
            "unit": "quantity", "chunk_id": "a", "quote": "BAT revenue: GBP120"}
    plan = {"tasks": [{"task_id": "T1", "label": "BAT revenue"}], "question_conditions": [],
            "calculations": [{"id": "C1", "label": "Total", "operation": "sum", "inputs": ["T1", "T1"]}]}
    facts = {"T1": fact}
    calculations = calculate(plan["calculations"], facts)
    evidence = [RetrievedEvidence(**e) for e in answer.evidence]
    answer.answer = render_calculations(plan, facts, calculations, evidence, [], "en")
    answer.trace.update(research_plan=plan, facts=facts, calculations=calculations)
    claims, sources = prepare_verification(answer)
    assert len(claims) == 2 and "CALC:C1" in sources
    config = RagConfig()
    enforce_verification(answer, "Calculate double BAT revenue", Verifier(), config, QueryBudget(config))
    assert answer.verification["state"] == "verified"
    answer.answer += "\nGrowth is caused by cost reductions [E1]"
    with pytest.raises(ValueError, match="renderer"): prepare_verification(answer)
