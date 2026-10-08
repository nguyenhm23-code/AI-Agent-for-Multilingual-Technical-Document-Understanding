"""Evaluate reviewed evidence sets and Decimal results, separate from draft single-source QA."""
from decimal import Decimal
from pathlib import Path
import time
from ..backends.artifacts import read_json
from ..pipeline.rag_runner import ask
from ..io import write_json
from .routing_metrics import trace_metrics, summarize_trace_metrics


def score_record(answer, gold):
    retrieved = {e["chunk"]["chunk_id"] for e in answer.get("evidence",[])}
    alternatives = gold.get("evidence_sets",[])
    evidence_complete = any(set(s)<=retrieved for s in alternatives) if alternatives else None
    results = {r["id"]:Decimal(r["value"]) for r in answer.get("trace",{}).get("calculations",[])}
    expected = gold.get("calculations",{})
    calculation_correct = all(k in results and results[k]==Decimal(str(v)) for k,v in expected.items()) if expected else None
    plan = answer.get("trace", {}).get("research_plan", {})
    conditions = answer.get("trace", {}).get("question_conditions", plan.get("question_conditions", []))
    expected_condition = gold.get("expected_condition")
    condition_valid = None
    if conditions or expected_condition:
        from ..bt6.question_conditions import validate_question_conditions
        try:
            verified = validate_question_conditions(gold.get("question", ""), gold.get("clarification"), conditions)
            ids = {row["condition_id"] for row in verified}
            used = {cid for result in answer.get("trace", {}).get("calculations", []) for cid in result.get("condition_ids", [])}
            condition_valid = (not used - ids and not any(c.get("evidence_id") in ids for c in answer.get("citations", [])))
            if expected_condition:
                condition_valid = condition_valid and any(all(row.get(key) == value for key, value in expected_condition.items()) for row in verified)
        except (ValueError, TypeError, KeyError):
            condition_valid = False
    return {"status_correct":answer["status"]==gold["status"],"required_evidence_set_recall":evidence_complete,
        "calculation_correct":calculation_correct, "condition_provenance_valid": condition_valid,
        "citation_pointer_valid":(answer["status"]!="answered" or bool(answer.get("citations"))) and
            all(c["chunk_id"] in retrieved for c in answer.get("citations",[]))}


def evaluate_multidoc(index, qa_path, output, config):
    target = Path(output)
    if target.exists():
        raise ValueError("Use a new evaluation directory")
    gold = read_json(Path(qa_path))
    if gold.get("review_status")!="human_reviewed":
        raise ValueError("Multi-document gold must be human_reviewed; draft labels are not accuracy ground truth")
    if "assumptions" in gold or any("assumptions" in row for row in gold["questions"]):
        raise ValueError("Legacy assumptions fixture is unsupported. Put explicit conditions in each question/clarification.")
    target.mkdir(parents=True)
    records = []
    for row in gold["questions"]:
        tick = time.perf_counter()
        try:
            answer = ask(index=index, question=row["question"], config=config,
                filters=row.get("filters"))
            record = {"id":row["id"],"answer":answer,**score_record(answer,row),
                **trace_metrics(answer, row), "semantic_correctness_review": "required"}
        except Exception as exc:
            record = {"id":row["id"],"error":f"{type(exc).__name__}: {exc}"}
        record["wall_seconds"] = time.perf_counter()-tick
        records.append(record)
        write_json(target / "records.json",records)
    summary = {"questions":len(records),"errors":sum("error" in r for r in records),
        "status_accuracy":sum(r.get("status_correct",False) for r in records)/max(1,len(records)),
        "index": str(Path(index).resolve()), "config": config.to_dict(),
        "orchestration_mode": config.orchestration_mode, "visual_policy": config.visual_policy,
        "timing_regime": "models reloaded each request; OS disk cache may be warm; no steady-state inference claim",
        "limitations":["Coverage is not calibrated confidence","Citation pointer validity does not prove semantic support",
            "Semantic answer and visual observation correctness require source review"],
        **summarize_trace_metrics(records)}
    for metric in ("required_evidence_set_recall","calculation_correct","citation_pointer_valid","condition_provenance_valid"):
        applicable = [r[metric] for r in records if r.get(metric) is not None]
        summary[metric] = {"value":sum(applicable)/len(applicable) if applicable else None,"evaluated":len(applicable)}
    write_json(target / "summary.json",summary)
    return summary
