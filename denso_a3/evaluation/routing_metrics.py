"""Observable routing/cost metrics; these are not semantic answer correctness."""
import numpy as np


def trace_metrics(answer, gold=None):
    trace = answer.get("trace", {})
    decision = trace.get("routing", trace.get("route_decision", {}))
    route = decision.get("final_route") or decision.get("route") or trace.get("selected_route") or trace.get("route")
    initial_route = decision.get("initial_route") or route
    if not route and trace.get("orchestration_mode") in {"baseline", "hierarchical"}:
        route = trace["orchestration_mode"]
    attempts = trace.get("route_attempts", trace.get("attempts", []))
    budget = trace.get("budget", {})
    acceptable = (gold or {}).get("acceptable_routes", [])
    assessment = trace.get("evidence_assessment", trace.get("assessment", {}))
    timings = dict(answer.get("timings_ms", {}))
    if isinstance(attempts, list):
        for i, attempt in enumerate(attempts, 1):
            for stage, elapsed in attempt.get("timings_ms", {}).items():
                timings[f"attempt_{i}_{attempt.get('route', 'unknown')}_{stage}"] = elapsed
    return {"route": route, "initial_route": initial_route, "route_source": decision.get("method", decision.get("source")),
        "routing_agreement": route in acceptable if acceptable and route else None,
        "initial_routing_agreement": initial_route in acceptable if acceptable and initial_route else None,
        "route_attempt_count": len(attempts) if isinstance(attempts, list) else None,
        "escalated": bool(budget.get("escalations", trace.get("escalations", 0))),
        "stop_reason": trace.get("stop_reason") or budget.get("stop_reason"),
        "evidence_assessment_state": assessment.get("state"),
        "missing_requirement_ids": assessment.get("missing_requirement_ids", assessment.get("requirement_ids", [])),
        "budget": budget, "timings_ms": timings,
        "usage": answer.get("usage", {})}


def _percentiles(values):
    return {"p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95))} if values else {}


def summarize_trace_metrics(records):
    successful = [row for row in records if "error" not in row]
    routing = [row["routing_agreement"] for row in successful if row.get("routing_agreement") is not None]
    stage_names = sorted({stage for row in successful for stage in row.get("timings_ms", {})})
    counters = ("retrieval_calls", "planner_calls", "router_calls", "generation_attempts", "visual_calls")
    return {"routing_agreement": {"value": sum(routing) / len(routing) if routing else None,
            "evaluated": len(routing)},
        "escalation_rate": sum(row.get("escalated", False) for row in successful) / len(successful) if successful else None,
        "budget_exits": sum(row.get("stop_reason") in {"deadline", "query_deadline", "stage_deadline", "call_budget", "retrieval_calls_budget", "generation_budget", "visual_budget", "visual_calls_budget", "router_calls_budget", "context_budget"}
            for row in successful),
        "latency_seconds": _percentiles([row["wall_seconds"] for row in successful]),
        "stage_latency_ms": {stage: _percentiles([row["timings_ms"][stage] for row in successful
            if isinstance(row.get("timings_ms", {}).get(stage), (int, float))]) for stage in stage_names},
        "model_and_retrieval_calls": {counter: {"total": sum(row["budget"][counter] for row in successful
            if isinstance(row.get("budget", {}).get(counter), (int, float))),
            "requests_measured": sum(isinstance(row.get("budget", {}).get(counter), (int, float)) for row in successful)}
            for counter in counters},
        "usage_totals": {name: sum(row.get("usage", {}).get(name, 0) for row in successful
            if isinstance(row.get("usage", {}).get(name), (int, float)))
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")}}
