import json
from dataclasses import replace
import pytest
from denso_a3.contracts import RagConfig, AnswerRecord, EvidenceAssessment, QueryBudgetExceeded
from denso_a3.pipeline.query_budget import QueryBudget
from denso_a3.pipeline import rag_runner
from denso_a3.bt6.planner import plan_query
from denso_a3.bt6.question_analysis import analyze_query
from denso_a3.bt6.evidence_assessment import assess_evidence
from denso_a3.contracts import RetrievedEvidence


@pytest.fixture
def index(tmp_path):
    (tmp_path/"manifest.json").write_text(json.dumps({"state":"ready","schema_version":"1.1","hierarchy_version":"1.0","model_manifest":{}}))
    (tmp_path/"registry.json").write_text(json.dumps({"documents":{}}))
    return tmp_path


def test_escalation_one_budget_one_memory_turn(index, monkeypatch):
    from denso_a3.bt7.memory import ConversationStore
    path=index/"memory.sqlite"
    with_memory=ConversationStore(path)
    cid=with_memory.create(); with_memory.close()
    budgets=[]
    def baseline(root, question, plan, config, manifest, generator, analysis, budget, progress):
        budget.consume_retrieval(); budgets.append(budget)
        record=AnswerRecord(question,"Incomplete", "abstain", "en")
        return record, EvidenceAssessment("missing", ["R2"], next_action="research")
    def research(*args, **kwargs):
        budget=kwargs["budget"]; budgets.append(budget); budget.consume_retrieval()
        return AnswerRecord(args[1],"All inputs missing", "abstain", "en", trace={"stop_reason":"no_progress"})
    monkeypatch.setattr("denso_a3.pipeline.baseline_runner.run_baseline",baseline)
    monkeypatch.setattr("denso_a3.pipeline.research_runner.run_research",research)
    result=rag_runner.ask(index=index, question="Voltage and weight?", config=RagConfig(), conversation_id=cid, memory_path=path)
    assert budgets[0] is budgets[1]
    assert result["trace"]["budget"]["retrieval_calls"]==2 and result["trace"]["budget"]["escalations"]==1
    assert [a["route"] for a in result["trace"]["attempts"]]==["baseline","hierarchical"]
    memory=ConversationStore(path)
    assert len(memory.history(cid))==1
    memory.close()


def test_exhausted_probe_has_no_new_research_budget(index,monkeypatch):
    def baseline(*args):
        budget=args[7]; budget.consume_retrieval()
        return AnswerRecord(args[1],"missing","abstain","en"),EvidenceAssessment("missing",["R2"])
    def research(*args,**kwargs):
        kwargs["budget"].consume_retrieval()
        pytest.fail("Budget reset on escalation")
    monkeypatch.setattr("denso_a3.pipeline.baseline_runner.run_baseline",baseline)
    monkeypatch.setattr("denso_a3.pipeline.research_runner.run_research",research)
    result=rag_runner.ask(index=index, question="Voltage and weight?", config=RagConfig(query_max_retrieval_calls=1))
    assert result["status"]=="abstain" and result["trace"]["budget"]["retrieval_calls"]==1


def test_api_rejected_before_memory_or_generation(index,monkeypatch):
    def forbidden(*args,**kwargs): pytest.fail("API policy checked too late")
    monkeypatch.setattr(rag_runner,"Generator",forbidden)
    monkeypatch.setattr(rag_runner,"ConversationStore",forbidden)
    with pytest.raises(ValueError,match="local_only"):
        rag_runner.ask(index=index, question="Voltage?", config=RagConfig(generation_provider="api"),conversation_id="x",memory_path="x")


def test_legacy_index_research_capability_missing(index):
    manifest=json.loads((index/"manifest.json").read_text()); manifest.pop("hierarchy_version")
    (index/"manifest.json").write_text(json.dumps(manifest))
    result=rag_runner.ask(index=index,question="Calculate total budgets.",config=RagConfig())
    assert result["status"]=="abstain" and result["trace"]["stop_reason"]=="capability_missing"
    assert result["trace"]["budget"]["generation_attempts"]==0


def test_pending_legacy_invalidated_without_losing_history(index):
    result=rag_runner.ask(index=index,question="100%",config=RagConfig(),pending={"original_question":"Old","assumptions":{"transfer":1}})
    assert result["status"]=="clarification" and "pending" not in result["trace"]
    assert result["trace"]["pending_invalidated"]


def test_coverage_pointer_is_insufficient_for_missing_attribute():
    question="M1 voltage and weight?"
    analysis=analyze_query(question,plan_query(question,{"documents":{}}))
    evidence=[RetrievedEvidence({"chunk_id":"a","text":"M1 voltage: 5 V", "kind":"text"},1,evidence_id="E1")]
    coverage=[{"requirement_id":r.requirement_id,"state":"supported","support_refs":[{"source_id":"E1","quote":"M1 voltage: 5 V"}]} for r in analysis.requirements]
    assessment=assess_evidence(analysis,evidence,generated_coverage=coverage)
    assert assessment.state=="missing" and assessment.requirement_ids==["R2"]


def test_monotonic_deadline_and_visual_limits():
    now=[100.]
    budget=QueryBudget(RagConfig(query_deadline_seconds=1,query_max_visual_calls=1),clock=lambda:now[0])
    budget.consume_visual()
    with pytest.raises(QueryBudgetExceeded): budget.consume_visual()
    now[0]=102
    with pytest.raises(QueryBudgetExceeded,match="query_deadline"): budget.consume_retrieval()


def test_support_quote_cannot_borrow_another_entity_attribute():
    question="M1 weight?"
    analysis=analyze_query(question,plan_query(question,{"documents":{}}))
    evidence=[RetrievedEvidence({"chunk_id":"a","text":"M1 voltage5V\nM2 weight7kg","kind":"text"},1,evidence_id="E1")]
    coverage=[{"requirement_id":"R1","state":"supported","support_refs":[{"source_id":"E1","quote":"M2 weight7kg"}]}]
    assert assess_evidence(analysis,evidence,generated_coverage=coverage).state=="missing"


def test_clarification_selects_known_source_without_widening_scope():
    registry={"documents":{"A":{"source_relative":"A.pdf","fields":{}},"B":{"source_relative":"B.pdf","fields":{}}}}
    search,filters,error=rag_runner.resolve_pending_choice("Tên công ty là gì?","A.pdf",registry,"hybrid",{},"auto")
    assert filters=={"source_relative":"A.pdf"} and not error
    _,filters,error=rag_runner.resolve_pending_choice("Tên công ty là gì?","B.pdf",registry,"hybrid",{"source_relative":["A.pdf"]},"auto")
    assert filters=={"source_relative":["A.pdf"]}
