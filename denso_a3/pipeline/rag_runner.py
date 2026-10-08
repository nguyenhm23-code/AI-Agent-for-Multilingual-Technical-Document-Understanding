"""Local auto dispatcher, immutable scope, pending and one final memory turn."""
import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import replace
from ..backends.artifacts import read_json
from ..backends.generation import Generator
from ..bt4.metadata import verified_values
from ..bt5.store import RetrievalStore, eligible
from ..bt6.planner import plan_query
from ..bt6.planner import fold
from ..bt6.question_analysis import analyze_query
from ..bt6.query_router import route_question
from ..bt7.memory import ConversationStore
from ..contracts import AnswerRecord, SUPPORTED_RAG_SCHEMAS, QueryBudgetExceeded, ContextBudgetExceeded
from ..io import fingerprint, sha256_file
from .query_budget import QueryBudget

PENDING_VERSION = "2.0"


def request_fingerprints(root, filters, config, manifest, registry):
    content = {"manifest": manifest, "registry": registry}
    for filename in ("chunks.json", "documents.json"):
        path = root / filename
        if path.is_file():
            content[filename] = sha256_file(path)
    return {"scope_fingerprint": fingerprint({"index": str(root), "filters": filters or {}}),
            "index_fingerprint": fingerprint(content), "config_fingerprint": fingerprint(config.to_dict())}


def resolve_current_scope(root, plan, registry):
    if "current" not in plan.intents:
        return plan
    with RetrievalStore(root) as store:
        doc_ids = {c["document_id"] for c in store.rows.values() if eligible(c, plan.hard_filters)}
    values = {d: verified_values(registry["documents"].get(d, {}).get("fields", {})) for d in doc_ids}
    if not values or any(not v.get("effective_from") for v in values.values()):
        return replace(plan, clarification="Chưa có metadata hiệu lực đã xác minh." if plan.language=="vi" else "Verified effective dates are missing.")
    today = datetime.now(timezone(timedelta(hours=7))).date().isoformat()
    active = [d for d,v in values.items() if v["effective_from"]<=today and (not v.get("effective_to") or today<=v["effective_to"])]
    return replace(plan, hard_filters={**plan.hard_filters, "document_id": active})


def resolve_pending_choice(original, clarification, registry, strategy, filters, visual_policy):
    """Map a literal choice to known metadata and intersect the user's scope."""
    base = plan_query(original, registry, strategy, filters, visual_policy=visual_policy)
    search = original + "\nUser clarification: " + clarification
    scoped = dict(filters or {})
    if not base.clarification:
        return search, scoped, None
    reply = fold(clarification)
    def contains(value):
        return bool(re.search(r"(?<!\w)"+re.escape(fold(str(value)))+r"(?!\w)",reply))
    if "version" in base.intents:
        revisions = {v["revision_id"] for d in registry["documents"].values()
                     if "revision_id" in (v:=verified_values(d.get("fields", {})))}
        selected = [r for r in revisions if contains(r)]
        if len(selected)==1:
            key,value = "revision_id",selected[0]
            search = re.sub(r"\b(?:revision|rev\.?|phiên bản)\s+[\w.-]+", "revision "+value, search, flags=re.I)
        else:
            return search, scoped, base.clarification
    else:
        selected = [d for did,d in registry["documents"].items()
                    if contains(d.get("source_relative", did)) or contains(did)]
        if len(selected)!=1:
            return search, scoped, base.clarification
        key,value = "source_relative",selected[0].get("source_relative")
        if not value:
            return search, scoped, base.clarification
    previous = scoped.get(key)
    if previous is not None and value not in (previous if isinstance(previous,list) else [previous]):
        return search, scoped, "Lựa chọn nằm ngoài phạm vi đã chọn; hãy chọn nguồn trong phạm vi hiện tại."
    scoped[key] = value
    return search, scoped, None


