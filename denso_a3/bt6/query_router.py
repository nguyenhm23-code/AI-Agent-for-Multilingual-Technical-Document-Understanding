"""Local rule-first routing. A validated ambiguous-query plan is reused."""
from ..contracts import RouteDecision
from .task_planner import build_research_plan


def route_question(analysis, capabilities, config, generator, budget, question_conditions=None, clarification=None):
    if config.generation_provider != "local":
        raise ValueError("Auto routing requires local generation")
    budget.check()
    plan = None
    source = "rules"
    if config.orchestration_mode != "auto":
        route, source = config.orchestration_mode, "manual_debug"
        reasons = ["forced_debug_mode"]
    elif any((analysis.needs_calculation, analysis.needs_dependency, analysis.needs_comparison)):
        route = "hierarchical"
        reasons = [x for x in analysis.rule_hits if x in {"calculation", "dependency", "comparison"}]
    elif analysis.ambiguous:
        source = "local_llm"
        plan, _ = build_research_plan(analysis.question, analysis.language, generator, config,
                                     question_conditions=question_conditions, budget=budget, clarification=clarification,
                                     search_question=analysis.search_question)
        # Independent attributes of one entity can stay a baseline probe. Genuine
        # task dependencies, comparison and arithmetic require research.
        dependent = any(t.get("depends_on") for t in plan["tasks"])
        entities = {t.get("entity") for t in plan["tasks"] if t.get("entity")}
        route = "hierarchical" if plan.get("calculations") or dependent or len(entities)>1 else "baseline"
        reasons = ["validated_local_plan"]
    else:
        route, reasons = "baseline", list(analysis.rule_hits)
    capability = {"hierarchy": bool(capabilities.get("hierarchy")), "required": route == "hierarchical"}
    return RouteDecision(route, source, reasons, "; ".join(reasons), analysis.needs_visual,
                         capability, plan is not None and route=="hierarchical", plan)
