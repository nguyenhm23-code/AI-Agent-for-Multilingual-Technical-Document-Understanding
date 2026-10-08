"""Bounded research orchestration over a shared request budget and source scope."""
import time
from dataclasses import replace
from ..bt5.store import RetrievalStore, eligible
from ..bt5.hierarchy_store import HierarchyStore
from ..bt6.task_planner import build_research_plan, validate_research_plan
from ..bt6.hierarchical_retrieval import retrieve_hierarchical
from ..bt6.evidence_coverage import check_coverage, fact_conflicts
from ..bt6.conflicts import detect_conflicts
from ..bt4.metadata import verified_values
from ..bt7.fact_extraction import extract_fact
from ..bt7.calculation import calculate
from ..bt7.calculation_render import render_calculations
from ..bt7.answering import grounded_answer, citations_for
from ..bt7.visual_evidence import run_visual_phase
from ..contracts import AnswerRecord, ContextBudgetExceeded, QueryBudgetExceeded, RetrievedEvidence
from .query_budget import QueryBudget


def _add_usage(answer, usage):
    for key in ("prompt_tokens","completion_tokens","total_tokens"):
        answer.usage[key] = answer.usage.get(key,0)+usage.get(key,0)


def run_research(root, question, query_plan, config, manifest, generator, research_plan=None,
                 question_conditions=None, budget=None, progress=None, clarification=None):
    started = time.perf_counter()
    budget = budget or QueryBudget(config)
    # Stage ceiling can shorten the deadline, but never renew the query deadline.
    deadline = min(budget.deadline,started+config.research_deadline_seconds,
                   getattr(generator,"deadline",budget.deadline) or budget.deadline)
    generator.deadline, generator.query_budget = deadline,budget
    answer = AnswerRecord(question,"","abstain",query_plan.language)
    trace = {"orchestration_mode":"hierarchical","rounds":[],"stop_reason":"",
             "deadline_seconds":max(0,deadline-started),"planner_reused":research_plan is not None,
             "visual_phases":[]}
    answer.trace = trace
    if progress:
        progress("Đang kiểm tra kế hoạch tìm bằng chứng…" if research_plan is not None else "Đang lập kế hoạch tìm bằng chứng…")
    planning_started = time.perf_counter()
    try:
        budget.check()
        if research_plan is None:
            plan,usage = build_research_plan(question,query_plan.language,generator,config,
                                            question_conditions=question_conditions,budget=budget,clarification=clarification,
                                            search_question=query_plan.search_question)
            _add_usage(answer,usage)
        else:
            plan = validate_research_plan(research_plan,config.research_max_tasks,question=question,
                clarification=clarification if clarification is not None else research_plan.get("_clarification"),
                question_conditions=question_conditions,search_question=query_plan.search_question)
    except (QueryBudgetExceeded,ContextBudgetExceeded) as exc:
        answer.answer = "Đã hết ngân sách truy vấn; chưa đủ bằng chứng." if query_plan.language=="vi" else "Query budget exhausted; evidence is incomplete."
        trace.update(stop_reason=str(exc) if isinstance(exc,QueryBudgetExceeded) else "context_budget",budget=budget.to_dict())
        answer.timings_ms["research_total"] = (time.perf_counter()-started)*1000
        return answer
    finally:
        generator.close()
    answer.timings_ms["planning"] = 0 if research_plan is not None else (time.perf_counter()-planning_started)*1000
    trace["research_plan"] = plan
    trace["question_conditions"] = plan["question_conditions"]
    if plan.get("clarification"):
        answer.status,answer.answer = "clarification",plan["clarification"]
        trace.update(stop_reason="user_choice_required",clarification_question=plan["clarification"],budget=budget.to_dict())
        return answer
    facts,evidence_pool,queries,used_queries = {},{}, {},set()
    calls,conflicts = 0,[]
    with RetrievalStore(root) as store:
        hierarchy = HierarchyStore(store)
        try:
            for round_number in range(1,config.research_max_rounds+1):
                if time.perf_counter()>=deadline:
                    trace["stop_reason"] = "deadline"
                    break
                stage = {"round":round_number,"searches":[],"extractions":[]}
                before = len(facts)
                batches = []
                for task in plan["tasks"]:
                    tid = task["task_id"]
                    if tid in facts or any(dep not in facts for dep in task["depends_on"]):
                        continue
                    if calls>=config.research_max_calls or time.perf_counter()>=deadline:
                        trace["stop_reason"] = "call_budget" if calls>=config.research_max_calls else "deadline"
                        break
                    search = queries.get(tid,task["query"])
                    resolved_task = dict(task)
                    for dep in task["depends_on"]:
                        search = search.replace("{"+dep+"}",facts[dep]["value"])
                        resolved_task["entity"] = resolved_task.get("entity", "").replace("{"+dep+"}",facts[dep]["value"])
                    key = (tid,search)
                    if key in used_queries:
                        continue
                    try:
                        budget.consume_retrieval()
                    except QueryBudgetExceeded as exc:
                        trace["stop_reason"] = str(exc)
                        break
                    used_queries.add(key)
                    if progress:
                        progress(f"Vòng {round_number}: tìm nguồn cho {task['label']}")
                    local_plan = replace(query_plan,search_question=search)
                    tick = time.perf_counter()
                    hits,route = retrieve_hierarchical(store,hierarchy,local_plan,config,manifest)
                    answer.timings_ms["retrieval"] = answer.timings_ms.get("retrieval",0)+(time.perf_counter()-tick)*1000
                    calls += 1
                    for e in hits:
                        evidence_pool[e.chunk["chunk_id"]] = e
                    stage["searches"].append({"task_id":tid,"query":search,"route":route,"chunk_ids":[e.chunk["chunk_id"] for e in hits]})
                    batches.append((resolved_task,hits,local_plan))
                # Retrieval models close before VLM/extraction generation loads.
                for task,hits,local_plan in batches:
                    if time.perf_counter()>=deadline:
                        trace["stop_reason"] = "deadline"
                        break
                    if progress:
                        progress(f"Vòng {round_number}: kiểm tra dữ kiện {task['label']}")
                    try:
                        budget.check()
                        tick = time.perf_counter()
                        requirements = [{"requirement_id":task["task_id"],"label":task["label"],
                            "entity":task["entity"],"attribute":task["query"],"wording":task["query"]}]
                        visual = run_visual_phase(root,local_plan,hits,config,manifest,generator,budget,requirements,task=task)
                        answer.timings_ms["visual_phase"] = answer.timings_ms.get("visual_phase",0)+(time.perf_counter()-tick)*1000
                        trace["visual_phases"].append({"task_id":task["task_id"],**visual.trace})
                        _add_usage(answer,visual.usage)
                        conflicts.extend(visual.conflicts)
                        tick = time.perf_counter()
                        try:
                            fact,missing,usage,error = extract_fact(task,hits,generator,config,visual_result=visual)
                        finally:
                            answer.timings_ms["generation"] = answer.timings_ms.get("generation",0)+(time.perf_counter()-tick)*1000
                    except (QueryBudgetExceeded,ContextBudgetExceeded) as exc:
                        trace["stop_reason"] = str(exc) if isinstance(exc,QueryBudgetExceeded) else "context_budget"
                        break
                    _add_usage(answer,usage)
                    if fact:
                        alternatives = fact.pop("alternatives", [])
                        for alternative in alternatives:
                            collision = fact_conflicts(task,fact,alternative)
                            if collision:
                                conflicts.append(collision)
                        facts[task["task_id"]] = fact
                    else:
                        queries[task["task_id"]] = missing
                    stage["extractions"].append({"task_id":task["task_id"],"found":bool(fact),"validation_error":error})
                generator.close()
                trace["rounds"].append(stage)
                tick = time.perf_counter()
                coverage = check_coverage(plan,facts)
                answer.timings_ms["evidence_assessment"] = answer.timings_ms.get("evidence_assessment",0)+(time.perf_counter()-tick)*1000
                if coverage["ready"]:
                    trace["stop_reason"] = "coverage_complete"
                    break
                if trace["stop_reason"]:
                    break
                if not batches or (len(facts)==before and all((t["task_id"],queries.get(t["task_id"],t["query"])) in used_queries
                    for t in plan["tasks"] if t["task_id"] not in facts)):
                    trace["stop_reason"] = "no_progress"
                    break
            families = {verified_values(store.rows[f["chunk_id"]].get("metadata",{})).get("document_family_id") for f in facts.values()}-{None}
            conflict_pool = [c for c in store.rows.values() if eligible(c,query_plan.hard_filters)
                and verified_values(c.get("metadata",{})).get("document_family_id") in families]
            conflicts += detect_conflicts(conflict_pool)
            for c in conflict_pool:
                if any(c["chunk_id"] in conflict.get("chunk_ids",[]) for conflict in conflicts):
                    evidence_pool.setdefault(c["chunk_id"],RetrievedEvidence(c,0))
        finally:
            hierarchy.close()
    coverage = check_coverage(plan,facts)
    selected = list(dict.fromkeys(f["chunk_id"] for f in facts.values()))
    if not selected:
        selected = list(evidence_pool)[:config.evidence_limit]
    if conflicts:
        selected = list(dict.fromkeys(selected+[cid for c in conflicts for cid in c.get("chunk_ids",[])]))
    evidence = [evidence_pool[cid] for cid in selected if cid in evidence_pool]
    for i,e in enumerate(evidence,1):
        e.evidence_id = f"E{i}"
        image_facts = [f for f in facts.values() if f["chunk_id"]==e.chunk["chunk_id"] and f.get("support_kind")=="image"]
        if image_facts:
            image_fact = image_facts[0]
            e.chunk = {**e.chunk,"visual_facts":image_facts,"support_kind":"image",
                "visual_observation_id":image_fact.get("visual_observation_id"),
                "image_id":image_fact.get("image_id"),"visual_provenance":image_fact.get("visual_provenance",{})}
    answer.evidence = [e.to_dict() for e in evidence]
    answer.conflicts = conflicts
    trace.update(facts=facts,coverage=coverage,retrieval_calls=calls,budget=budget.to_dict())
    if conflicts:
        answer.status = "conflict"
        answer.answer = "Các nguồn có giá trị mâu thuẫn; chưa kết luận." if query_plan.language=="vi" else "Sources disagree; no conclusion."
        trace["stop_reason"] = "conflict"
    elif coverage["ready"]:
        if progress:
            progress("Đã đủ đầu vào theo kế hoạch, đang kiểm tra phép tính/citation…")
        try:
            budget.check()
            calculations = calculate(plan["calculations"],facts,plan["question_conditions"],validated_plan=plan)
        except QueryBudgetExceeded as exc:
            answer.status = "abstain"
            answer.answer = "Đã hết ngân sách truy vấn; chưa tính được kết quả." if query_plan.language=="vi" else "Query budget exhausted before calculation."
            trace["stop_reason"] = str(exc)
            calculations = []
        except (ValueError,KeyError) as exc:
            answer.status = "clarification"
            answer.answer = ("Cần làm rõ đơn vị/đầu vào phép tính: " if query_plan.language=="vi" else "Clarify calculation units/inputs: ")+str(exc)
            trace.update(stop_reason="incompatible_calculation",clarification_question=answer.answer)
            calculations = []
        trace["calculations"] = calculations
        if trace["stop_reason"]=="coverage_complete":
            if calculations:
                answer.answer = render_calculations(plan,facts,calculations,evidence,plan["question_conditions"],query_plan.language)
                answer.status = "answered"
            else:
                synthetic = []
                for e in evidence:
                    quotes = [f.get("support_text") or f["quote"] for f in facts.values() if f["chunk_id"]==e.chunk["chunk_id"]]
                    synthetic.append(replace(e,chunk={**e.chunk,"text":"\n".join(quotes),"kind":"text"}))
                try:
                    budget.check()
                    tick = time.perf_counter()
                    result,_,usage = grounded_answer(question,query_plan.language,synthetic,root,generator,
                        max_context_tokens=config.research_context_tokens,required_ids=[e.evidence_id for e in synthetic])
                    answer.answer,answer.status = result["answer"],result["status"]
                    _add_usage(answer,usage)
                    answer.timings_ms["generation"] = answer.timings_ms.get("generation",0)+(time.perf_counter()-tick)*1000
                except (ContextBudgetExceeded,QueryBudgetExceeded) as exc:
                    answer.status = "abstain"
                    if isinstance(exc,QueryBudgetExceeded):
                        answer.answer = "Đã hết ngân sách truy vấn; chưa tổng hợp được đáp án." if query_plan.language=="vi" else "Query budget exhausted before synthesis."
                        trace["stop_reason"] = str(exc)
                    else:
                        answer.answer = "Bằng chứng cần thiết vượt ngân sách ngữ cảnh; hãy thu hẹp câu hỏi." if query_plan.language=="vi" else "Required evidence exceeds context budget; narrow the question."
                        trace["stop_reason"] = "context_budget"
    else:
        labels = [t["label"] for t in plan["tasks"] if t["task_id"] not in facts]
        answer.answer = ("Chưa đủ bằng chứng cho: " if query_plan.language=="vi" else "Evidence missing for: ")+"; ".join(labels)
        trace["stop_reason"] = trace["stop_reason"] or "round_budget"
    answer.citations = citations_for([e.evidence_id for e in evidence],evidence,root)
    trace["budget"] = budget.to_dict()
    answer.timings_ms["research_total"] = (time.perf_counter()-started)*1000
    return answer