def ask(index, question, config, strategy=None, filters=None, conversation_id=None, memory_path=None,
        pending=None, progress=None):
    started = time.perf_counter()
    config.validate()
    if config.generation_provider!="local":
        raise ValueError("Question answering requires local generation before memory rewrite")
    root = Path(index).resolve()
    manifest = read_json(root / "manifest.json")
    if manifest.get("state")!="ready" or manifest.get("schema_version") not in SUPPORTED_RAG_SCHEMAS:
        raise ValueError("Index is incomplete or incompatible")
    registry = read_json(root / "registry.json")
    fingerprints = request_fingerprints(root, filters, config, manifest, registry)
    scope = fingerprints["scope_fingerprint"]
    budget = QueryBudget(config)
    generator = Generator(config, manifest["model_manifest"])
    generator.query_budget, generator.deadline = budget, budget.deadline
    memory, attempts, escalations, routing = None, [], [], {}
    timing = {}
    search_question = question
    effective_filters, choice_error = dict(filters or {}), None
    original_question, clarification = question, None
    answer = AnswerRecord(question, "", "abstain", "vi")

    def attempt(route, record):
        return {"route": route, "status": record.status, "evidence_ids": [e.get("evidence_id") for e in record.evidence],
                "chunk_ids": [e["chunk"]["chunk_id"] for e in record.evidence],
                "citations": list(record.citations),
                "trace": dict(record.trace), "timings_ms": dict(record.timings_ms), "usage": dict(record.usage)}

    def research(research_plan=None):
        from .research_runner import run_research
        if not manifest.get("hierarchy_version"):
            record = AnswerRecord(original_question, "Kho chưa có hierarchy; hãy chọn snapshot đã nâng cấp bằng rag-upgrade.", "abstain", plan.language)
            record.trace["stop_reason"] = "capability_missing"
            attempts.append(attempt("hierarchical", record))
            return record
        replace(config, orchestration_mode="hierarchical").validate()
        if research_plan is not None:
            research_plan = {**research_plan, "_clarification": clarification or ""}
        record = run_research(root, original_question, plan, config, manifest, generator,
                              research_plan=research_plan, budget=budget, progress=progress, clarification=clarification)
        attempts.append(attempt("hierarchical", record))
        return record

    def finalize(record):
        from ..bt7.verification import enforce_verification
        enforce_verification(record, search_question, generator, config, budget, progress)
        budget.stop_reason = budget.stop_reason or record.trace.get("stop_reason") or record.status
        record.question = question
        record.trace.update(routing={**routing, "escalations": escalations}, attempts=attempts, budget=budget.to_dict(),
                            memory_mode="long_term" if memory else "instant", index=str(root),
                            model_lifecycle="sequential load per stage; timings include cold model load")
        record.timings_ms.update(timing)
        for stage in ("planning", "retrieval", "visual_phase", "evidence_assessment", "generation"):
            record.timings_ms[stage] = sum(item["timings_ms"].get(stage,0) for item in attempts) if attempts else record.timings_ms.get(stage,0)
        for stage in ("rewrite", "routing"):
            record.timings_ms.setdefault(stage, 0)
        record.timings_ms["total"] = (time.perf_counter()-started)*1000
        if budget.usage:
            record.usage = dict(budget.usage)
        if memory:
            memory.append(conversation_id, scope, question, record.to_dict())
            memory.set_pending(conversation_id, scope, record.trace.get("pending"))
        return record.to_dict()

    try:
        if conversation_id:
            if not memory_path:
                raise ValueError("Long-term conversation requires memory_path")
            memory = ConversationStore(memory_path)
            pending = pending or memory.pending(conversation_id, None)
        if pending:
            if pending.get("schema_version") != PENDING_VERSION or "assumptions" in pending or any(
                    pending.get(k)!=v for k,v in fingerprints.items()):
                answer.status = "clarification"
                answer.answer = "Lượt làm rõ cũ không còn hợp lệ; hãy đặt lại câu hỏi đầy đủ."
                answer.trace["pending_invalidated"] = "legacy_or_scope_content_config_changed"
                return finalize(answer)
            original_question = pending["original_question"]
            previous_clarification = pending.get("user_clarification", "")
            clarification = previous_clarification + ("\n" if previous_clarification else "") + question
            search_question,effective_filters,choice_error = resolve_pending_choice(
                original_question, clarification, registry, strategy or config.retrieval_strategy, filters, config.visual_policy)
        elif memory:
            history = memory.history(conversation_id, scope=scope, limit=6)
            if history:
                tick = time.perf_counter()
                rewritten, _ = generator.generate_json(
                    "Rewrite the last question as a standalone search question. Conversation resolves references only. "
                    "Prior answers are unverified context, never evidence. Do not invent facts. Return JSON {question: string}.",
                    json.dumps({"conversation": [{"question": x["question"][:1500], "answer": x["answer"].get("answer", "")[:1500]} for x in history[-3:]], "question": question}, ensure_ascii=False), max_tokens=200)
                if isinstance(rewritten.get("question"), str) and rewritten["question"].strip():
                    search_question = rewritten["question"][:8000]
                timing["rewrite"] = (time.perf_counter()-tick)*1000
                generator.close()
        plan = plan_query(original_question, registry, strategy or config.retrieval_strategy, effective_filters,
                          search_question, config.visual_policy)
        if choice_error:
            plan = replace(plan, clarification=choice_error)
        plan = resolve_current_scope(root, plan, registry)
        analysis = analyze_query(original_question, plan)
        answer.language = plan.language
        if plan.clarification:
            answer.status, answer.answer = "clarification", plan.clarification
            answer.trace["plan"] = plan.to_dict()
        else:
            tick = time.perf_counter()
            if analysis.ambiguous and config.orchestration_mode=="auto":
                replace(config, orchestration_mode="hierarchical").validate()
            budget.active_stage = "router"
            decision = route_question(analysis, {"hierarchy": bool(manifest.get("hierarchy_version"))}, config,
                                      generator, budget, clarification=clarification)
            budget.active_stage = "research"
            generator.close()
            timing["routing"] = (time.perf_counter()-tick)*1000
            routing = {"initial_route": decision.route, "final_route": decision.route, "method": decision.source,
                       "reason_codes": decision.reason_codes, "needs_visual": decision.needs_visual,
                       "plan_reused": decision.planner_reused, "capability": decision.capability_check}
            if decision.research_plan and decision.research_plan.get("clarification"):
                answer.status, answer.answer = "clarification", decision.research_plan["clarification"]
            elif decision.route=="hierarchical":
                answer = research(decision.research_plan)
            else:
                from .baseline_runner import run_baseline
                answer, assessment = run_baseline(root, original_question, plan, config, manifest, generator, analysis, budget, progress)
                attempts.append(attempt("baseline", answer))
                missing = assessment.requirement_ids
                can_search = bool(missing) and (len(analysis.requirements)>1 or analysis.needs_dependency)
                retrieval_available = budget.retrieval_calls < config.query_max_retrieval_calls
                if config.orchestration_mode=="auto" and assessment.state in {"missing", "requires_research"} and assessment.next_action!="stop" and can_search and budget.config.query_max_escalations and retrieval_available:
                    budget.consume_escalation()
                    escalations.append({"from": "baseline", "to": "hierarchical", "source": "evidence_escalation", "missing_requirements": missing})
                    routing["final_route"] = "hierarchical"
                    generator.close()
                    answer = research(decision.research_plan)
                elif assessment.state!="sufficient" and answer.status=="abstain":
                    answer.trace["stop_reason"] = answer.trace.get("stop_reason") or ("retrieval_calls_budget" if not retrieval_available else "missing_evidence_no_new_direction")
            answer.trace["plan"] = plan.to_dict()
            answer.trace["analysis"] = analysis.to_dict()
        if answer.status=="clarification":
            answer.trace["pending"] = {"schema_version": PENDING_VERSION, "original_question": original_question,
                                       "clarification_question": answer.answer, **fingerprints,
                                       "user_clarification": clarification or "",
                                       "condition_spans": answer.trace.get("question_conditions", []), "route_hint": routing.get("final_route")}
        return finalize(answer)
    except (QueryBudgetExceeded, ContextBudgetExceeded) as exc:
        answer.status = "abstain"
        answer.answer = "Ngân sách truy vấn đã hết; chưa đủ bằng chứng để trả lời." if answer.language=="vi" else "Query budget exhausted; evidence is insufficient."
        answer.trace["stop_reason"] = getattr(exc, "reason", "context_budget")
        return finalize(answer)
    finally:
        generator.close()
        if memory:
            memory.close()
