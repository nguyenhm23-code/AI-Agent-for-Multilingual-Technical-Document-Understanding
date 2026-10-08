"""One baseline probe. Dispatch and memory belong to rag_runner."""
import time
from ..bt5.store import RetrievalStore, eligible
from ..bt4.metadata import verified_values
from ..bt6.retrieval import retrieve
from ..bt6.conflicts import detect_conflicts
from ..bt6.evidence_assessment import assess_evidence
from ..bt7.answering import grounded_answer, citations_for
from ..bt7.visual_evidence import run_visual_phase
from ..contracts import AnswerRecord, EvidenceAssessment, RetrievedEvidence, QueryBudgetExceeded, ContextBudgetExceeded


def run_baseline(root, question, plan, config, manifest, generator, analysis, budget, progress=None):
    answer = AnswerRecord(question, "", "abstain", plan.language)
    tick = time.perf_counter()
    budget.consume_retrieval()
    if progress:
        progress("Đang tìm bằng chứng…")
    with RetrievalStore(root) as store:
        evidence, trace = retrieve(store, plan, config, manifest)
        families = {verified_values(e.chunk.get("metadata", {})).get("document_family_id") for e in evidence} - {None}
        conflict_pool = [c for c in store.rows.values() if eligible(c, plan.hard_filters)
                         and verified_values(c.get("metadata", {})).get("document_family_id") in families]
        conflicts = detect_conflicts(conflict_pool)
        if conflicts:
            ids = list(dict.fromkeys(cid for c in conflicts for cid in c["chunk_ids"]))
            evidence = [RetrievedEvidence(store.rows[cid], 0, evidence_id=f"E{i+1}") for i, cid in enumerate(ids)]
    answer.timings_ms["retrieval"] = (time.perf_counter()-tick)*1000
    answer.trace, answer.conflicts = trace, conflicts
    answer.evidence = [e.to_dict() for e in evidence]
    def stopped(exc, stage):
        answer.timings_ms[stage] = (time.perf_counter()-tick)*1000
        answer.trace.update(stop_reason=getattr(exc, "reason", "context_budget"), failed_stage=stage)
        answer.answer = "Ngân sách truy vấn đã hết; chưa đủ bằng chứng." if plan.language=="vi" else "Query budget exhausted; evidence is insufficient."
        answer.citations = citations_for([e.evidence_id for e in evidence], evidence, root)
        return answer, EvidenceAssessment("missing", [r.requirement_id for r in analysis.requirements], next_action="stop",
                                          reason_codes=[answer.trace["stop_reason"]])
    if conflicts:
        answer.status = "conflict"
        answer.answer = "Các nguồn có giá trị mâu thuẫn; chưa kết luận." if plan.language=="vi" else "Sources contain conflicting values; no conclusion."
        answer.citations = citations_for([e.evidence_id for e in evidence], evidence, root)
        return answer, EvidenceAssessment("conflict", reason_codes=["source_conflict"])
    tick = time.perf_counter()
    try:
        visual = run_visual_phase(root, plan, evidence, config, manifest, generator, budget, analysis.requirements)
    except (QueryBudgetExceeded, ContextBudgetExceeded) as exc:
        return stopped(exc, "visual_phase")
    answer.trace["visual_phase"] = visual.to_dict()
    answer.timings_ms["visual_phase"] = (time.perf_counter()-tick)*1000
    tick = time.perf_counter()
    assessment = assess_evidence(analysis, evidence, visual)
    answer.timings_ms["evidence_assessment"] = (time.perf_counter()-tick)*1000
    answer.trace["assessment_preflight"] = assessment.to_dict()
    if assessment.state == "conflict":
        answer.status, answer.conflicts = "conflict", visual.conflicts
        answer.answer = "Ảnh và văn bản có dữ kiện mâu thuẫn." if plan.language=="vi" else "Image and text evidence conflict."
        answer.citations = citations_for([e.evidence_id for e in evidence], evidence, root)
        return answer, assessment
    if evidence and assessment.next_action == "generate":
        tick = time.perf_counter()
        try:
            result, answer.citations, answer.usage = grounded_answer(
                plan.search_question, plan.language, evidence, root, generator,
                requirements=analysis.requirements, visual_result=visual, budget=budget,
                max_context_tokens=config.context_tokens-config.max_answer_tokens-512)
        except (QueryBudgetExceeded, ContextBudgetExceeded) as exc:
            return stopped(exc, "generation")
        answer.timings_ms["generation"] = (time.perf_counter()-tick)*1000
        tick = time.perf_counter()
        assessment = assess_evidence(analysis, evidence, visual, result.get("coverage", []))
        answer.timings_ms["evidence_assessment"] += (time.perf_counter()-tick)*1000
        if assessment.state=="sufficient":
            answer.answer, answer.status = result["answer"], result["status"]
        if result["status"]=="abstain" and assessment.state=="sufficient":
            assessment = EvidenceAssessment("missing", [r.requirement_id for r in analysis.requirements],
                                            next_action="research", reason_codes=["generation_abstained"])
    if not answer.answer:
        answer.answer = "Chưa đủ bằng chứng cho mọi yêu cầu trong phạm vi đã chọn." if plan.language=="vi" else "Evidence is insufficient for all requirements within the selected scope."
    answer.trace["assessment"] = assessment.to_dict()
    return answer, assessment
